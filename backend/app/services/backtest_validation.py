"""Statistical validation for backtests: Monte Carlo permutation + bootstrap Sharpe CI.

Ported and adapted from Vibe-Trading (HKUDS/Vibe-Trading, MIT License).
Answers "is this backtest result statistically meaningful or just lucky?".
"""

from __future__ import annotations

import math

import numpy as np

_N_SIMS = 1000
_RNG_SEED = 42


def _sharpe(returns: np.ndarray, ann_factor: float = math.sqrt(252)) -> float:
    std = returns.std()
    if std == 0 or len(returns) == 0:
        return 0.0
    return float(returns.mean() / std * ann_factor)


def monte_carlo_permutation(
    daily_returns: np.ndarray,
    n_sims: int = _N_SIMS,
    seed: int = _RNG_SEED,
) -> dict:
    """Shuffle return order n_sims times; test if observed Sharpe beats random.

    p_value < 0.05 → strategy edge is statistically significant at 95% confidence.
    High p_value → observed Sharpe is consistent with random luck.
    """
    if len(daily_returns) < 10:
        return {"error": "insufficient_data"}

    rng = np.random.default_rng(seed)
    observed = _sharpe(daily_returns)

    sim_sharpes = np.array([
        _sharpe(rng.permutation(daily_returns))
        for _ in range(n_sims)
    ])

    p_value = float(np.mean(sim_sharpes >= observed))
    return {
        "observed_sharpe": round(observed, 3),
        "simulated_sharpe_mean": round(float(sim_sharpes.mean()), 3),
        "simulated_sharpe_std": round(float(sim_sharpes.std()), 3),
        "p_value": round(p_value, 3),
        "significant_at_05": p_value < 0.05,
        "n_simulations": n_sims,
    }


def bootstrap_sharpe_ci(
    daily_returns: np.ndarray,
    n_sims: int = _N_SIMS,
    confidence: float = 0.95,
    seed: int = _RNG_SEED,
) -> dict:
    """Resample daily returns with replacement; return CI bounds on Sharpe.

    prob_positive_sharpe: probability strategy has positive Sharpe in a new
    unseen period — the key number for forward-looking edge assessment.
    """
    if len(daily_returns) < 10:
        return {"error": "insufficient_data"}

    rng = np.random.default_rng(seed)
    n = len(daily_returns)

    sharpes = np.array([
        _sharpe(rng.choice(daily_returns, size=n, replace=True))
        for _ in range(n_sims)
    ])

    alpha = (1 - confidence) / 2
    return {
        "sharpe_ci_lower": round(float(np.quantile(sharpes, alpha)), 3),
        "sharpe_ci_upper": round(float(np.quantile(sharpes, 1 - alpha)), 3),
        "prob_positive_sharpe": round(float(np.mean(sharpes > 0)), 3),
        "confidence": confidence,
        "n_simulations": n_sims,
    }


def run_validation(daily_returns: np.ndarray) -> dict:
    """Full validation suite: Monte Carlo permutation + bootstrap Sharpe CI."""
    arr = np.asarray(daily_returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 10:
        return {"status": "insufficient_data", "n_returns": len(arr)}
    return {
        "status": "ok",
        "n_returns": len(arr),
        "monte_carlo": monte_carlo_permutation(arr),
        "bootstrap_ci": bootstrap_sharpe_ci(arr),
    }
