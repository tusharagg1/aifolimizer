"""Portfolio-level Risk Gate (Phase 12).

Circuit breaker that gates new BUY signals + scales position sizing when
portfolio-wide risk metrics exceed thresholds. Distinct from per-symbol
concentration warnings (which already exist).

Triggers (any one → reduce_size; combinations escalate to halt):
  - portfolio max DD over window > 15%         → reduce_size
  - portfolio max DD over window > 25%         → halt
  - VIX > 35                                    → reduce_size
  - 5 consecutive losing recs in last 7d        → reduce_size
  - calibration ECE > 0.30 in last 30d          → reduce_size (overconfident)

Effects (consumed by callers — Phase 12 only computes/persists the gate):
  - status='trade'         → size_multiplier 1.0, no effect
  - status='reduce_size'   → size_multiplier 0.5; BUY alerts soften, sizing cut
  - status='halt'          → size_multiplier 0.0; BUY/ADD recs suppressed

State is persisted to `risk_gate_events` hypertable and cached in Redis at
`risk_gate:{tenant_hash}`. User can override via `/ws/risk-gate/override`
(Phase 12 endpoint) for up to 24h.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)


# Trigger thresholds
_DD_REDUCE_PCT = -15.0
_DD_HALT_PCT = -25.0
_VIX_REDUCE = 35.0
_LOSS_STREAK_REDUCE = 5
_LOSS_STREAK_WINDOW_DAYS = 7
_ECE_REDUCE = 0.30

_DEFAULT_VALID_HOURS = 24


@dataclass
class RiskGateState:
    status: str                                       # trade|reduce_size|halt
    size_multiplier: float                            # 1.0 / 0.5 / 0.0
    reasons: list[str] = field(default_factory=list)
    triggers: dict[str, Any] = field(default_factory=dict)
    triggered_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    valid_until: datetime = field(
        default_factory=lambda:
            datetime.now(tz=timezone.utc) + timedelta(
                hours=_DEFAULT_VALID_HOURS,
            ),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "size_multiplier": self.size_multiplier,
            "reasons": self.reasons,
            "triggers": self.triggers,
            "triggered_at": self.triggered_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
        }


def _trade() -> RiskGateState:
    return RiskGateState(status="trade", size_multiplier=1.0)


# ── pure rule evaluator ────────────────────────────────────────────────────

def evaluate(
    *,
    max_drawdown_pct: float | None,
    vix: float | None,
    loss_streak_count: int,
    calibration_ece: float | None,
) -> RiskGateState:
    """Pure: given current portfolio metrics, return a RiskGateState.
    No I/O. Caller fetches inputs + persists output.
    """
    reasons: list[str] = []
    triggers: dict[str, Any] = {}
    halt = False
    reduce = False

    if max_drawdown_pct is not None:
        if max_drawdown_pct <= _DD_HALT_PCT:
            halt = True
            reasons.append(f"max DD {max_drawdown_pct:.1f}%")
            triggers["max_drawdown_pct"] = max_drawdown_pct
        elif max_drawdown_pct <= _DD_REDUCE_PCT:
            reduce = True
            reasons.append(f"max DD {max_drawdown_pct:.1f}%")
            triggers["max_drawdown_pct"] = max_drawdown_pct

    if vix is not None and vix >= _VIX_REDUCE:
        reduce = True
        reasons.append(f"VIX {vix:.1f}")
        triggers["vix"] = vix

    if loss_streak_count >= _LOSS_STREAK_REDUCE:
        reduce = True
        reasons.append(
            f"{loss_streak_count} consecutive losses in "
            f"{_LOSS_STREAK_WINDOW_DAYS}d",
        )
        triggers["loss_streak_count"] = loss_streak_count

    if calibration_ece is not None and calibration_ece > _ECE_REDUCE:
        reduce = True
        reasons.append(f"calibration ECE {calibration_ece:.2f}")
        triggers["calibration_ece"] = calibration_ece

    if halt:
        return RiskGateState(
            status="halt", size_multiplier=0.0,
            reasons=reasons, triggers=triggers,
        )
    if reduce:
        return RiskGateState(
            status="reduce_size", size_multiplier=0.5,
            reasons=reasons, triggers=triggers,
        )
    return _trade()


# ── DB-backed orchestration ────────────────────────────────────────────────

async def _gather_inputs(tenant_hash: str) -> dict[str, Any]:
    """Fetch current trigger inputs from PG (best-effort)."""
    out: dict[str, Any] = {
        "max_drawdown_pct": None,
        "vix": None,
        "loss_streak_count": 0,
        "calibration_ece": None,
    }
    try:
        from app.db.pool import get_pool
        pool = get_pool()
        if pool is None:
            return out
        async with pool.acquire() as conn:
            # Max DD from latest 90d KPI snapshot
            row = await conn.fetchrow(
                """
                SELECT max_drawdown_pct FROM live_kpi_snapshots
                WHERE tenant_hash = $1 AND window_days = 90
                ORDER BY ts DESC LIMIT 1
                """,
                tenant_hash,
            )
            if row and row["max_drawdown_pct"] is not None:
                out["max_drawdown_pct"] = float(row["max_drawdown_pct"])

            # Loss streak in last 7d (consecutive losers from most-recent closed)
            streak_rows = await conn.fetch(
                """
                SELECT win FROM recommendations
                WHERE tenant_hash = $1
                  AND status <> 'open'
                  AND exit_date >
                      current_date - ($2::TEXT || ' days')::INTERVAL
                ORDER BY exit_date DESC, id DESC
                LIMIT 20
                """,
                tenant_hash, str(_LOSS_STREAK_WINDOW_DAYS),
            )
            count = 0
            for r in streak_rows:
                if r["win"] is False:
                    count += 1
                else:
                    break
            out["loss_streak_count"] = count

            # Latest calibration ECE
            cal_row = await conn.fetchrow(
                """
                SELECT ece FROM calibration_reports
                WHERE horizon_days = 21
                  AND ts > now() - INTERVAL '30 days'
                ORDER BY ts DESC LIMIT 1
                """,
            )
            if cal_row and cal_row["ece"] is not None:
                out["calibration_ece"] = float(cal_row["ece"])

            # Latest regime row carries VIX
            regime_row = await conn.fetchrow(
                "SELECT vix FROM regime_history "
                "ORDER BY ts DESC LIMIT 1"
            )
            if regime_row and regime_row["vix"] is not None:
                out["vix"] = float(regime_row["vix"])
    except Exception as e:
        log.warning("risk_gate gather_inputs failed: %s", e)
    return out


async def _persist(tenant_hash: str, state: RiskGateState) -> None:
    try:
        from app.db.pool import get_pool
        pool = get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO risk_gate_events (
                  tenant_hash, ts, status, size_multiplier,
                  reasons, triggers, valid_until
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                """,
                tenant_hash, state.triggered_at, state.status,
                state.size_multiplier, state.reasons,
                json.dumps(state.triggers), state.valid_until,
            )
    except Exception as e:
        log.warning("risk_gate persist failed: %s", e)

    try:
        from app.cache import get_redis
        r = get_redis()
        if r is not None:
            await r.set(
                f"risk_gate:{tenant_hash}",
                json.dumps(state.to_dict()),
                ex=int(_DEFAULT_VALID_HOURS * 3600),
            )
    except Exception as e:
        log.warning("risk_gate redis set failed: %s", e)


