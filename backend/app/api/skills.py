"""REST endpoints for codified skill snapshots + trust/accuracy helpers."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from app.security import session_from_request
from app.services import skill_runner
from app.services import signal_history
from app.services import paper_trade
from app.jobs import scheduler


router = APIRouter()


def _tenant_from(request: Request) -> str | None:
    """Resolve tenant id from httpOnly session cookie (or legacy query param)."""
    return session_from_request(request, request.query_params.get("session_id"))


@router.get("/list")
async def list_skills():
    return {
        "codified": skill_runner.codified_skills(),
        "llm_only": skill_runner.llm_only_skills(),
    }


@router.get("/snapshots")
async def get_all_snapshots(request: Request):
    tenant = _tenant_from(request)
    return {"snapshots": skill_runner.list_snapshots(tenant_id=tenant)}


@router.get("/snapshot/{skill}")
async def get_snapshot(skill: str, request: Request):
    tenant = _tenant_from(request)
    snap = skill_runner.read_snapshot(skill, tenant_id=tenant)
    if snap is None:
        raise HTTPException(404, f"no snapshot for skill={skill}")
    return snap


@router.post("/refresh")
async def refresh_snapshots(
    request: Request,
    skill: str | None = Query(None),
):
    """Force an immediate tick for this tenant. Optional `skill` runs one only."""
    tenant = _tenant_from(request)
    if not tenant:
        raise HTTPException(401, "no session — log in first")
    if skill:
        if skill not in skill_runner.SKILL_RUNNERS:
            raise HTTPException(400, f"unknown or LLM-only skill: {skill}")
        portfolio = await scheduler._fetch_portfolio_for(tenant)
        if portfolio is None:
            raise HTTPException(409, "session expired or portfolio fetch failed")
        snap = skill_runner.SKILL_RUNNERS[skill](portfolio)
        skill_runner.write_snapshot(snap, tenant_id=tenant)
        return snap
    # Run one tick for this tenant only
    return await scheduler._run_for_session(tenant)


@router.get("/scheduler/status")
async def status():
    return scheduler.scheduler_status()


# ── Trust / accuracy reporting ────────────────────────────────────────────────

@router.get("/trust/decay")
async def trust_decay(
    action_filter: str | None = Query(None),
    min_count: int = Query(5),
):
    """Empirical decay curve across 1/3/5/10/21/42/63d horizons."""
    return await asyncio.to_thread(
        signal_history.signal_decay_curve,
        signal_history._DEFAULT_HORIZONS,
        action_filter=action_filter,
        min_count=min_count,
    )


@router.get("/trust/attribution")
async def trust_attribution(
    horizon: int = Query(21),
    min_count: int = Query(5),
):
    """Per-sub-signal alpha attribution at the requested horizon."""
    return await asyncio.to_thread(
        signal_history.per_signal_source_attribution,
        horizon, min_count=min_count,
    )


@router.get("/trust/calibration")
async def trust_calibration(horizon: int = Query(21)):
    """Confidence-label calibration verdict at the requested horizon."""
    return await asyncio.to_thread(
        signal_history.calibrate_confidence,
        horizon,
    )


@router.get("/trust/track-record")
async def trust_track_record():
    """Rolling 7/30/90/180d benchmark-relative paper-trade track record."""
    return await asyncio.to_thread(paper_trade.get_track_record)


@router.get("/trust/accuracy")
async def trust_accuracy(horizon: int = Query(21)):
    """Per-action precision/recall + calibration table at horizon."""
    return await asyncio.to_thread(signal_history.accuracy_report, horizon)
