"""Quantitative primitives: returns, volatility, Sharpe, Sortino, VaR, beta, correlation.

Zero external dependencies — pure stdlib. Adapted from ai-portfolio-analyzer reference.
"""

from __future__ import annotations

import hashlib
from statistics import mean, pstdev, stdev
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
    return stdev(returns) * math.sqrt(periods)


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0, periods: int = TRADING_DAYS) -> float | None:
    if len(returns) < 2:
        return None
    excess_daily = risk_free_rate / periods
    excess = [value - excess_daily for value in returns]
    vol = stdev(excess)
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


def correlation_matrix(
    symbol_returns: dict[str, list[float]], min_observations: int = 30
) -> dict[str, dict[str, float | None]]:
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


def calmar_ratio(values: list[float], periods: int = TRADING_DAYS) -> float | None:
    """CAGR divided by absolute max drawdown. Penalises deep-drawdown strategies."""
    if len(values) < 2 or values[0] <= 0:
        return None
    returns = simple_returns(values)
    if not returns:
        return None
    years = len(returns) / periods
    if years <= 0:
        return None
    total_return = (values[-1] / values[0]) - 1
    cagr = (1 + total_return) ** (1 / years) - 1
    dd = max_drawdown(values)
    if dd == 0:
        return None
    return round(cagr / abs(dd), 3)


def omega_ratio(returns: list[float], threshold: float = 0.0) -> float | None:
    """Probability-weighted gains above threshold / losses below it."""
    if len(returns) < 2:
        return None
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = sum(threshold - r for r in returns if r <= threshold)
    if losses == 0:
        return None
    return round(gains / losses, 3)


def monthly_return_breakdown(dates: list[str], values: list[float]) -> list[dict[str, Any]]:
    """
    Monthly return table from daily NAV series.

    dates: "YYYY-MM-DD" strings aligned with values.
    Returns list of {year, month, return_pct} sorted chronologically.
    """
    if len(dates) != len(values) or len(dates) < 2:
        return []

    monthly: dict[str, list[float]] = {}
    for d, v in zip(dates, values):
        key = d[:7]  # "YYYY-MM"
        monthly.setdefault(key, []).append(v)

    out: list[dict[str, Any]] = []
    for key in sorted(monthly):
        prices = monthly[key]
        if len(prices) < 2:
            continue
        ret = round((prices[-1] / prices[0] - 1) * 100, 2)
        year, month = key.split("-")
        out.append({"year": int(year), "month": int(month), "return_pct": ret})
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


# ── Deterministic bucketing (adapted from AI-Trader experiments.py, MIT) ──────


def stable_bucket(key: str, n_buckets: int = 1_000_000) -> int:
    """SHA-256 deterministic bucket assignment. Same key always → same bucket.

    Use for reproducible walk-forward splits and strategy A/B grouping without
    persisting any state — the bucket is fully derived from the key string.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % n_buckets


def deterministic_split_idx(symbol: str, n_points: int, train_frac: float = 0.7) -> int:
    """Return split index for walk-forward backtests that is stable per symbol.

    Adds a small per-symbol jitter (±5% of n_points) to the nominal train_frac
    split so different symbols don't all break at the same calendar date. The
    jitter is deterministic — same symbol always gets the same offset.
    """
    nominal = int(n_points * train_frac)
    jitter_range = max(1, int(n_points * 0.05))
    bucket = stable_bucket(f"wf:{symbol}", n_buckets=2 * jitter_range + 1)
    offset = bucket - jitter_range  # maps [0, 2*jr] → [-jr, jr]
    return max(int(n_points * 0.3), min(int(n_points * 0.9), nominal + offset))


# ── Alpha factors (Qlib-inspired, pure stdlib) ────────────────────────────────


def momentum_factor(prices: list[float], lookback: int = 252, skip: int = 21) -> float | None:
    """12-1 month price momentum (Jegadeesh-Titman).

    Skips the last `skip` bars to avoid short-term reversal contamination.
    Positive = upward momentum over the lookback window.
    """
    if len(prices) < lookback + skip + 1:
        return None
    past = prices[-(lookback + skip)]
    reference = prices[-skip] if skip > 0 else prices[-1]
    if past <= 0:
        return None
    return round((reference / past) - 1.0, 4)


def mean_reversion_factor(returns: list[float], window: int = 20) -> float | None:
    """Short-term reversal: negative z-score of the most recent return.

    Positive value = oversold (expects bounce); negative = overbought (expects fade).
    Stats computed from the prior `window` returns (excluding the value being
    z-scored) to prevent self-contamination of mu/sd by returns[-1].
    """
    if len(returns) < window + 1:
        return None
    stats_window = returns[-window - 1 : -1]
    mu = mean(stats_window)
    sd = stdev(stats_window)
    if sd == 0:
        return None
    z = (returns[-1] - mu) / sd
    return round(-z, 4)


def volatility_adjusted_momentum(prices: list[float], lookback: int = 252, skip: int = 21) -> float | None:
    """Momentum divided by trailing annualised vol. Rewards smooth uptrends over noisy ones."""
    mom = momentum_factor(prices, lookback, skip)
    if mom is None or len(prices) < lookback:
        return None
    rets = simple_returns(prices[-lookback:])
    vol = annualized_volatility(rets)
    if vol == 0:
        return None
    return round(mom / vol, 4)


def _rank(values: list[float]) -> list[float]:
    """Average-rank assignment (ties get mean rank). Used for Spearman IC."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) - 1 and indexed[j + 1][1] == indexed[j][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg
        i = j + 1
    return ranks


def ic_score(factor_values: list[float], forward_returns: list[float]) -> float | None:
    """Spearman rank IC between a factor vector and realised forward returns.

    Interpretation: ±0.05 = weak, ±0.10 = useful, ±0.15+ = strong signal.
    Requires equal-length lists with ≥5 observations.
    """
    if len(factor_values) != len(forward_returns) or len(factor_values) < 5:
        return None
    return correlation(_rank(factor_values), _rank(forward_returns))


def icir(ic_series: list[float]) -> float | None:
    """IC Information Ratio = mean(IC) / std(IC).

    ≥0.5 = consistently predictive factor. Requires ≥3 IC observations.
    """
    if len(ic_series) < 3:
        return None
    mu = mean(ic_series)
    sd = pstdev(ic_series)
    if sd == 0:
        return None
    return round(mu / sd, 3)