async def evaluate_and_persist(tenant_hash: str) -> RiskGateState:
    """Full pipeline: fetch inputs → evaluate → persist → return."""
    inputs = await _gather_inputs(tenant_hash)
    state = evaluate(
        max_drawdown_pct=inputs["max_drawdown_pct"],
        vix=inputs["vix"],
        loss_streak_count=inputs["loss_streak_count"],
        calibration_ece=inputs["calibration_ece"],
    )
    await _persist(tenant_hash, state)
    return state


async def get_current(tenant_hash: str) -> RiskGateState | None:
    """Read current gate state from Redis (preferred) or PG."""
    try:
        from app.cache import get_redis
        r = get_redis()
        if r is not None:
            blob = await r.get(f"risk_gate:{tenant_hash}")
            if blob:
                d = json.loads(blob)
                return RiskGateState(
                    status=d["status"],
                    size_multiplier=float(d["size_multiplier"]),
                    reasons=d.get("reasons", []),
                    triggers=d.get("triggers", {}),
                    triggered_at=datetime.fromisoformat(d["triggered_at"]),
                    valid_until=datetime.fromisoformat(d["valid_until"]),
                )
    except Exception as e:
        log.warning("risk_gate redis get failed: %s", e)
    try:
        from app.db.pool import get_pool
        pool = get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM risk_gate_events
                WHERE tenant_hash = $1
                ORDER BY ts DESC LIMIT 1
                """,
                tenant_hash,
            )
        if not row:
            return None
        return RiskGateState(
            status=row["status"],
            size_multiplier=float(row["size_multiplier"] or 0),
            reasons=list(row["reasons"] or []),
            triggers=row["triggers"] or {},
            triggered_at=row["ts"],
            valid_until=row["valid_until"]
                or row["ts"] + timedelta(hours=_DEFAULT_VALID_HOURS),
        )
    except Exception as e:
        log.warning("risk_gate get_current failed: %s", e)
        return None


async def override(
    tenant_hash: str, reason: str, hours: int = 24,
) -> RiskGateState:
    """User manual override → status=trade, size_multiplier=1.0 for N hours.
    Logged in risk_gate_events with reason.
    """
    hours = max(1, min(24, hours))
    state = RiskGateState(
        status="trade",
        size_multiplier=1.0,
        reasons=[f"manual override: {reason}"],
        triggers={"override": True, "reason": reason},
        triggered_at=datetime.now(tz=timezone.utc),
        valid_until=datetime.now(tz=timezone.utc) + timedelta(hours=hours),
    )
    await _persist(tenant_hash, state)
    return state
