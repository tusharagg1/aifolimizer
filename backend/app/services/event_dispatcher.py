"""Event-driven skill trigger.

Listens for material market/portfolio events emitted by existing services
and runs targeted LLM skills out-of-band from the fixed scheduler cadence.

Events:
  - regime_flip(prev, new):    risk-assessment + portfolio-health + macro-impact
  - earnings_surprise(t, pct): earnings-postmortem for ticker
  - drawdown_breach(prev, new): risk-assessment + ntfy push
  - crowding_flip(t, prev, new): adversarial-research for ticker

Dedup: per (event_key, date) via Redis SETNX with 24h TTL. Inline fallback
to in-process set when Redis unavailable.

Snapshots are persisted to skill_snapshots via the same upsert path used by
the nightly orchestrator, so dashboards + signal_evidence pick them up
identically.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_DEDUP_TTL_S = 24 * 3600
_LOCAL_DEDUP: set[str] = set()

# Material flip thresholds
_EARNINGS_SURPRISE_THRESHOLD_PCT = 5.0
_CROWDING_FLIP_DELTA = 30
_REGIME_TRIGGER_COMPONENTS = ("trend", "volatility", "macro")
_DRAWDOWN_TRIGGER_STATUSES = ("reduce_size", "halt")


async def _claim_dedup(key: str) -> bool:
    """Return True if this is the first claim today, False if already fired."""
    try:
        from app.cache import get_redis
        r = get_redis()
        if r is not None:
            ok = await r.set(key, "1", ex=_DEDUP_TTL_S, nx=True)
            return bool(ok)
    except Exception as e:
        log.warning("dedup redis claim failed: %s", e)
    if key in _LOCAL_DEDUP:
        return False
    _LOCAL_DEDUP.add(key)
    return True


def _today() -> str:
    return date.today().isoformat()


async def _persist(tenant_hash: str, snapshot: dict) -> None:
    try:
        from app.db.repositories import snapshots_repo
        await snapshots_repo.upsert(tenant_hash, snapshot["skill"], snapshot)
    except Exception as e:
        log.warning(
            "event_dispatcher: persist failed (%s): %s",
            snapshot.get("skill"), e,
        )


def _push_ntfy(title: str, body: str, priority: str = "default") -> None:
    try:
        from app.core.config import settings
        if not settings.ntfy_topic:
            return
        from app.services.alerts import _push_ntfy as ntfy_send
        ntfy_send(
            topic=settings.ntfy_topic,
            title=title, body=body,
            priority=priority, tags="bell",
        )
    except Exception as e:
        log.warning("event_dispatcher: ntfy push failed: %s", e)


# ── Public event handlers ──────────────────────────────────────────────────

def _regime_is_material(prev: Any, new: Any) -> bool:
    if prev is None:
        return False
    for comp in _REGIME_TRIGGER_COMPONENTS:
        if getattr(prev, comp, None) != getattr(new, comp, None):
            return True
    return False


async def on_regime_flip(
    prev_regime: Any, new_regime: Any, *, tenant_hashes: list[str],
) -> dict[str, Any]:
    """Material regime change → run macro + risk + health LLM skills for all
    tenants. Dedup per composite per day so VIX wiggle does not spam.
    """
    if not _regime_is_material(prev_regime, new_regime):
        return {"status": "skip", "reason": "no_material_flip"}

    key = f"evt:regime:{new_regime.composite}:{_today()}"
    if not await _claim_dedup(key):
        return {"status": "skip", "reason": "deduped"}

    from app.services import skill_llm_runner
    ctx = {
        "regime_composite": new_regime.composite,
        "vix": new_regime.vix,
        "spy_vs_sma200_pct": new_regime.spy_vs_sma200_pct,
        "ten_y_yield": new_regime.ten_y_yield,
        "fed_funds": new_regime.fed_funds,
    }

    macro_snap = await skill_llm_runner.run_macro_impact(ctx)
    risk_snap = await skill_llm_runner.run_risk_assessment(ctx)
    health_snap = await skill_llm_runner.run_portfolio_health(ctx)

    for thash in tenant_hashes:
        await _persist(thash, macro_snap)
        await _persist(thash, risk_snap)
        await _persist(thash, health_snap)

    prev_label = (
        getattr(prev_regime, "composite", "n/a") if prev_regime else "n/a"
    )
    _push_ntfy(
        title=f"Regime flip: {prev_label} → {new_regime.composite}",
        body=(
            f"VIX {new_regime.vix} · "
            f"SPY-SMA200 {new_regime.spy_vs_sma200_pct}%. "
            "Risk/health/macro skills re-run."
        ),
        priority="high",
    )

    return {
        "status": "ok",
        "event": "regime_flip",
        "from": prev_label,
        "to": new_regime.composite,
        "tenants": len(tenant_hashes),
    }


async def on_earnings_surprise(
    tenant_hash: str,
    ticker: str,
    surprise_pct: float,
    *,
    context: dict | None = None,
) -> dict[str, Any]:
    """Surprise beyond ±threshold → earnings-postmortem LLM run."""
    if abs(surprise_pct) < _EARNINGS_SURPRISE_THRESHOLD_PCT:
        return {"status": "skip", "reason": "below_threshold"}

    key = f"evt:earnings:{tenant_hash[:8]}:{ticker}:{_today()}"
    if not await _claim_dedup(key):
        return {"status": "skip", "reason": "deduped"}

    from app.services import skill_llm_runner
    ctx = dict(context or {})
    ctx.update({"surprise_pct": surprise_pct, "earnings_date": _today()})
    snap = await skill_llm_runner.run_earnings_postmortem(ticker, ctx)
    await _persist(tenant_hash, snap)

    direction = "beat" if surprise_pct > 0 else "miss"
    _push_ntfy(
        title=f"{ticker}: earnings {direction} {surprise_pct:+.1f}%",
        body="Post-mortem skill re-run; check dashboard.",
        priority="high",
    )

    return {
        "status": "ok",
        "event": "earnings_surprise",
        "ticker": ticker,
        "surprise_pct": surprise_pct,
    }


async def on_drawdown_breach(
    tenant_hash: str,
    prev_status: str | None,
    new_status: str,
    *,
    context: dict | None = None,
) -> dict[str, Any]:
    """Risk gate flipped to reduce_size/halt → risk-assessment LLM + ntfy."""
    if new_status not in _DRAWDOWN_TRIGGER_STATUSES:
        return {"status": "skip", "reason": "non_trigger_status"}
    if prev_status == new_status:
        return {"status": "skip", "reason": "no_change"}

    key = f"evt:dd:{tenant_hash[:8]}:{new_status}:{_today()}"
    if not await _claim_dedup(key):
        return {"status": "skip", "reason": "deduped"}

    from app.services import skill_llm_runner
    ctx = dict(context or {})
    snap = await skill_llm_runner.run_risk_assessment(ctx)
    await _persist(tenant_hash, snap)

    _push_ntfy(
        title=f"Risk gate → {new_status.upper()}",
        body=(
            f"Drawdown/vol breach (prev={prev_status or 'trade'}). "
            "Risk assessment skill re-run."
        ),
        priority="urgent" if new_status == "halt" else "high",
    )

    return {
        "status": "ok",
        "event": "drawdown_breach",
        "from": prev_status,
        "to": new_status,
    }


async def on_crowding_flip(
    tenant_hash: str,
    ticker: str,
    prev_score: int | float,
    new_score: int | float,
    *,
    context: dict | None = None,
) -> dict[str, Any]:
    """Crowding score shift ≥threshold → adversarial-research LLM run."""
    delta = float(new_score) - float(prev_score)
    if abs(delta) < _CROWDING_FLIP_DELTA:
        return {"status": "skip", "reason": "below_delta"}

    key = f"evt:crowd:{tenant_hash[:8]}:{ticker}:{_today()}"
    if not await _claim_dedup(key):
        return {"status": "skip", "reason": "deduped"}

    from app.services import skill_llm_runner
    ctx = dict(context or {})
    ctx.update({"crowding_score": new_score, "crowding_delta": delta})
    snap = await skill_llm_runner.run_adversarial_research(ticker, ctx)
    await _persist(tenant_hash, snap)

    direction = "↑ crowded" if delta > 0 else "↓ uncrowded"
    _push_ntfy(
        title=f"{ticker}: crowding {direction} ({delta:+.0f})",
        body=f"Score {prev_score:.0f} → {new_score:.0f}. Re-thesis triggered.",
        priority="default",
    )

    return {
        "status": "ok",
        "event": "crowding_flip",
        "ticker": ticker,
        "delta": delta,
    }
