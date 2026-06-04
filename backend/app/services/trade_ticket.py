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

# Conviction → exit-ladder R-multiples (T1, T2, T3 distance above entry, in risk units)
_CONVICTION_LADDER = {
    "HIGH": (2.0, 4.0, 6.0),
    "MED": (1.5, 3.0, 4.5),
    "LOW": (1.0, 2.0, 3.0),
}

# Scale-out fractions per target — sums to 1.0 (full exit across the ladder)
_LADDER_SELL_FRACTIONS = (0.40, 0.35, 0.25)

_LEVEL_LABELS = {
    "sma_20": "20-day SMA",
    "sma_50": "50-day SMA",
    "sma_150": "150-day SMA",
    "sma_200": "200-day SMA",
    "bb_mid": "Bollinger mid",
    "bb_lower": "lower Bollinger band",
    "bb_upper": "upper Bollinger band",
}


def _num(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _levels_below(price: float, tech: dict, keys: tuple[str, ...]) -> list[tuple[str, float]]:
    out = [(k, round(_num(tech.get(k)), 4)) for k in keys if 0 < _num(tech.get(k)) < price]
    out.sort(key=lambda kv: -kv[1])  # nearest support (highest below price) first
    return out


def _levels_above(price: float, tech: dict, keys: tuple[str, ...]) -> list[tuple[str, float]]:
    out = [(k, round(_num(tech.get(k)), 4)) for k in keys if _num(tech.get(k)) > price]
    out.sort(key=lambda kv: kv[1])  # nearest resistance first
    return out


def _atr_abs(price: float, tech: dict) -> float:
    atr = _num(tech.get("atr_14"))
    if atr > 0:
        return atr
    atr_pct = _num(tech.get("atr_pct"))
    if atr_pct > 0:
        return price * atr_pct / 100
    return price * 0.02  # fallback: 2% of price


def _build_entry_zone(price: float, tech: dict) -> dict[str, Any]:
    """Buy zone: 'buy_now' when price sits near support, else 'wait_pullback'."""
    atr = _atr_abs(price, tech)
    supports = _levels_below(price, tech, ("sma_20", "bb_mid", "sma_50", "bb_lower", "sma_150", "sma_200"))
    if supports:
        sup_key, sup = supports[0]
        sup_label = _LEVEL_LABELS.get(sup_key, sup_key)
    else:
        sup = round(price * 0.97, 4)
        sup_label = "3% below current"

    rsi = tech.get("rsi_14")
    rsi = _num(rsi) if rsi is not None else None
    dist_atr = (price - sup) / atr if atr else 99.0
    extended = (rsi is not None and rsi >= 70) or dist_atr > 2.0

    if extended:
        timing = "wait_pullback"
        low = round(sup, 4)
        high = round(min(price, sup + 0.5 * atr), 4)
        note = (
            f"Price stretched {dist_atr:.1f}x ATR above {sup_label}"
            + (f", RSI {rsi:.0f}" if rsi is not None else "")
            + " — wait for pullback into zone before buying."
        )
    else:
        timing = "buy_now"
        low = round(max(sup, price - 0.5 * atr), 4)
        high = round(price, 4)
        note = f"Price near {sup_label} support — acceptable to buy within zone now."

    if low > high:
        low, high = high, low
    return {
        "timing": timing,
        "low": low,
        "high": high,
        "reference": round((low + high) / 2, 4),
        "support_level": round(sup, 4),
        "support_basis": sup_label,
        "note": note,
    }


def _build_exit_ladder(
    ref_entry: float,
    stop_price: float,
    quantity: float,
    conviction: str,
    tech: dict,
    avg_cost: float,
    is_crypto: bool,
) -> list[dict[str, Any]]:
    """Tiered profit-taking: T1/T2/T3 by R-multiple, T1 anchored to resistance when it lands in range."""
    risk = ref_entry - stop_price
    if risk <= 0:
        risk = ref_entry * _CONVICTION_STOP.get(conviction, 0.06)
    mults = _CONVICTION_LADDER.get(conviction, _CONVICTION_LADDER["MED"])
    resist = _levels_above(ref_entry, tech, ("bb_upper", "sma_50", "sma_200"))
    nearest_resist = resist[0] if resist else None

    ladder: list[dict[str, Any]] = []
    remaining = quantity
    last = len(mults) - 1
    for i, (mult, frac) in enumerate(zip(mults, _LADDER_SELL_FRACTIONS)):
        tp = round(ref_entry + mult * risk, 4)
        rationale = f"{mult:g}R"
        if i == 0 and nearest_resist:
            rk, rv = nearest_resist
            if ref_entry < rv <= ref_entry + mults[1] * risk:
                tp = rv
                rationale = f"{_LEVEL_LABELS.get(rk, rk)} (~{(rv - ref_entry) / risk:.1f}R)"

        if i == last:
            shares = round(remaining, 6) if is_crypto else int(remaining)
        elif is_crypto:
            shares = round(min(quantity * frac, remaining), 6)
        else:
            shares = min(max(1, math.floor(quantity * frac)), int(remaining)) if quantity else 0
        remaining = max(0.0, round(remaining - shares, 6))

        tgt: dict[str, Any] = {
            "label": f"T{i + 1}",
            "price": tp,
            "sell_pct": round(frac * 100),
            "shares": shares,
            "gain_pct": round((tp - ref_entry) / ref_entry * 100, 1) if ref_entry else None,
            "rationale": rationale,
        }
        if avg_cost > 0:
            tgt["gain_from_cost_pct"] = round((tp - avg_cost) / avg_cost * 100, 1)
        ladder.append(tgt)
    return ladder


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
    avg_cost: float = 0.0,
    holding_return_pct: float = 0.0,
    position_quantity: float = 0.0,
) -> dict[str, Any]:
    """
    Generate a precise, immediately actionable trade ticket.

    Returns: entry_price, quantity, dollar_amount, stop_loss_price,
    target_price, risk_reward_ratio, max_loss, position_size_pct,
    order_type, limit_price, time_in_force, account_recommendation,
    and a plain-English instruction line. For BUY/ADD also returns
    entry_zone (buy_now vs wait_pullback band) and exit_ladder
    (tiered T1/T2/T3 profit-taking). When the ticker is already held
    (avg_cost > 0) adds a position block with cost-basis context.

    action: BUY | ADD | HOLD | TRIM | SELL | EXIT
      HOLD returns a management plan for a held name: stop + exit_ladder
      (profit-taking from current price) + position block, no entry/sizing.
    conviction: HIGH | MED | LOW
    portfolio_value_cad: total portfolio value (pass from get_profile)
    current_position_value_cad: current market value of this holding
    available_cash_cad: uninvested cash available
    target_pct / stop_pct: override auto-sizing (% from entry price)
    avg_cost: native per-share book cost of an existing holding (0 if not held)
    holding_return_pct: unrealized return % of the existing holding
    position_quantity: held share/unit count (used for HOLD ladder sizing)
    """
    symbol = symbol.upper()
    action = action.upper()
    conviction = conviction.upper()

    cache_key = f"{symbol}:{action}:{conviction}:{round(portfolio_value_cad)}:{account_type}:{round(avg_cost, 2)}"
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

        try:
            tech = technicals_svc.get_technicals([symbol]).get(symbol, {})
        except Exception:
            tech = {}

        # ── Stop-loss ────────────────────────────────────────────────────────
        if stop_pct is not None:
            stop_price = round(price * (1 - stop_pct / 100), 4)
        else:
            # Use SMA20 as natural stop if price is above it
            sma20 = _num(tech.get("sma_20"))
            if 0 < sma20 < price:
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

        risk_reward = round((target_price - price) / (price - stop_price), 2) if price > stop_price else None

        # ── Dollar amount ─────────────────────────────────────────────────────
        if action == "HOLD":
            dollar_amount = 0.0
        elif action in ("SELL", "EXIT"):
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
        if action == "HOLD":
            # No order — size the ladder against the held quantity
            quantity = round(position_quantity, 6) if symbol in _CRYPTO else int(position_quantity)
        elif symbol in _CRYPTO:
            quantity = round(dollar_amount / price, 6) if price else 0.0
        else:
            quantity = max(1, math.floor(dollar_amount / price)) if price else 0
            dollar_amount = round(quantity * price, 2)

        max_loss_cad = round(quantity * (price - stop_price), 2)
        position_size_pct = round(dollar_amount / portfolio_value_cad * 100, 1) if portfolio_value_cad > 0 else 0.0

        # ── Entry zone + exit ladder ──────────────────────────────────────────
        # BUY/ADD → buy zone + ladder from entry. HOLD → ladder from current price.
        entry_zone = None
        exit_ladder = None
        if action in ("BUY", "ADD"):
            entry_zone = _build_entry_zone(price, tech)
            exit_ladder = _build_exit_ladder(
                entry_zone["reference"], stop_price, quantity, conviction, tech, avg_cost, symbol in _CRYPTO
            )
        elif action == "HOLD":
            exit_ladder = _build_exit_ladder(
                price, stop_price, quantity, conviction, tech, avg_cost, symbol in _CRYPTO
            )

        # ── Order type ────────────────────────────────────────────────────────
        if action == "HOLD":
            order_type = "MANAGE"  # no order — profit-taking / stop plan for a held name
            limit_price = None
            unit = "units" if symbol in _CRYPTO else "shares"
            targets = " / ".join(
                f"{t['label']} ${t['price']:,.4f} (sell {t['sell_pct']}%)" for t in (exit_ladder or [])
            )
            ret_clause = f" Currently {holding_return_pct:+.1f}% from cost." if avg_cost > 0 else ""
            order_note = (
                f"HOLD {quantity} {unit}. Stop ${stop_price:,.4f} "
                f"({round((price - stop_price) / price * 100, 1)}% below). "
                f"Take-profit ladder: {targets}.{ret_clause}"
            )
        elif action in ("SELL", "EXIT"):
            order_type = "MARKET"
            limit_price = None
            order_note = (
                f"Market sell {quantity} shares at ~${price:,.2f}. Expected proceeds: ${dollar_amount:,.2f} {currency}."
            )
        else:
            order_type = "LIMIT"
            # Buy slightly below ask to avoid chasing; mid-point is fair
            limit_price = round(price * 0.998, 4)
            unit = "units" if symbol in _CRYPTO else "shares"
            if entry_zone:
                if entry_zone["timing"] == "wait_pullback":
                    entry_clause = (
                        f"WAIT for pullback into ${entry_zone['low']:,.4f}-${entry_zone['high']:,.4f} "
                        f"({entry_zone['support_basis']})"
                    )
                else:
                    entry_clause = (
                        f"Buy zone ${entry_zone['low']:,.4f}-${entry_zone['high']:,.4f}, "
                        f"limit ${limit_price:,.4f}"
                    )
                targets = " / ".join(
                    f"{t['label']} ${t['price']:,.4f} (sell {t['sell_pct']}%)" for t in (exit_ladder or [])
                )
                order_note = (
                    f"{action.title()} {quantity} {unit} ({currency}). {entry_clause}. "
                    f"Stop ${stop_price:,.4f} ({round((price - stop_price) / price * 100, 1)}% risk). "
                    f"Exit ladder: {targets}."
                )
            else:
                order_note = (
                    f"Limit {action.lower()} {quantity} {unit} "
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
            # Entry zone + tiered exits (BUY/ADD only; None otherwise)
            "entry_zone": entry_zone,
            "exit_ladder": exit_ladder,
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

        if avg_cost > 0:
            result["position"] = {
                "avg_cost": round(avg_cost, 4),
                "return_pct": round(holding_return_pct, 1),
                "stop_below_cost": stop_price < avg_cost,
                "unrealized": "profit" if holding_return_pct >= 0 else "loss",
            }

        _ticket_cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        return {"error": str(e), "symbol": symbol}
