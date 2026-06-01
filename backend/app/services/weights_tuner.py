"""Nightly weights tuner (Phase 5).

Reads signal_history accuracy per sub-signal (tech/fund/macro/sentiment/skill)
at horizon_days=21 over a 90-day lookback and adjusts the 5 sub-signal weights
by a small gradient step. Writes a new versioned row to the `weights` table
when anything changes.

Bounds:
  legacy signals (tech/fund/macro/sentiment): [0.5, 1.5]
  skill weight: [0.1, 1.5]   — starts smaller, allowed to grow if proven

Phase 11 swaps the objective from accuracy → after-cost expectancy.
This module keeps both code paths so the swap is a one-line config change.

Safety:
  - refuse update if any signal has n_samples < 20 (too noisy)
  - clamp every weight to its bound
  - persist attribution snapshot in `weights.attribution` JSONB for audit
  - publish redis events:weights_updated event so consumers can refresh cache
"""
from __future__ import annotations

import json
import logging
from typing import Literal

log = logging.getLogger(__name__)

_W_MIN_LEGACY, _W_MAX_LEGACY = 0.5, 1.5
_W_MIN_SKILL, _W_MAX_SKILL = 0.1, 1.5
_BUMP = 1.05
_CUT = 0.95
_BUMP_OVERCONFIDENT = 1.02   # half-step when calibration says we're overconfident
_CUT_OVERCONFIDENT = 0.92    # heavier cut when overconfident
_N_MIN = 20

Objective = Literal["accuracy", "expectancy"]


def _bounds_for(source: str) -> tuple[float, float]:
    if source == "skill":
        return _W_MIN_SKILL, _W_MAX_SKILL
    return _W_MIN_LEGACY, _W_MAX_LEGACY


def _adjust(
    w_old: float,
    stats: dict,
    *,
    source: str,
    objective: Objective,
    overconfident: bool = False,
) -> float:
    lo, hi = _bounds_for(source)
    n = int(stats.get("n") or 0)
    if n < _N_MIN:
        return w_old

    # When the most recent calibration verdict is "overconfident" the model's
    # win-prob is systematically optimistic. Smaller bumps and bigger cuts
    # damp future overshoot without freezing the tuner.
    bump = _BUMP_OVERCONFIDENT if overconfident else _BUMP
    cut = _CUT_OVERCONFIDENT if overconfident else _CUT

    if objective == "accuracy":
        hit = float(stats.get("win_rate") or 0)
        avg = float(stats.get("avg_return") or 0)
        if hit >= 0.55 and avg > 0.005:
            return min(round(w_old * bump, 2), hi)
        if hit <= 0.45 or avg < 0:
            return max(round(w_old * cut, 2), lo)
        return w_old

    # Phase 11 objective: expectancy + profit factor.
    exp = float(stats.get("after_cost_expectancy_pct") or 0)
    pf = float(stats.get("profit_factor") or 0)
    if exp > 0.005 and pf > 1.1:
        return min(round(w_old * bump, 2), hi)
    if exp < 0 or pf < 0.9:
        return max(round(w_old * cut, 2), lo)
    return w_old


async def recalibrate(
    *,
    horizon_days: int = 21,
    lookback_days: int = 90,
    objective: Objective | None = None,
) -> dict:
    """Run one tuning pass. Returns a result dict for the RQ result log.

    Phase 11: if `objective` is None, auto-select. Use 'expectancy' once any
    sub-signal has ≥20 horizon-scored samples (enough to estimate after-cost
    EV reliably); fall back to 'accuracy' otherwise.
    """
    try:
        from app.db.repositories import signals_repo, weights_repo
    except Exception as e:
        return {"status": "error", "error": f"db not available: {e}"}

    attribution = await signals_repo.attribution_by_source(
        horizon_days=horizon_days, lookback_days=lookback_days,
    )
    if not attribution:
        return {
            "status": "skip",
            "reason": "no horizon-scored samples yet",
            "horizon_days": horizon_days,
        }

    # Phase 11: auto-select objective once enough samples exist.
    if objective is None:
        max_n = max(
            (int((d or {}).get("n") or 0) for d in attribution.values()),
            default=0,
        )
        objective = "expectancy" if max_n >= _N_MIN else "accuracy"

    # Calibration check: if the model is currently overconfident, damp the
    # tuner step so a streak of optimistic predictions doesn't ratchet
    # weights up further. `overconfident` flag flows into _adjust below.
    overconfident = False
    try:
        from app.services.calibration import calibration_verdict
        cal = await calibration_verdict(horizon_days=horizon_days)
        if isinstance(cal, dict) and cal.get("verdict") == "overconfident":
            overconfident = True
    except Exception as e:
        log.debug("calibration probe in tuner failed: %s", e)

    current = await weights_repo.current()
    proposed = {
        k: float(current.get(k) or 0)
        for k in ("w_tech", "w_fund", "w_macro", "w_sentiment", "w_skill")
    }
    for src in ("tech", "fund", "macro", "sentiment", "skill"):
        stats = attribution.get(src)
        if not stats:
            continue
        key = f"w_{src}"
        proposed[key] = _adjust(
            proposed[key], stats, source=src, objective=objective,
            overconfident=overconfident,
        )

    changed = {
        k: (float(current.get(k) or 0), proposed[k])
        for k in proposed
        if abs(float(current.get(k) or 0) - proposed[k]) > 1e-6
    }
    if not changed:
        return {
            "status": "noop",
            "reason": "no weight change after tuning",
            "objective": objective,
            "attribution_n": {s: int((d or {}).get("n") or 0)
                              for s, d in attribution.items()},
        }

    version = await weights_repo.insert_version(
        proposed,
        reason="nightly_tuner",
        objective=objective,
        attribution=attribution,
    )

    # Publish event so consumers (recommendations._load_weights cache)
    # refresh on next request.
    try:
        from app.cache import get_redis
        r = get_redis()
        if r is not None:
            await r.publish(
                "events:weights_updated",
                json.dumps({"version": version, "objective": objective}),
            )
    except Exception as e:
        log.warning("weights_updated publish failed: %s", e)

    return {
        "status": "ok",
        "version": version,
        "objective": objective,
        "changed": {k: {"prev": v[0], "new": v[1]} for k, v in changed.items()},
    }
