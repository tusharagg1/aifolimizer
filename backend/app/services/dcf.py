"""Deterministic discounted-cash-flow (DCF) intrinsic value.

Anchors a 5-year FCF projection + Gordon terminal value to the SEC EDGAR
free-cash-flow series (authoritative 10-K data), discounts at a CAPM-derived
rate, and returns a per-share fair value with a sensitivity grid.

Gives price targets a quantitative spine instead of a free-hand LLM estimate.
US-listed symbols only (EDGAR has no .TO filings). No API key required.

Assumptions are explicit and conservative; this is one valuation lens, not a
verdict. Equity value approximated as enterprise value (net debt ignored) —
treat the per-share number as an unlevered FCFF anchor, not a precise target.
"""

from __future__ import annotations

import yfinance as yf

from app.security import get_logger
from app.services import fundamentals as funda

_LOG = get_logger("aifolimizer.services.dcf")

_PROJECTION_YEARS = 5
_DEFAULT_RF = 0.045  # ~10Y UST
_EQUITY_RISK_PREMIUM = 0.05
_DEFAULT_TERMINAL_GROWTH = 0.025
_GROWTH_BOUNDS = (-0.05, 0.20)
_DISCOUNT_BOUNDS = (0.07, 0.15)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _fair_value(fcf0: float, growth: float, discount: float, term_g: float, shares: float) -> float:
    """PV of 5y projected FCF + discounted Gordon terminal value, per share."""
    if discount <= term_g or shares <= 0:
        return 0.0
    pv = 0.0
    fcf = fcf0
    for t in range(1, _PROJECTION_YEARS + 1):
        fcf = fcf * (1 + growth)
        pv += fcf / (1 + discount) ** t
    terminal = fcf * (1 + term_g) / (discount - term_g)
    pv += terminal / (1 + discount) ** _PROJECTION_YEARS
    return pv / shares


def dcf_valuation(symbol: str) -> dict:
    """Compute intrinsic per-share value for a US ticker via deterministic DCF."""
    symbol = symbol.upper()
    if "." in symbol:
        return {"error": "non_us_symbol", "note": "SEC EDGAR DCF supports US-listed tickers only."}

    cf = funda.get_sec_cashflow(symbol)
    fcf_series = cf.get("free_cash_flow_annual") or []
    if not fcf_series:
        return {"error": "no_cashflow_data", "symbol": symbol}

    fcf0 = float(fcf_series[-1]["value"])
    if fcf0 <= 0:
        return {
            "error": "negative_fcf",
            "symbol": symbol,
            "latest_fcf": fcf0,
            "note": "Latest free cash flow is negative — DCF is unreliable; use a "
            "revenue-multiple or scenario approach instead.",
        }

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as exc:
        _LOG.warning(f"[dcf] {symbol} info error: {exc}")
        info = {}
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    shares = info.get("sharesOutstanding")
    beta = info.get("beta")
    if not shares or not price:
        return {"error": "missing_price_or_shares", "symbol": symbol}

    fcf_cagr = cf.get("fcf_cagr")
    growth = _clamp(fcf_cagr if fcf_cagr is not None else 0.05, *_GROWTH_BOUNDS)
    beta_c = _clamp(beta if beta else 1.0, 0.5, 2.5)
    discount = _clamp(_DEFAULT_RF + beta_c * _EQUITY_RISK_PREMIUM, *_DISCOUNT_BOUNDS)

    base = _fair_value(fcf0, growth, discount, _DEFAULT_TERMINAL_GROWTH, shares)
    upside = round((base / price - 1) * 100, 1) if price else None

    # Sensitivity: discount rate × terminal growth
    grid = []
    for d in (round(discount - 0.02, 4), discount, round(discount + 0.02, 4)):
        for g in (0.015, 0.025, 0.035):
            fv = _fair_value(fcf0, growth, d, g, shares)
            grid.append(
                {
                    "discount_rate_pct": round(d * 100, 1),
                    "terminal_growth_pct": round(g * 100, 1),
                    "fair_value": round(fv, 2),
                    "upside_pct": round((fv / price - 1) * 100, 1) if price else None,
                }
            )
    fvs = [r["fair_value"] for r in grid if r["fair_value"] > 0]

    return {
        "symbol": symbol,
        "current_price": round(float(price), 2),
        "fair_value_base": round(base, 2),
        "upside_pct": upside,
        "verdict": (
            "undervalued" if upside and upside > 15 else "overvalued" if upside and upside < -15 else "fairly_valued"
        ),
        "fair_value_range": {
            "low": round(min(fvs), 2) if fvs else None,
            "high": round(max(fvs), 2) if fvs else None,
        },
        "inputs": {
            "latest_fcf": round(fcf0, 0),
            "fcf_growth_pct": round(growth * 100, 1),
            "fcf_growth_source": "sec_fcf_cagr" if fcf_cagr is not None else "default_5pct",
            "discount_rate_pct": round(discount * 100, 1),
            "beta": round(beta_c, 2),
            "terminal_growth_pct": round(_DEFAULT_TERMINAL_GROWTH * 100, 1),
            "projection_years": _PROJECTION_YEARS,
            "shares_outstanding": shares,
        },
        "sensitivity": grid,
        "fcf_history": fcf_series,
        "caveats": [
            "Net debt ignored — fair value approximates enterprise value per share.",
            "Discount = CAPM cost of equity (rf 4.5% + beta×5% ERP), clamped 7-15%.",
            "FCF growth clamped to [-5%, 20%]; one lens, not a verdict.",
        ],
    }
