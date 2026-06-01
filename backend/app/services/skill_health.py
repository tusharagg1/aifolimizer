"""Nightly skill-health gate.

Reads `scored_recommendations.jsonl` (written by `recommendations.score`),
groups by skill, and disables any skill that has been net-negative over a
meaningful sample size:

  hit_rate < 0.40 AND profit_factor < 0.9 AND n >= 20

Calls `agent_registry.set_enabled(skill, False)` when the gate trips and
appends an audit row to `.cache/skill_health_audit.jsonl`.

Differs from `backtest_gate`:
  * `backtest_gate` looks at offline walk-forward + deflated Sharpe.
  * `skill_health` looks at live forward-tested recommendations.
A skill that survives offline but bleeds money live still gets muted here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.security import get_logger
from app.services import agent_registry

_LOG = get_logger("aifolimizer.services.skill_health")

_CTX = Path(__file__).resolve().parents[2] / ".claude" / "context"
_SCORED_FILE = _CTX / "scored_recommendations.jsonl"
_AUDIT_FILE = (
    Path(__file__).resolve().parents[2] / ".cache" / "skill_health_audit.jsonl"
)

DEFAULT_HIT_RATE_FLOOR = 0.40
DEFAULT_PF_FLOOR = 0.9
DEFAULT_MIN_SAMPLES = 20


def _load_scored() -> list[dict]:
    if not _SCORED_FILE.exists():
        return []
    out: list[dict] = []
    for line in _SCORED_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _per_skill_stats(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{skill: {n, hit_rate, profit_factor, avg_return}}.

    profit_factor = sum(positive_returns) / abs(sum(negative_returns)). Skill
    must have at least one closed signal of each sign before profit_factor
    can be computed; otherwise returns ``inf`` (all-wins) or ``0`` (all-losses)
    so callers can still decide.
    """
    by_skill: dict[str, list[float]] = {}
    for r in rows:
        if r.get("status") not in ("target_hit", "stop_hit", "expired", "closed"):
            continue
        ret = r.get("return_pct")
        if ret is None:
            continue
        skill = (r.get("skill") or "").strip() or "unknown"
        by_skill.setdefault(skill, []).append(float(ret))

    out: dict[str, dict[str, float]] = {}
    for skill, returns in by_skill.items():
        if not returns:
            continue
        wins = [x for x in returns if x > 0]
        losses = [x for x in returns if x <= 0]
        loss_sum = abs(sum(losses))
        pf = (sum(wins) / loss_sum) if loss_sum > 0 else (
            float("inf") if wins else 0.0
        )
        out[skill] = {
            "n": float(len(returns)),
            "hit_rate": len(wins) / len(returns),
            "profit_factor": pf,
            "avg_return": sum(returns) / len(returns),
        }
    return out


def _append_audit(row: dict) -> None:
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        _LOG.warning("skill_health: audit append failed: %s", e)


def enforce(
    *,
    hit_rate_floor: float = DEFAULT_HIT_RATE_FLOOR,
    pf_floor: float = DEFAULT_PF_FLOOR,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    rows = _load_scored()
    stats = _per_skill_stats(rows)

    muted: list[dict] = []
    inspected: list[dict] = []
    for skill, s in stats.items():
        n = int(s["n"])
        if n < min_samples:
            continue
        inspected.append({
            "skill": skill,
            "n": n,
            "hit_rate": round(s["hit_rate"], 3),
            "profit_factor": (
                round(s["profit_factor"], 3)
                if s["profit_factor"] != float("inf")
                else "inf"
            ),
        })
        if s["hit_rate"] >= hit_rate_floor or s["profit_factor"] >= pf_floor:
            continue
        try:
            current = agent_registry.runtime_state(skill).get("enabled", True)
        except Exception:
            current = True
        if not current:
            continue
        try:
            agent_registry.set_enabled(skill, False)
        except Exception as e:
            _LOG.warning("skill_health: set_enabled(%s) failed: %s", skill, e)
            continue
        row = {
            "ts": time.time(),
            "skill": skill,
            "n": n,
            "hit_rate": round(s["hit_rate"], 3),
            "profit_factor": (
                round(s["profit_factor"], 3)
                if s["profit_factor"] != float("inf")
                else None
            ),
            "action": "disabled",
        }
        _append_audit(row)
        muted.append(row)
        _LOG.warning(
            "skill_health: muted %s (hit=%.2f pf=%s n=%d)",
            skill, s["hit_rate"], row["profit_factor"], n,
        )

    return {
        "n_inspected": len(inspected),
        "muted": muted,
        "inspected": inspected,
        "thresholds": {
            "hit_rate_floor": hit_rate_floor,
            "pf_floor": pf_floor,
            "min_samples": min_samples,
        },
    }
