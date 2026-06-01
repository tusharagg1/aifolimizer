"""REST endpoints for the autonomous-agent dashboard.

Each agent in agent_registry.py exposes:
  GET  /agents/list                  → all agents w/ trigger + runtime state
  GET  /agents/{name}                → single agent detail + recent runs
  POST /agents/{name}/run            → force-run now (requires session)
  POST /agents/{name}/enable         → enable/disable
  POST /agents/{name}/snooze         → snooze N minutes
  GET  /agents/events/recent         → last N dispatched events (for live feed)

Reads last-run state from registry + skill_snapshots for output payload.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.security import session_from_request
from app.services import agent_registry as ar
from app.services import skill_runner

router = APIRouter()


def _tenant_from(request: Request) -> str | None:
    return session_from_request(request, request.query_params.get("session_id"))


@router.get("/whoami")
async def whoami(request: Request) -> dict[str, Any]:
    """Return session_id if cookie-auth valid, else null."""
    sid = _tenant_from(request)
    return {"authenticated": bool(sid), "session_id": sid}


@router.get("/list")
async def list_agents(request: Request) -> dict[str, Any]:
    """All registered agents with trigger + runtime state.

    Optionally pairs each entry with its latest snapshot (last LLM output)
    if session is present.
    """
    tenant = _tenant_from(request)
    agents = ar.list_agents()
    if tenant:
        for a in agents:
            try:
                snap = skill_runner.read_snapshot(a["name"], tenant_id=tenant)
                if snap:
                    a["last_snapshot"] = {
                        "computed_at": str(snap.get("computed_at")),
                        "status": snap.get("status"),
                        "summary": snap.get("summary"),
                    }
            except Exception:
                pass
    return {"agents": agents, "count": len(agents)}


@router.get("/{name}")
async def get_agent_detail(name: str, request: Request) -> dict[str, Any]:
    spec = ar.get_agent(name)
    if spec is None:
        raise HTTPException(404, f"unknown agent: {name}")
    tenant = _tenant_from(request)
    snap = None
    if tenant:
        try:
            snap = skill_runner.read_snapshot(name, tenant_id=tenant)
        except Exception:
            snap = None
    return {
        "name": spec.name,
        "description": spec.description,
        "trigger": spec.trigger,
        "schedule": spec.schedule,
        "event_types": spec.event_types,
        "model_pref": spec.model_pref,
        "auto_execute": spec.auto_execute,
        "category": spec.category,
        "horizon_days": spec.horizon_days,
        "runner_available": ar.resolve_runner(spec) is not None,
        "state": ar.runtime_state(name),
        "last_snapshot": snap,
    }


@router.post("/{name}/run")
async def force_run(
    name: str,
    request: Request,
    context: dict | None = None,
) -> dict[str, Any]:
    """Force-run an agent now. Bypasses schedule/event triggers."""
    spec = ar.get_agent(name)
    if spec is None:
        raise HTTPException(404, f"unknown agent: {name}")
    tenant = _tenant_from(request)
    if tenant is None:
        raise HTTPException(401, "no session — log in first")
    runner = ar.resolve_runner(spec)
    if runner is None:
        raise HTTPException(
            501,
            f"no backend runner registered for {name} — Claude Code only",
        )
    try:
        if asyncio.iscoroutinefunction(runner):
            snap = await runner(context or {})
        else:
            snap = await asyncio.to_thread(runner, context or {})
    except Exception as e:
        ar.mark_run(name, "error")
        raise HTTPException(500, f"runner failed: {e}")

    # Persist + record state
    try:
        skill_runner.write_snapshot(snap, tenant_id=tenant)
    except Exception:
        pass
    ar.mark_run(name, snap.get("status") or "ok")
    ar.mark_manual_run(name)
    return snap


@router.post("/{name}/enable")
async def enable_agent(
    name: str,
    enabled: bool = Query(True),
) -> dict[str, Any]:
    if ar.get_agent(name) is None:
        raise HTTPException(404, f"unknown agent: {name}")
    ar.set_enabled(name, enabled)
    return {"name": name, "enabled": enabled}


@router.post("/{name}/snooze")
async def snooze_agent(
    name: str,
    minutes: int = Query(60, ge=1, le=10080),
) -> dict[str, Any]:
    if ar.get_agent(name) is None:
        raise HTTPException(404, f"unknown agent: {name}")
    until_ts = time.time() + minutes * 60
    ar.set_snoozed_until(name, until_ts)
    return {"name": name, "snoozed_until_ts": until_ts, "minutes": minutes}


@router.post("/{name}/unsnooze")
async def unsnooze_agent(name: str) -> dict[str, Any]:
    if ar.get_agent(name) is None:
        raise HTTPException(404, f"unknown agent: {name}")
    ar.set_snoozed_until(name, None)
    return {"name": name, "snoozed_until_ts": None}


@router.get("/events/recent")
async def recent_events(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """Recent dispatched events from event_dispatcher.

    Used by dashboard live feed. Falls back to empty list if event log
    backend is unavailable.
    """
    try:
        from app.services import event_dispatcher

        events = getattr(event_dispatcher, "recent_events", None)
        if callable(events):
            return {"events": events(limit=limit)}
    except Exception:
        pass
    return {"events": []}


@router.get("/")
async def dashboard_summary(request: Request) -> dict[str, Any]:
    """Compact summary for dashboard top card.

    Counts: total / enabled / snoozed / recently-run.
    """
    agents = ar.list_agents()
    now = time.time()
    return {
        "total": len(agents),
        "enabled": sum(1 for a in agents if a.get("enabled")),
        "snoozed": sum(1 for a in agents if a.get("snoozed_until_ts") and a["snoozed_until_ts"] > now),
        "ran_last_24h": sum(1 for a in agents if a.get("last_run_ts") and (now - a["last_run_ts"]) < 86400),
        "by_trigger": {
            "cron": sum(1 for a in agents if a.get("trigger") == "cron"),
            "event": sum(1 for a in agents if a.get("trigger") == "event"),
            "manual": sum(1 for a in agents if a.get("trigger") == "manual"),
        },
        "by_category": {
            cat: sum(1 for a in agents if a.get("category") == cat) for cat in {a.get("category") for a in agents}
        },
    }
