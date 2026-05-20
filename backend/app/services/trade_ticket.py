"""Trade ticket generator: convert analysis signals into precise, actionable orders.

Output is broker-agnostic — all fields map directly to any broker's order form.
Current workflow targets Wealthsimple UI. When adding a second broker,
implement a format_for_broker(ticket, broker="wealthsimple") adapter here.
"""
from __future__ import annotations

import math
import time
from typing import Any

from app.services import data_router
from app.services import technicals as technicals_svc

_TICKET_TTL = 60  # 1 min — prices change; tickets must be fresh

_ticket_cache: dict[str, tuple[float, dict]] = {}

# Conviction → default position size (% of portfolio value)
_CONVICTION_SIZE = {"HIGH": 0.07, "MED": 0.05, "LOW": 0.03}

# Conviction → default stop-loss distance below entry
_CONVICTION_STOP = {"HIGH": 0.08, "MED": 0.06, "LOW": 0.04}

# Conviction → R:R multiplier for default target
_CONVICTION_RR = {"HIGH": 3.0, "MED": 2.5, "LOW": 2.0}

# Trim conviction → fraction of current position to sell
_TRIM_FRACTION = {"HIGH": 0.50, "MED": 0.33, "LOW": 0.20}

# Crypto symbols — allow fractional quantities
_CRYPTO = {"BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "LINK", "DOGE", "XRP"}


def generate_trade_ticket(
    symbol: str,
    action: str,
    portfolio_value_cad: float,
    current_position_value_cad: float = 0.0,
    available_cash_cad: float = 0.0,
    conviction: str = "MED",
    account_type: str = "",
    target_pct: float | None = None,
    stop_pct: float | None = None,
) -> dict[str, Any]:
    """
    Generate a precise, immediately actionable trade ticket.

    Returns: entry_price, quantity, dollar_amount, stop_loss_price,
    target_price, risk_reward_ratio, max_loss, position_size_pct,
    order_type, limit_price, time_in_force, account_recommendation,
    and a plain-English instruction line.

    action: BUY | SELL | ADD | TRIM | EXIT
    conviction: HIGH | MED | LOW
    portfolio_value_cad: total portfolio value (pass from get_profile)
    current_position_value_cad: current market value of this holding
    available_cash_cad: uninvested cash available
    target_pct / stop_pct: override auto-sizing (% from entry price)
    """
    symbol = symbol.upper()
    action = action.upper()
    conviction = conviction.upper()

    cache_key = (
        f"{symbol}:{action}:{conviction}:"
        f"{round(portfolio_value_cad)}:{account_type}"
    )
    entry = _ticket_cache.get(cache_key)
    if entry and time.time() - entry[0] < _TICKET_TTL:
        return entry[1]

    try:
        quote = data_router.get_quote(symbol, max_age_s=60.0)
        price = float(quote.get("price") or quote.get("prev_close") or 0)
        if not price:
            return {"error": "no_price_available", "symbol": symbol}

        currency = str(quote.get("currency", "USD"))
        source = str(quote.get("source", "yfinance"))

        # ── Stop-loss ────────────────────────────────────────────────────────
        if stop_pct is not None:
            stop_price = round(price * (1 - stop_pct / 100), 4)
        else:
            # Use SMA20 as natural stop if price is above it
            try:
                tech = technicals_svc.get_technicals([symbol]).get(symbol, {})
                sma20 = tech.get("sma_20")
            except Exception:
                sma20 = None

            if sma20 and 0 < sma20 < price:
                stop_price = round(sma20 * 0.99, 4)
            else:
                default_stop = _CONVICTION_STOP.get(conviction, 0.06)
                stop_price = round(price * (1 - default_stop), 4)

        # ── Target price ─────────────────────────────────────────────────────
        risk_pct = (price - stop_price) / price
        if target_pct is not None:
            target_price = round(price * (1 + target_pct / 100), 4)
        else:
            rr_mult = _CONVICTION_RR.get(conviction, 2.5)
            target_price = round(price * (1 + risk_pct * rr_mult), 4)

        risk_reward = (
            round((target_price - price) / (price - stop_price), 2)
            if price > stop_price else None
        )

        # ── Dollar amount ─────────────────────────────────────────────────────
        if action in ("SELL", "EXIT"):
            dollar_amount = round(current_position_value_cad, 2)
        elif action == "TRIM":
            frac = _TRIM_FRACTION.get(conviction, 0.33)
            dollar_amount = round(current_position_value_cad * frac, 2)
        else:
            # BUY / ADD
            target_size = _CONVICTION_SIZE.get(conviction, 0.05)
            dollar_amount = round(portfolio_value_cad * target_size, 2)
            if available_cash_cad > 0:
                # Keep 10% cash buffer; never deploy more than available
                dollar_amount = min(dollar_amount, available_cash_cad * 0.90)

        dollar_amount = max(dollar_amount, 0.0)

        # ── Quantity ──────────────────────────────────────────────────────────
        if symbol in _CRYPTO:
            quantity = round(dollar_amount / price, 6) if price else 0.0
        else:
            quantity = max(1, math.floor(dollar_amount / price)) if price else 0
            dollar_amount = round(quantity * price, 2)

        max_loss_cad = round(quantity * (price - stop_price), 2)
        position_size_pct = (
            round(dollar_amount / portfolio_value_cad * 100, 1)
            if portfolio_value_cad > 0 else 0.0
        )

        # ── Order type ────────────────────────────────────────────────────────
        if action in ("SELL", "EXIT"):
            order_type = "MARKET"
            limit_price = None
            order_note = (
                f"Market sell {quantity} shares at ~${price:,.2f}. "
                f"Expected proceeds: ${dollar_amount:,.2f} {currency}."
            )
        else:
            order_type = "LIMIT"
            # Buy slightly below ask to avoid chasing; mid-point is fair
            limit_price = round(price * 0.998, 4)
            order_note = (
                f"Limit {action.lower()} {quantity} "
                f"{'units' if symbol in _CRYPTO else 'shares'} "
                f"@ ${limit_price:,.4f} ({currency}). "
                f"Stop: ${stop_price:,.4f} "
                f"({round((price - stop_price) / price * 100, 1)}% risk). "
                f"Target: ${target_price:,.4f}. "
                f"R/R: {risk_reward}:1."
            )

        # ── Account recommendation ────────────────────────────────────────────
        if account_type:
            account_rec = account_type
        elif action in ("BUY", "ADD"):
            account_rec = (
                "TFSA (tax-free growth preferred). "
                "Use RRSP if TFSA room exhausted. "
                "Non-Reg only as last resort (capital gains taxable)."
            )
        else:
            account_rec = "Match the account where position is held."

        result: dict[str, Any] = {
            "symbol": symbol,
            "action": action,
            "conviction": conviction,
            # Execution
            "order_type": order_type,
            "entry_price": price,
            "limit_price": limit_price,
            "quantity": quantity,
            "dollar_amount_cad": dollar_amount,
            "currency": currency,
            # Risk management
            "stop_loss_price": stop_price,
            "target_price": target_price,
            "risk_reward_ratio": risk_reward,
            "max_loss_cad": max_loss_cad,
            # Sizing
            "position_size_pct": position_size_pct,
            # Logistics
            "time_in_force": "DAY",
            "account_recommendation": account_rec,
            # Plain-English instruction
            "instruction": order_note,
            # Provenance
            "price_source": source,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        _ticket_cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        return {"error": str(e), "symbol": symbol}
