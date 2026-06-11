"""Portfolio benchmark comparison service.

Computes portfolio weighted total return vs. XEQT.TO, SPY, QQQ, ^GSPTSE
for multiple periods. No API key required - yfinance only.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import yfinance as yf

_BENCHMARKS = {
    "XEQT.TO": "XEQT (Global Equity ETF)",
    "SPY": "SPY (S&P 500)",
    "QQQ": "QQQ (Nasdaq-100)",
    "^GSPTSE": "TSX Composite",
    "VFV.TO": "VFV (S&P 500 CAD)",
}

_PERIODS = {
    "1mo": "1 Month",
    "3mo": "3 Months",
    "6mo": "6 Months",
    "1y": "1 Year",
    "3y": "3 Years",
}

_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 3600  # 1h


def _total_return(prices: pd.Series) -> float | None:
    """Total return % from first to last price in series."""
    prices = prices.dropna()
    if len(prices) < 2:
        return None
    first = float(prices.iloc[0])
    last = float(prices.iloc[-1])
    if first == 0:
        return None
    return round((last - first) / first * 100, 2)


def _fetch_prices(symbols: list[str], period: str) -> dict[str, pd.Series]:
    if not symbols:
        return {}
    try:
        df = yf.download(symbols, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"]
        else:
            close = df[["Close"]] if "Close" in df.columns else df
        result = {}
        for sym in symbols:
            try:
                if sym in close.columns:
                    result[sym] = close[sym].dropna()
                elif len(symbols) == 1:
                    result[sym] = close.squeeze().dropna()
            except Exception:
                logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
        return result
    except Exception:
        return {}


def compare_to_benchmarks(
    positions: list[dict],
) -> dict[str, Any]:
    """Return portfolio vs benchmark total returns across multiple periods.

    positions: list of dicts with keys: symbol, weight (% of portfolio), currency.
    """
    cache_key = ",".join(sorted(p["symbol"] for p in positions))
    entry = _CACHE.get(cache_key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]

    portfolio_symbols = [p["symbol"] for p in positions]
    weight_map = {p["symbol"]: (p.get("weight") or 0) / 100 for p in positions}

    # Normalise weights to sum to 1 (exclude cash)
    total_w = sum(weight_map.values())
    if total_w > 0:
        weight_map = {s: w / total_w for s, w in weight_map.items()}

    all_symbols = portfolio_symbols + list(_BENCHMARKS.keys())
    results: dict[str, Any] = {"periods": {}, "benchmarks_meta": _BENCHMARKS}

    for period, label in _PERIODS.items():
        prices = _fetch_prices(all_symbols, period)

        # Portfolio weighted return
        port_return: float | None = None
        portfolio_detail: dict[str, float | None] = {}
        weighted_sum = 0.0
        weight_covered = 0.0
        for sym in portfolio_symbols:
            w = weight_map.get(sym, 0)
            if sym in prices:
                r = _total_return(prices[sym])
                portfolio_detail[sym] = r
                if r is not None:
                    weighted_sum += r * w
                    weight_covered += w
        if weight_covered > 0.2:  # need at least 20% coverage
            port_return = round(weighted_sum / weight_covered, 2)

        # Benchmark returns
        benchmark_returns: dict[str, float | None] = {}
        for bsym in _BENCHMARKS:
            if bsym in prices:
                benchmark_returns[bsym] = _total_return(prices[bsym])
            else:
                benchmark_returns[bsym] = None

        # Alpha vs each benchmark
        alphas: dict[str, float | None] = {}
        if port_return is not None:
            for bsym, br in benchmark_returns.items():
                alphas[bsym] = round(port_return - br, 2) if br is not None else None

        results["periods"][period] = {
            "label": label,
            "portfolio_return": port_return,
            "benchmarks": benchmark_returns,
            "alpha": alphas,
            "portfolio_detail": portfolio_detail,
        }

    _CACHE[cache_key] = (results, time.time())
    return results
