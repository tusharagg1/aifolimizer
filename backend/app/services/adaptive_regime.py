"""Adaptive (skill, regime) multipliers — Phase 11.

The static table in `market_regime._INITIAL_MULTIPLIERS` was an educated guess.
This module learns the table from realized data: for each (skill, composite
regime), aggregate the after-cost expectancy of every signal that fired in
that regime, then map the relative expectancy to a multiplier in [0.3, 1.5].

Output:
  backend/.cache/regime_multipliers.json
    {skill: {composite_regime: float}}

Read on every `market_regime.multiplier_for` call. If the file is missing
or stale we fall back to the static table — adaptive learning is purely
additive and non-blocking.

Wired from `app/jobs/scheduler.py` after `weights_tuner.recalibrate`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.adaptive_regime")

_OUT_DIR = Path(__file__).resolve().parents[2] / ".cache"
_OUT_FILE = _OUT_DIR / "regime_multipliers.json"

_BOUND_LO = 0.3
_BOUND_HI = 1.5
_MIN_SAMPLES = 10  # don't move a (skill, regime) cell with fewer firings


def _clip(v: float, lo: float = _BOUND_LO, hi: float = _BOUND_HI) -> float:
    return max(lo, min(hi, v))


def _expectancy_to_multiplier(exp_pct: float, baseline_pct: float) -> float:
    """Map relative expectancy to a [0.3, 1.5] multiplier.

    Centred at 1.0 when the (skill, regime) cell matches the all-skills
    baseline. Each percentage point of out-/under-performance shifts the
    multiplier by 0.1 — a deliberately gentle slope so a noisy month
    doesn't flip a regime call.
    """
    delta = exp_pct - baseline_pct
    return _clip(1.0 + 0.1 * delta)


async def recalibrate_multipliers() -> dict[str, Any]:
    """Pull per-(skill, regime) attribution from Postgres + write JSON.

    Falls back to a no-op when:
      * `signals_repo.attribution_by_skill_regime` isn't available yet
      * no skill has enough samples to learn from
    """
    try:
        from app.db.repositories import signals_repo
    except Exception as e:
        return {"status": "skip", "reason": f"db not available: {e}"}

    fn = getattr(signals_repo, "attribution_by_skill_regime", None)
    if fn is None:
        # The repository doesn't expose this slice yet; record a stub so
        # operators see the gap in the audit log without blocking.
        return {
            "status": "skip",
            "reason": "signals_repo.attribution_by_skill_regime missing",
        }
    try:
        raw = await fn()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Expected shape: {(skill, composite): {n, expectancy_pct, win_rate}}
    if not raw:
        return {"status": "skip", "reason": "no rows"}

    by_regime_baseline: dict[str, list[float]] = {}
    learned: dict[str, dict[str, float]] = {}
    for (skill, composite), stats in raw.items():
        n = int((stats or {}).get("n") or 0)
        exp = float((stats or {}).get("expectancy_pct") or 0.0)
        if n < _MIN_SAMPLES:
            continue
        by_regime_baseline.setdefault(composite, []).append(exp)
        learned.setdefault(skill, {})[composite] = exp

    # Resolve baselines per regime so the adaptive multiplier reflects
    # `relative` performance rather than absolute expectancy.
    baseline = {
        composite: (sum(values) / len(values)) if values else 0.0 for composite, values in by_regime_baseline.items()
    }

    multipliers: dict[str, dict[str, float]] = {}
    for skill, regimes in learned.items():
        multipliers[skill] = {
            composite: round(_expectancy_to_multiplier(exp, baseline.get(composite, 0.0)), 3)
            for composite, exp in regimes.items()
        }

    if not multipliers:
        return {"status": "skip", "reason": "no skill met n>=%d" % _MIN_SAMPLES}

    try:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        _OUT_FILE.write_text(
            json.dumps({"updated_ts": time.time(), "multipliers": multipliers}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        return {"status": "error", "error": f"write failed: {e}"}

    return {
        "status": "ok",
        "n_skills": len(multipliers),
        "regimes": sorted(baseline.keys()),
        "path": str(_OUT_FILE),
    }
