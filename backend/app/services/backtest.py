"""Per-position backtesting against simple rule-based strategies.

Strategies:
- buy_hold       baseline; buy at start, sell at end
- rsi_swing      buy when RSI(14) < 30, sell when RSI > 70
- sma_cross      buy when close > SMA50, sell when close < SMA50

Metrics (per symbol per strategy):
- total_return_pct, cagr_pct, sharpe (rf=0), max_drawdown_pct, num_trades

Output also shows delta_vs_buy_hold so user sees if active rules added value.
Cached 1h per (symbol, strategy, lookback_days).
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pandas as pd
import ta
import yfinance as yf

_CACHE: dict[tuple, tuple[dict, float]] = {}
_CACHE_TTL = 3600

_STRATEGIES = ("buy_hold", "rsi_swing", "sma_cross")


def _annualize_factor(periods_per_year: int = 252) -> float:
    return math.sqrt(periods_per_year)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min() * 100)


def _sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * _annualize_factor())


def _cagr(start: float, end: float, days: int) -> float:
    if start <= 0 or days <= 0:
        return 0.0
    years = days / 365.25
    try:
        return float(((end / start) ** (1 / years) - 1) * 100)
    except Exception:
        return 0.0


def _run_buy_hold(close: pd.Series) -> dict:
    if close.empty:
        return _empty_result()
    start, end = float(close.iloc[0]), float(close.iloc[-1])
    daily_ret = close.pct_change().dropna()
    equity = (1 + daily_ret).cumprod()
    days = (close.index[-1] - close.index[0]).days
    return {
        "total_return_pct": round((end / start - 1) * 100, 2),
        "cagr_pct": round(_cagr(start, end, days), 2),
        "sharpe": round(_sharpe(daily_ret), 2),
        "max_drawdown_pct": round(_max_drawdown(equity), 2),
        "num_trades": 1,
        "days": days,
    }


def _run_signal(
    close: pd.Series, signal: pd.Series, lookback_days: int
) -> dict:
    """Long-only walk: enter on signal=1, exit on signal=0. No fees modeled."""
    if close.empty or signal.empty:
        return _empty_result()
    aligned = pd.concat([close, signal], axis=1).dropna()
    aligned.columns = ["close", "sig"]
    if len(aligned) < 2:
        return _empty_result()

    in_pos = False
    entry_price = 0.0
    trades: list[tuple[float, float]] = []
    equity = [1.0]

    for i in range(1, len(aligned)):
        price_prev = aligned["close"].iloc[i - 1]
        price_now = aligned["close"].iloc[i]
        sig_now = aligned["sig"].iloc[i]

        if in_pos:
            equity.append(equity[-1] * (price_now / price_prev))
        else:
            equity.append(equity[-1])

        # Transition signals — execute on next bar (use today's signal for tomorrow)
        if not in_pos and sig_now == 1:
            in_pos = True
            entry_price = float(price_now)
        elif in_pos and sig_now == 0:
            trades.append((entry_price, float(price_now)))
            in_pos = False

    if in_pos:
        trades.append((entry_price, float(aligned["close"].iloc[-1])))

    eq_series = pd.Series(equity, index=aligned.index)
    daily_ret = eq_series.pct_change().dropna()
    days = (aligned.index[-1] - aligned.index[0]).days
    final = equity[-1]
    return {
        "total_return_pct": round((final - 1) * 100, 2),
        "cagr_pct": round(_cagr(1.0, final, days), 2),
        "sharpe": round(_sharpe(daily_ret), 2),
        "max_drawdown_pct": round(_max_drawdown(eq_series), 2),
        "num_trades": len(trades),
        "days": days,
    }


def _empty_result() -> dict:
    return {
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "sharpe": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 0,
        "days": 0,
    }


def _rsi_signal(close: pd.Series) -> pd.Series:
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    sig = pd.Series(np.nan, index=close.index)
    sig[rsi < 30] = 1
    sig[rsi > 70] = 0
    return sig.ffill().fillna(0)


def _sma_cross_signal(close: pd.Series) -> pd.Series:
    sma = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    return (close > sma).astype(int)


def _fetch_close(symbol: str, period: str) -> pd.Series:
    """Download Close series for one symbol."""
    try:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"[backtest] fetch {symbol} failed: {e}", flush=True)
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df.columns:
        return pd.Series(dtype=float)
    return df["Close"].dropna()


def backtest_symbol(
    symbol: str,
    strategy: str = "buy_hold",
    lookback_days: int = 365,
) -> dict[str, Any]:
    """Backtest one symbol on one strategy. Cached 1h."""
    if strategy not in _STRATEGIES:
        return {"error": f"unknown strategy: {strategy}"}
    cache_key = (symbol.upper(), strategy, int(lookback_days))
    entry = _CACHE.get(cache_key)
    now = time.time()
    if entry and (now - entry[1]) < _CACHE_TTL:
        return entry[0]

    period = "2y" if lookback_days > 365 else "1y"
    close = _fetch_close(symbol, period)
    if close.empty or len(close) < 30:
        result = {"symbol": symbol, "strategy": strategy, **_empty_result(),
                  "error": "insufficient_data"}
        _CACHE[cache_key] = (result, now)
        return result

    # Trim to lookback window
    cutoff = close.index[-1] - pd.Timedelta(days=lookback_days)
    close = close[close.index >= cutoff]

    if strategy == "buy_hold":
        metrics = _run_buy_hold(close)
    elif strategy == "rsi_swing":
        sig = _rsi_signal(close)
        metrics = _run_signal(close, sig, lookback_days)
    elif strategy == "sma_cross":
        sig = _sma_cross_signal(close)
        metrics = _run_signal(close, sig, lookback_days)
    else:
        metrics = _empty_result()

    result = {"symbol": symbol, "strategy": strategy, **metrics}
    _CACHE[cache_key] = (result, now)
    return result


def backtest_portfolio(
    symbols: list[str],
    weights: dict[str, float] | None = None,
    lookback_days: int = 365,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    """Backtest each symbol across listed strategies.
    Returns per-symbol results + weighted-portfolio totals + delta-vs-buy-hold.
    """
    if not symbols:
        return {"error": "no symbols"}
    strategies = strategies or list(_STRATEGIES)
    bad = [s for s in strategies if s not in _STRATEGIES]
    if bad:
        return {"error": f"unknown strategies: {bad}"}

    per_symbol: dict[str, dict[str, dict]] = {}
    for sym in symbols:
        per_symbol[sym] = {}
        for strat in strategies:
            per_symbol[sym][strat] = backtest_symbol(sym, strat, lookback_days)

    # Weighted portfolio totals per strategy
    w = weights or {s: 1.0 / len(symbols) for s in symbols}
    w_sum = sum(w.get(s, 0.0) for s in symbols) or 1.0
    norm_w = {s: w.get(s, 0.0) / w_sum for s in symbols}

    portfolio_totals: dict[str, dict] = {}
    for strat in strategies:
        agg_return = 0.0
        agg_cagr = 0.0
        worst_dd = 0.0
        n_valid = 0
        for sym in symbols:
            r = per_symbol[sym][strat]
            if r.get("error"):
                continue
            agg_return += r.get("total_return_pct", 0.0) * norm_w[sym]
            agg_cagr += r.get("cagr_pct", 0.0) * norm_w[sym]
            worst_dd = min(worst_dd, r.get("max_drawdown_pct", 0.0))
            n_valid += 1
        portfolio_totals[strat] = {
            "weighted_total_return_pct": round(agg_return, 2),
            "weighted_cagr_pct": round(agg_cagr, 2),
            "worst_position_drawdown_pct": round(worst_dd, 2),
            "symbols_evaluated": n_valid,
        }

    # Delta vs buy_hold per strategy
    deltas: dict[str, float] = {}
    if "buy_hold" in portfolio_totals:
        base = portfolio_totals["buy_hold"]["weighted_total_return_pct"]
        for strat, totals in portfolio_totals.items():
            if strat == "buy_hold":
                continue
            deltas[strat] = round(
                totals["weighted_total_return_pct"] - base, 2
            )

    return {
        "lookback_days": lookback_days,
        "strategies": strategies,
        "per_symbol": per_symbol,
        "portfolio_totals": portfolio_totals,
        "delta_vs_buy_hold_pct": deltas,
    }
