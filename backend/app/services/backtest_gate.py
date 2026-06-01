"""Auto-mute skills whose deflated Sharpe is statistically indistinguishable
from luck.

Deflated Sharpe (Bailey & López de Prado, 2014) corrects raw Sharpe for the
multiple-testing inflation that comes with selecting a winning strategy from
many candidates. A walk-forward run already records DSR per skill into
`backend/.cache/backtests/walk_forward_*.json`. Without a gate, those numbers
sit on disk and live recommendations from a 0.2-DSR skill keep firing as if
nothing happened.

This module:
  1. Reads every JSON dropped under `.cache/backtests/`.
  2. Aggregates per-skill DSR over the last `lookback_runs` runs.
  3. Calls `agent_registry.set_enabled(name, False)` for any skill whose
     median DSR over the window is below the threshold.
  4. Persists a small audit row to `.cache/backtest_gate_audit.jsonl` so
     a maintainer can see why the skill was muted.

Wired from `app/jobs/scheduler.py` after `walk_forward_all_skills`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import median
from typing import Any

from app.security import get_logger
from app.services import agent_registry

_LOG = get_logger("aifolimizer.services.backtest_gate")

_BACKTEST_DIR = Path(__file__).resolve().parents[2] / ".cache" / "backtests"
_AUDIT_FILE = Path(__file__).resolve().parents[2] / ".cache" / "backtest_gate_audit.jsonl"

# DSR < this over `lookback_runs` walk-forward runs trips the gate. 0.5 is the
# Bailey/Lopez threshold for "weak evidence — borderline, could be noise".
DEFAULT_DSR_THRESHOLD = 0.5
DEFAULT_LOOKBACK_RUNS = 2


def _load_recent_runs(lookback_runs: int) -> list[dict]:
    if not _BACKTEST_DIR.is_dir():
        return []
    files = sorted(
        _BACKTEST_DIR.glob("walk_forward_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:lookback_runs]
    out: list[dict] = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # pragma: no cover
            _LOG.warning("backtest_gate: failed reading %s: %s", f, e)
    return out


def _per_skill_dsr(runs: list[dict]) -> dict[str, list[float]]:
    """{skill: [dsr_run_1, dsr_run_2, ...]} aggregated across runs."""
    out: dict[str, list[float]] = {}
    for run in runs:
        for r in run.get("results") or []:
            skill = r.get("skill")
            dsr = r.get("deflated_sharpe")
            if not skill or dsr is None:
                continue
            out.setdefault(skill, []).append(float(dsr))
    return out


def _append_audit(row: dict) -> None:
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:  # pragma: no cover
        _LOG.warning("backtest_gate: audit append failed: %s", e)


def enforce_dsr_gate(
    *,
    threshold: float = DEFAULT_DSR_THRESHOLD,
    lookback_runs: int = DEFAULT_LOOKBACK_RUNS,
) -> dict[str, Any]:
    """Read recent walk-forward runs, mute skills below threshold.

    Skill must appear in at least `lookback_runs` runs to be considered —
    a one-off bad run shouldn't trip the gate.
    """
    runs = _load_recent_runs(lookback_runs)
    per_skill = _per_skill_dsr(runs)

    muted: list[dict] = []
    inspected: list[dict] = []
    for skill, scores in per_skill.items():
        if len(scores) < lookback_runs:
            continue
        med = float(median(scores))
        inspected.append({"skill": skill, "median_dsr": med, "n": len(scores)})
        if med >= threshold:
            continue
        # Skip if user already disabled the skill — nothing to do.
        try:
            current = agent_registry.runtime_state(skill).get("enabled", True)
        except Exception:
            current = True
        if not current:
            continue
        try:
            agent_registry.set_enabled(skill, False)
        except Exception as e:  # pragma: no cover
            _LOG.warning("backtest_gate: set_enabled(%s) failed: %s", skill, e)
            continue
        row = {
            "ts": time.time(),
            "skill": skill,
            "median_dsr": round(med, 3),
            "threshold": threshold,
            "lookback_runs": lookback_runs,
            "action": "disabled",
        }
        _append_audit(row)
        muted.append(row)
        _LOG.warning(
            "backtest_gate: muted %s (DSR=%.3f < %.2f over %d runs)",
            skill,
            med,
            threshold,
            len(scores),
        )

    return {
        "threshold": threshold,
        "lookback_runs": lookback_runs,
        "n_runs_loaded": len(runs),
        "n_inspected": len(inspected),
        "muted": muted,
        "inspected": inspected,
    }
