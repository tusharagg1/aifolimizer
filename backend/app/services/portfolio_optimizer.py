"""Portfolio optimization using PyPortfolioOpt.

Runs Efficient Frontier (max Sharpe) on current holdings.
Returns optimal weights vs current weights — tells you exactly
how much of each position to add/trim to maximise risk-adjusted return.

No API key required. Uses yfinance price history + analyst targets
as forward-looking return views (Black-Litterman blend when available).
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import yfinance as yf
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.portfolio_optimizer")


_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 3600  # 1h — same as technicals


def _fetch_price_history(symbols: list[str], period: str = "2y") -> pd.DataFrame:
    """Returns adjusted close price DataFrame, columns = symbols."""
    try:
        df = yf.download(symbols, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"]
        else:
            close = df[["Close"]] if "Close" in df.columns else df
        # Drop symbols with >30% missing data
        thresh = int(len(close) * 0.7)
        close = close.dropna(thresh=thresh, axis=1)
        close = close.ffill().dropna()
        return close
    except Exception as e:
        _LOG.warning(f"[optimizer] price history error: {e}")
        return pd.DataFrame()


def optimize(
    positions: list[dict],
    analyst_targets: dict[str, float] | None = None,
    risk_free_rate: float = 0.045,
) -> dict[str, Any]:
    """Run max-Sharpe optimization. Returns optimal weights + change vs current.

    positions: list of dicts with symbol, weight (%), market_value_cad
    analyst_targets: {symbol: target_price} for Black-Litterman views
    risk_free_rate: annual risk-free rate (default: ~current 3-month T-bill)
    """
    from pypfopt import EfficientFrontier, risk_models, expected_returns
    from pypfopt.black_litterman import BlackLittermanModel
    symbols = [p["symbol"] for p in positions]
    current_weights = {p["symbol"]: (p.get("weight") or 0) / 100 for p in positions}

    cache_key = ",".join(sorted(symbols))
    entry = _CACHE.get(cache_key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]

    prices = _fetch_price_history(symbols)
    if prices.empty or len(prices.columns) < 2:
        return {"error": "insufficient_price_history", "symbols_available": list(prices.columns)}

    available = list(prices.columns)
    missing = [s for s in symbols if s not in available]

    try:
        # Expected returns: mean historical return (annualised)
        mu = expected_returns.mean_historical_return(prices)

        # If analyst targets available, blend with Black-Litterman
        if analyst_targets:
            views: dict[str, float] = {}
            latest = prices.iloc[-1]
            for sym, target in analyst_targets.items():
                if sym in available and sym in latest.index:
                    current = float(latest[sym])
                    if current > 0:
                        implied_return = (target - current) / current
                        views[sym] = implied_return
            if views:
                try:
                    S = risk_models.CovarianceShrinkage(prices).ledoit_wolf()
                    bl = BlackLittermanModel(S, pi=mu, absolute_views=views)
                    mu = bl.bl_returns()
                except Exception:
                    pass  # fall back to historical mu

        # Covariance matrix (Ledoit-Wolf shrinkage reduces estimation error)
        S = risk_models.CovarianceShrinkage(prices).ledoit_wolf()

        # Efficient Frontier — maximise Sharpe ratio.
        # Default weight_bounds=(0,1) already enforces non-negative weights.
        # No forced minimum: optimizer is free to allocate 0% (exit a name);
        # the previous w>=0.01 floor consumed ~30% of capital on a 30-name book
        # before optimization could place a single dollar based on signal.
        ef = EfficientFrontier(mu, S)
        ef.add_constraint(lambda w: w <= 0.35)        # max 35% per position
        ef.max_sharpe(risk_free_rate=risk_free_rate)
        optimal_weights = ef.clean_weights()

        perf = ef.portfolio_performance(risk_free_rate=risk_free_rate, verbose=False)
        expected_return_pct = round(perf[0] * 100, 2)
        expected_vol_pct = round(perf[1] * 100, 2)
        sharpe = round(perf[2], 3)

        # Changes vs current
        changes: list[dict] = []
        for sym in available:
            opt_w = round(optimal_weights.get(sym, 0) * 100, 1)
            cur_w = round(current_weights.get(sym, 0) * 100, 1)
            diff = round(opt_w - cur_w, 1)
            if abs(diff) >= 0.5:   # only surface meaningful changes
                if diff > 2:
                    action = "INCREASE"
                elif diff < -2:
                    action = "DECREASE"
                else:
                    action = "TRIM" if diff < 0 else "ADD"
                changes.append({
                    "symbol": sym,
                    "current_weight": cur_w,
                    "optimal_weight": opt_w,
                    "change": diff,
                    "action": action,
                })

        changes.sort(key=lambda x: abs(x["change"]), reverse=True)

        result: dict[str, Any] = {
            "optimal_weights": {k: round(v * 100, 1) for k, v in optimal_weights.items() if v > 0.005},
            "expected_annual_return_pct": expected_return_pct,
            "expected_annual_volatility_pct": expected_vol_pct,
            "sharpe_ratio": sharpe,
            "changes": changes[:10],
            "missing_symbols": missing,
            "method": "black_litterman" if analyst_targets and views else "mean_historical",
            "risk_free_rate_pct": round(risk_free_rate * 100, 2),
        }

    except Exception as e:
        result = {"error": str(e)}

    _CACHE[cache_key] = (result, time.time())
    return result
