"""Quantitative primitives: returns, volatility, Sharpe, Sortino, VaR, beta, correlation.

Zero external dependencies — pure stdlib. Adapted from ai-portfolio-analyzer reference.
"""
from __future__ import annotations

from statistics import mean, pstdev
from typing import Any
import math


TRADING_DAYS = 252


def simple_returns(values: list[float]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous > 0:
            out.append((current / previous) - 1.0)
    return out


def log_returns(values: list[float]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous > 0 and current > 0:
            out.append(math.log(current / previous))
    return out


def annualized_volatility(returns: list[float], periods: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * math.sqrt(periods)


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0, periods: int = TRADING_DAYS) -> float | None:
    if len(returns) < 2:
        return None
    excess_daily = risk_free_rate / periods
    excess = [value - excess_daily for value in returns]
    vol = pstdev(excess)
    if vol == 0:
        return None
    return (mean(excess) / vol) * math.sqrt(periods)


def sortino_ratio(returns: list[float], risk_free_rate: float = 0.0, periods: int = TRADING_DAYS) -> float | None:
    if len(returns) < 2:
        return None
    excess_daily = risk_free_rate / periods
    excess = [value - excess_daily for value in returns]
    downside = [min(0.0, value) for value in excess]
    downside_dev = math.sqrt(mean([value * value for value in downside]))
    if downside_dev == 0:
        return None
    return (mean(excess) / downside_dev) * math.sqrt(periods)


def historical_var(returns: list[float], confidence: float = 0.95) -> float:
    if not returns:
        return 0.0
    index = max(0, min(len(returns) - 1, math.floor((1.0 - confidence) * len(returns))))
    threshold_return = sorted(returns)[index]
    return max(0.0, -threshold_return)


def expected_shortfall(returns: list[float], confidence: float = 0.95) -> float:
    if not returns:
        return 0.0
    var = historical_var(returns, confidence)
    tail_losses = [-value for value in returns if value <= -var]
    return mean(tail_losses) if tail_losses else var


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value / peak) - 1.0)
    return worst


def beta(asset_returns: list[float], benchmark_returns: list[float]) -> float | None:
    paired = list(zip(asset_returns, benchmark_returns))
    if len(paired) < 2:
        return None
    asset = [item[0] for item in paired]
    benchmark = [item[1] for item in paired]
    bench_mean = mean(benchmark)
    variance = mean([(value - bench_mean) ** 2 for value in benchmark])
    if variance == 0:
        return None
    asset_mean = mean(asset)
    covariance = mean([(a - asset_mean) * (b - bench_mean) for a, b in paired])
    return covariance / variance


def correlation(left: list[float], right: list[float]) -> float | None:
    paired = list(zip(left, right))
    if len(paired) < 2:
        return None
    xs = [item[0] for item in paired]
    ys = [item[1] for item in paired]
    x_mean = mean(xs)
    y_mean = mean(ys)
    x_var = sum((value - x_mean) ** 2 for value in xs)
    y_var = sum((value - y_mean) ** 2 for value in ys)
    if x_var == 0 or y_var == 0:
        return None
    cov = sum((x - x_mean) * (y - y_mean) for x, y in paired)
    return cov / math.sqrt(x_var * y_var)


def correlation_matrix(symbol_returns: dict[str, list[float]], min_observations: int = 30) -> dict[str, dict[str, float | None]]:
    matrix: dict[str, dict[str, float | None]] = {}
    for left_symbol, left_returns in symbol_returns.items():
        matrix[left_symbol] = {}
        for right_symbol, right_returns in symbol_returns.items():
            length = min(len(left_returns), len(right_returns))
            if left_symbol == right_symbol:
                matrix[left_symbol][right_symbol] = 1.0
            elif length < min_observations:
                matrix[left_symbol][right_symbol] = None
            else:
                value = correlation(left_returns[-length:], right_returns[-length:])
                matrix[left_symbol][right_symbol] = round(value, 3) if value is not None else None
    return matrix


def weighted_portfolio_returns(symbol_returns: dict[str, list[float]], weights: dict[str, float]) -> list[float]:
    series = {sym: list(map(float, ret)) for sym, ret in symbol_returns.items() if ret and weights.get(sym, 0.0) > 0}
    if not series:
        return []
    length = min(len(values) for values in series.values())
    if length <= 0:
        return []
    out: list[float] = []
    total_weight = sum(weights.get(sym, 0.0) for sym in series) or 1.0
    for index in range(-length, 0):
        out.append(sum((weights.get(sym, 0.0) / total_weight) * values[index] for sym, values in series.items()))
    return out


def portfolio_risk_metrics(symbol_returns: dict[str, list[float]], weights: dict[str, float]) -> dict[str, Any]:
    returns = weighted_portfolio_returns(symbol_returns, weights)
    if not returns:
        return {
            "observations": 0,
            "annualized_volatility": None,
            "sharpe": None,
            "sortino": None,
            "var_95": None,
            "expected_shortfall_95": None,
            "worst_daily_return": None,
        }
    return {
        "observations": len(returns),
        "annualized_volatility": annualized_volatility(returns),
        "sharpe": sharpe_ratio(returns),
        "sortino": sortino_ratio(returns),
        "var_95": historical_var(returns, 0.95),
        "expected_shortfall_95": expected_shortfall(returns, 0.95),
        "worst_daily_return": min(returns),
    }
