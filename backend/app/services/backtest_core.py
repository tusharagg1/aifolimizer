"""Shared backtest metric primitives.

Extracted verbatim from the (verified line-equivalent) per-engine copies in
backtest.py and skill_backtest.py so equity-curve math lives in one place.
backtest.py and skill_backtest.py import these as their private aliases, so
every existing call site is unchanged.

Deliberately NOT moved here (different contracts / behavior-sensitive state):
- backtest_stats._max_drawdown operates on np.ndarray, not pd.Series.
- the per-engine bar-fetch caches (_CLOSE_CACHE, _BATCH_BARS_CACHE) are mutable
  module state with provider-specific fetch logic.
"""

from __future__ import annotations

import math

import pandas as pd


def annualize_factor(periods_per_year: int = 252) -> float:
    return math.sqrt(periods_per_year)


def sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * annualize_factor())


def sortino(daily_ret: pd.Series) -> float:
    if daily_ret.empty:
        return 0.0
    downside = daily_ret[daily_ret < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    return float(daily_ret.mean() / downside.std() * annualize_factor())


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((equity - peak) / peak).min() * 100)


def cagr(start: float, end: float, days: int) -> float:
    if start <= 0 or days <= 0:
        return 0.0
    years = days / 365.25
    try:
        return float(((end / start) ** (1 / years) - 1) * 100)
    except Exception:
        return 0.0
