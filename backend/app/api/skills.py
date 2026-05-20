"""REST endpoints for codified skill snapshots + trust/accuracy helpers."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.services import skill_runner
from app.services import signal_history
from app.services import paper_trade
from app.jobs import scheduler


router = APIRouter()


@router.get("/list")
async def list_skills():
    return {
        "codified": skill_runner.codified_skills(),
        "llm_only": skill_runner.llm_only_skills(),
    }


@router.get("/snapshots")
async def get_all_snapshots():
    return {"snapshots": skill_runner.list_snapshots()}


@router.get("/snapshot/{skill}")
async def get_snapshot(skill: str):
    snap = skill_runner.read_snapshot(skill)
    if snap is None:
        raise HTTPException(404, f"no snapshot for skill={skill}")
    return snap


@router.post("/refresh")
async def refresh_snapshots(skill: str | None = Query(None)):
    """Force an immediate tick. Optional `skill` query runs one only."""
    if skill:
        if skill not in skill_runner.SKILL_RUNNERS:
            raise HTTPException(400, f"unknown or LLM-only skill: {skill}")
        # Re-fetch portfolio via scheduler helper to keep behavior identical
        portfolio = await scheduler._fetch_portfolio()
        if portfolio is None:
            raise HTTPException(409, "no active Wealthsimple session")
        snap = skill_runner.SKILL_RUNNERS[skill](portfolio)
        skill_runner.write_snapshot(snap)
        return snap
    return await scheduler.force_tick()


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
