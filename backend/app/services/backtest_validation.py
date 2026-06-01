"""Backtest validation: block-bootstrap significance + Sharpe CI.

Answers "is this backtest result statistically meaningful, or just lucky?".

History: earlier code used a return-order permutation test for significance.
That was statistically void — Sharpe = mean/std·sqrt(252) is invariant under
permutation (reshuffling order changes neither mean nor std), so its p-value
sat at ~0.5 and could never reject the null. Kept below only as
``order_dependence_check`` for diagnostic transparency; significance now comes
from a stationary block bootstrap against a mean-centered (zero-drift) null.
"""

from __future__ import annotations

import math

import numpy as np

_N_SIMS = 1000
_RNG_SEED = 42
_DEFAULT_BLOCK = 5  # ~1 trading week; preserves short-horizon autocorrelation
_MIN_N = 30  # below this the bootstrap is too coarse to be meaningful (a
# stricter threshold than the old n>=10; on n=10 with block=5 only ~10
# distinct cyclic starts exist, so the null distribution collapses to
# noise and p_value is unreliable).


def _sharpe(returns: np.ndarray, ann_factor: float = math.sqrt(252)) -> float:
    std = returns.std()
    if std == 0 or len(returns) == 0:
        return 0.0
    return float(returns.mean() / std * ann_factor)


def _block_resample(returns: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """Stationary block bootstrap: stitch contiguous blocks to length n.

    Sampling blocks (not individual points) preserves autocorrelation, so the
    resampled Sharpe distribution reflects realistic dependence structure.
    """
    n = len(returns)
    out = np.empty(n, dtype=float)
    filled = 0
    while filled < n:
        start = int(rng.integers(0, n))
        take = min(block, n - filled)
        for k in range(take):
            out[filled + k] = returns[(start + k) % n]
        filled += take
    return out


def block_bootstrap_significance(
    daily_returns: np.ndarray,
    block: int = _DEFAULT_BLOCK,
    n_sims: int = _N_SIMS,
    seed: int = _RNG_SEED,
) -> dict:
    """Test observed Sharpe against a zero-drift null via block bootstrap.

    One-sided test for *positive* edge. Null hypothesis: returns have no
    positive drift. We build the null by mean-centering the returns
    (forcing expected return to 0) and block-resampling them n_sims times.
    p_value = P(null Sharpe >= observed Sharpe).

    Returned ``direction`` flag disambiguates the three meaningful outcomes
    a single boolean cannot:
      - ``positive_edge``  : significant_at_05 is True (p_value < 0.05).
      - ``negative_edge``  : observed Sharpe is below 5th percentile of null
                             — strategy is a confirmed loser, not just
                             "consistent with no edge".
      - ``no_edge``        : observed Sharpe sits inside the null —
                             indistinguishable from luck.

    Block size is clamped to max(1, min(block, n // 4)) so a degenerate
    "single block fills the whole resample" case (block >= n) is impossible.
    """
    n = len(daily_returns)
    if n < _MIN_N:
        return {
            "error": "insufficient_data",
            "n_returns": int(n),
            "min_required": _MIN_N,
        }

    # Clamp block to keep the resample non-degenerate. Below n//4 the null
    # has at least four independent blocks per resample.
    effective_block = max(1, min(int(block), n // 4))

    rng = np.random.default_rng(seed)
    observed = _sharpe(daily_returns)

    centered = daily_returns - daily_returns.mean()  # zero-drift null
    null_sharpes = np.array([_sharpe(_block_resample(centered, effective_block, rng)) for _ in range(n_sims)])

    p_upper = float(np.mean(null_sharpes >= observed))
    p_lower = float(np.mean(null_sharpes <= observed))
    if p_upper < 0.05:
        direction = "positive_edge"
    elif p_lower < 0.05:
        direction = "negative_edge"
    else:
        direction = "no_edge"
    return {
        "observed_sharpe": round(observed, 3),
        "null_sharpe_mean": round(float(null_sharpes.mean()), 3),
        "null_sharpe_std": round(float(null_sharpes.std()), 3),
        "p_value": round(p_upper, 3),
        "p_value_lower_tail": round(p_lower, 3),
        "significant_at_05": p_upper < 0.05,
        "direction": direction,
        "block_size": effective_block,
        "block_size_requested": int(block),
        "n_simulations": n_sims,
        "method": "block_bootstrap_vs_zero_drift_null",
    }


def order_dependence_check(
    daily_returns: np.ndarray,
    n_sims: int = _N_SIMS,
    seed: int = _RNG_SEED,
) -> dict:
    """Diagnostic ONLY — measures sensitivity of Sharpe to return ordering.

    Permutes the return order and recomputes Sharpe. Because Sharpe depends only
    on mean/std (both order-invariant), the permuted Sharpe ≈ observed Sharpe by
    construction. A near-zero spread here just confirms Sharpe ignores ordering;
    a non-trivial spread would flag a Sharpe variant that is order-sensitive.

    This is NOT a significance test and its 'spread' must never be read as edge.
    Use block_bootstrap_significance for the significance verdict.
    """
    if len(daily_returns) < _MIN_N:
        return {
            "error": "insufficient_data",
            "n_returns": int(len(daily_returns)),
            "min_required": _MIN_N,
        }

    rng = np.random.default_rng(seed)
    observed = _sharpe(daily_returns)
    permuted = np.array([_sharpe(rng.permutation(daily_returns)) for _ in range(n_sims)])
    return {
        "observed_sharpe": round(observed, 3),
        "permuted_sharpe_std": round(float(permuted.std()), 6),
        "note": ("diagnostic only — not a significance test; Sharpe is order-invariant by construction."),
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
    if len(daily_returns) < _MIN_N:
        return {
            "error": "insufficient_data",
            "n_returns": int(len(daily_returns)),
            "min_required": _MIN_N,
        }

    rng = np.random.default_rng(seed)
    n = len(daily_returns)

    sharpes = np.array([_sharpe(rng.choice(daily_returns, size=n, replace=True)) for _ in range(n_sims)])

    alpha = (1 - confidence) / 2
    return {
        "sharpe_ci_lower": round(float(np.quantile(sharpes, alpha)), 3),
        "sharpe_ci_upper": round(float(np.quantile(sharpes, 1 - alpha)), 3),
        "prob_positive_sharpe": round(float(np.mean(sharpes > 0)), 3),
        "confidence": confidence,
        "n_simulations": n_sims,
    }


def run_validation(daily_returns: np.ndarray) -> dict:
    """Full validation suite: block-bootstrap significance + Sharpe CI.

    ``significance`` is the headline verdict. ``order_dependence_check`` is a
    kept diagnostic (the retired permutation test) — never read as edge.
    """
    arr = np.asarray(daily_returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < _MIN_N:
        return {
            "status": "insufficient_data",
            "n_returns": int(len(arr)),
            "min_required": _MIN_N,
        }
    return {
        "status": "ok",
        "n_returns": len(arr),
        "significance": block_bootstrap_significance(arr),
        "bootstrap_ci": bootstrap_sharpe_ci(arr),
        "order_dependence_check": order_dependence_check(arr),
    }
