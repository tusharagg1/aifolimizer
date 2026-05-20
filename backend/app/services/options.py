"""Options chain analytics: Greeks, covered call screener, protective puts.

Black-Scholes implemented in pure Python (no external deps).
Data via yfinance options chain (free, no API key).
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

import yfinance as yf

_CHAIN_TTL = 900       # 15 min — option prices move but not tick-by-tick
_SCREEN_TTL = 1800     # 30 min — screener results

_chain_cache: dict[str, tuple[float, dict]] = {}
_screen_cache: dict[str, tuple[float, dict]] = {}

_RISK_FREE_RATE = 0.045  # approx 3-month T-bill; update when rates change materially


# ── Black-Scholes ────────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = _RISK_FREE_RATE,
    option_type: str = "call",
) -> dict[str, float]:
    """
    S: spot price, K: strike, T: years to expiry,
    sigma: implied vol (annual), r: risk-free rate.
    Returns price + Delta, Gamma, Vega (per 1% IV), Theta (per day), Rho.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {}
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (
        sigma * math.sqrt(T)
    )
    d2 = d1 - sigma * math.sqrt(T)
    pdf_d1 = _npdf(d1)
    disc = math.exp(-r * T)

    if option_type == "call":
        price = S * _ncdf(d1) - K * disc * _ncdf(d2)
        delta = _ncdf(d1)
        rho = K * T * disc * _ncdf(d2) / 100
    else:
        price = K * disc * _ncdf(-d2) - S * _ncdf(-d1)
        delta = _ncdf(d1) - 1.0
        rho = -K * T * disc * _ncdf(-d2) / 100

    gamma = pdf_d1 / (S * sigma * math.sqrt(T))
    vega = S * pdf_d1 * math.sqrt(T) / 100   # per 1% change in IV
    theta = (
        -(S * pdf_d1 * sigma) / (2 * math.sqrt(T))
        - r * K * disc * (
            _ncdf(d2) if option_type == "call" else _ncdf(-d2)
        )
    ) / 365

    return {
        "bs_price": round(price, 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "vega": round(vega, 4),
        "theta": round(theta, 4),
        "rho": round(rho, 4),
    }


# ── Options chain ────────────────────────────────────────────────────────────

def _enrich_row(
    row: Any,
    opt_type: str,
    spot: float,
    T: float,
) -> dict:
    strike = float(row.get("strike", 0))
    iv = float(row.get("impliedVolatility") or 0)
    last = float(row.get("lastPrice") or 0)
    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    volume = int(row.get("volume") or 0)
    oi = int(row.get("openInterest") or 0)

    itm = (spot > strike) if opt_type == "call" else (spot < strike)
    greeks = (
        black_scholes_greeks(spot, strike, T, iv, option_type=opt_type)
        if iv > 0 and spot > 0 else {}
    )

    return {
        "strike": strike,
        "last": last,
        "bid": bid,
        "ask": ask,
        "iv_pct": round(iv * 100, 1),
        "volume": volume,
        "open_interest": oi,
        "in_the_money": itm,
        **greeks,
    }


def get_options_chain(
    symbol: str,
    expiry: str | None = None,
) -> dict[str, Any]:
    """
    Options chain with Black-Scholes Greeks for all strikes.

    expiry: "YYYY-MM-DD" or None (nearest). Returns calls + puts,
    available expiries, spot price, and per-leg Greeks.
    Cached 15 min.
    """
    symbol = symbol.upper()
    cache_key = f"{symbol}:{expiry or 'nearest'}"
    entry = _chain_cache.get(cache_key)
    if entry and time.time() - entry[0] < _CHAIN_TTL:
        return entry[1]

    try:
        ticker = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return {"error": "no_options_listed", "symbol": symbol}

        if expiry and expiry in expiries:
            chosen = expiry
        elif expiry:
            # snap to nearest available
            chosen = min(
                expiries,
                key=lambda e: abs(
                    (
                        datetime.strptime(e, "%Y-%m-%d")
                        - datetime.strptime(expiry, "%Y-%m-%d")
                    ).days
                ),
            )
        else:
            chosen = expiries[0]

        chain = ticker.option_chain(chosen)
        info = ticker.info or {}
        spot = float(
            info.get("regularMarketPrice")
            or info.get("previousClose")
            or 0
        )

        today = datetime.now()
        exp_dt = datetime.strptime(chosen, "%Y-%m-%d")
        T = max((exp_dt - today).days / 365.0, 1 / 365)

        calls = [
            _enrich_row(row, "call", spot, T)
            for _, row in chain.calls.iterrows()
        ]
        puts = [
            _enrich_row(row, "put", spot, T)
            for _, row in chain.puts.iterrows()
        ]

        result: dict[str, Any] = {
            "symbol": symbol,
            "spot": spot,
            "expiry": chosen,
            "days_to_expiry": (exp_dt - today).days,
            "available_expiries": list(expiries[:8]),
            "calls": sorted(calls, key=lambda x: x["strike"]),
            "puts": sorted(puts, key=lambda x: x["strike"]),
        }
        _chain_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── Covered call screener ────────────────────────────────────────────────────

def screen_covered_calls(
    symbol: str,
    min_annual_yield_pct: float = 10.0,
    max_delta: float = 0.40,
) -> dict[str, Any]:
    """
    Find OTM covered call strikes that meet yield + delta criteria.

    Screens next 4 expiries. Returns ranked candidates with:
    annual yield %, delta, breakeven, probability of keeping shares,
    and max profit scenario.
    Cached 30 min.
    """
    symbol = symbol.upper()
    cache_key = f"cc:{symbol}:{min_annual_yield_pct}:{max_delta}"
    entry = _screen_cache.get(cache_key)
    if entry and time.time() - entry[0] < _SCREEN_TTL:
        return entry[1]

    try:
        ticker = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return {"error": "no_options_listed", "symbol": symbol}

        info = ticker.info or {}
        spot = float(
            info.get("regularMarketPrice")
            or info.get("previousClose")
            or 0
        )
        if not spot:
            return {"error": "no_spot_price", "symbol": symbol}

        today = datetime.now()
        candidates: list[dict] = []

        for exp in expiries[:4]:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d")
            days = max((exp_dt - today).days, 1)
            T = days / 365.0

            try:
                chain = ticker.option_chain(exp)
            except Exception:
                continue
            if chain.calls.empty:
                continue

            for _, row in chain.calls.iterrows():
                strike = float(row.get("strike") or 0)
                iv = float(row.get("impliedVolatility") or 0)
                bid = float(row.get("bid") or 0)
                oi = int(row.get("openInterest") or 0)

                if strike <= spot or bid <= 0:
                    continue  # OTM only

                greeks = (
                    black_scholes_greeks(spot, strike, T, iv, option_type="call")
                    if iv > 0 else {}
                )
                delta = abs(greeks.get("delta", 1.0))
                if delta > max_delta:
                    continue

                annual_yield = (bid / spot) * (365 / days) * 100
                if annual_yield < min_annual_yield_pct:
                    continue

                candidates.append({
                    "strike": strike,
                    "expiry": exp,
                    "days_to_expiry": days,
                    "bid_premium": bid,
                    "delta": round(delta, 3),
                    "iv_pct": round(iv * 100, 1),
                    "open_interest": oi,
                    "annual_yield_pct": round(annual_yield, 1),
                    "upside_to_strike_pct": round(
                        (strike - spot) / spot * 100, 1
                    ),
                    "breakeven": round(spot - bid, 2),
                    "prob_keep_shares_pct": round((1 - delta) * 100, 1),
                    "max_profit_per_contract": round(
                        (strike - spot + bid) * 100, 2
                    ),
                })

        candidates.sort(key=lambda x: x["annual_yield_pct"], reverse=True)
        result: dict[str, Any] = {
            "symbol": symbol,
            "spot": spot,
            "strategy": "covered_call",
            "criteria": {
                "min_annual_yield_pct": min_annual_yield_pct,
                "max_delta": max_delta,
            },
            "candidates": candidates[:10],
            "note": (
                "Use bid as floor; set limit at mid (bid+ask)/2 for better fill. "
                "Each contract = 100 shares. "
                "prob_keep_shares_pct = probability call expires worthless."
            ),
        }
        _screen_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── Protective put screener ──────────────────────────────────────────────────

def screen_protective_puts(
    symbol: str,
    max_annual_cost_pct: float = 5.0,
    min_protection_pct: float = 5.0,
) -> dict[str, Any]:
    """
    Find ITM/ATM protective put strikes for downside hedging.

    Screens next 4 expiries. Returns candidates with annualised cost,
    protection floor, and break-even level.
    Cached 30 min.
    """
    symbol = symbol.upper()
    cache_key = f"pp:{symbol}:{max_annual_cost_pct}:{min_protection_pct}"
    entry = _screen_cache.get(cache_key)
    if entry and time.time() - entry[0] < _SCREEN_TTL:
        return entry[1]

    try:
        ticker = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return {"error": "no_options_listed", "symbol": symbol}

        info = ticker.info or {}
        spot = float(
            info.get("regularMarketPrice")
            or info.get("previousClose")
            or 0
        )
        if not spot:
            return {"error": "no_spot_price", "symbol": symbol}

        today = datetime.now()
        candidates: list[dict] = []

        for exp in expiries[:4]:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d")
            days = max((exp_dt - today).days, 1)
            T = days / 365.0

            try:
                chain = ticker.option_chain(exp)
            except Exception:
                continue
            if chain.puts.empty:
                continue

            for _, row in chain.puts.iterrows():
                strike = float(row.get("strike") or 0)
                iv = float(row.get("impliedVolatility") or 0)
                ask = float(row.get("ask") or 0)
                oi = int(row.get("openInterest") or 0)

                if strike >= spot or ask <= 0:
                    continue  # OTM puts only (hedging below current price)

                protection_pct = (spot - strike) / spot * 100
                if protection_pct < min_protection_pct:
                    continue

                annual_cost = (ask / spot) * (365 / days) * 100
                if annual_cost > max_annual_cost_pct:
                    continue

                greeks = (
                    black_scholes_greeks(spot, strike, T, iv, option_type="put")
                    if iv > 0 else {}
                )

                candidates.append({
                    "strike": strike,
                    "expiry": exp,
                    "days_to_expiry": days,
                    "ask_premium": ask,
                    "delta": round(abs(greeks.get("delta", 0)), 3),
                    "iv_pct": round(iv * 100, 1),
                    "open_interest": oi,
                    "annual_cost_pct": round(annual_cost, 1),
                    "protection_floor_pct": round(protection_pct, 1),
                    "breakeven": round(spot - ask, 2),
                    "cost_per_contract": round(ask * 100, 2),
                })

        candidates.sort(key=lambda x: x["annual_cost_pct"])
        result: dict[str, Any] = {
            "symbol": symbol,
            "spot": spot,
            "strategy": "protective_put",
            "criteria": {
                "max_annual_cost_pct": max_annual_cost_pct,
                "min_protection_pct": min_protection_pct,
            },
            "candidates": candidates[:10],
            "note": (
                "Ask price used; set limit at mid for better fill. "
                "Each contract = 100 shares."
            ),
        }
        _screen_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}
