"""Asyncio scheduler for codified skill runs.

Cadence (US market reference — TZ-aware):
  Market hours (Mon-Fri 09:30-16:00 America/New_York): 15 min
  Off-hours weekday:                                    60 min
  Weekend:                                              360 min (6 h)

Picks the first active Wealthsimple session to source portfolio data. For
single-user mode this is sufficient; multi-user mode requires per-session
scheduling and namespaced snapshots — TODO.

Lifecycle hooked from app/main.py startup. Uses asyncio.create_task so it
never blocks the FastAPI event loop. Each tick is wrapped in try/except so
a transient failure does not kill the loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from app.services import paper_trade
from app.services import skill_runner
from app.services import wealthsimple


_LOG = logging.getLogger("aifolimizer.scheduler")

_TASK: asyncio.Task | None = None
_SCORE_TASK: asyncio.Task | None = None
_STOP_EVENT: asyncio.Event | None = None
_LAST_RUN_TS: float | None = None
_LAST_RUN_RESULT: dict | None = None
_LAST_SCORE_DATE: str | None = None
_LAST_SCORE_RESULT: dict | None = None

# Per-tenant scheduling: max parallel tenants per tick to bound load.
_MAX_TENANT_FANOUT = 5

# Nightly score: any tick at/after this Eastern-time hour triggers it once per day.
_SCORE_HOUR_ET = 16  # 4pm ET — 30 min post US close, captures end-of-day prices
_SCORE_LOOP_INTERVAL_S = 30 * 60  # check every 30 min whether to fire


def _now_eastern() -> datetime:
    # America/New_York is UTC-5 (standard) / UTC-4 (DST). Approximate via
    # zoneinfo to stay correct across DST boundaries.
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(tz=ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(tz=timezone.utc) - timedelta(hours=5)


def _is_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def _interval_seconds(now: datetime) -> int:
    if _is_market_hours(now):
        return 15 * 60
    if now.weekday() >= 5:
        return 360 * 60
    return 60 * 60


def _active_session_ids(limit: int = _MAX_TENANT_FANOUT) -> list[str]:
    """Return all known active session ids, capped at `limit`.

    wealthsimple._SESSIONS is in-process; if the server restarts the scheduler
    stays idle until users log in.
    """
    sessions = getattr(wealthsimple, "_SESSIONS", None) or {}
    out: list[str] = []
    for sid, sess in sessions.items():
        if sess and sess.get("access_token"):
            out.append(sid)
            if len(out) >= limit:
                break
    return out


async def _fetch_portfolio_for(sid: str):
    session = wealthsimple.get_session(sid)
    if not session:
        return None
    try:
        from app.api.ws import _get_portfolio
        return await _get_portfolio(sid, session, "", max_age_s=300)
    except Exception as e:
        _LOG.warning(
            "scheduler: _get_portfolio failed for sid=%s: %s",
            sid[:8], e,
        )
        return None


async def _run_for_session(sid: str) -> dict:
    portfolio = await _fetch_portfolio_for(sid)
    if portfolio is None:
        return {"tenant": sid[:8], "status": "no_portfolio"}
    try:
        out = await asyncio.to_thread(
            skill_runner.run_all_skills, portfolio, tenant_id=sid,
        )
        return {
            "tenant": sid[:8],
            "status": "ok",
            "skills": list(out.keys()),
            "errors": [
                s for s, snap in out.items()
                if snap.get("status") == "error"
            ],
        }
    except Exception as e:
        _LOG.exception("scheduler: per-session run failed")
        return {"tenant": sid[:8], "status": "error", "error": str(e)}


async def _tick() -> dict:
    global _LAST_RUN_TS, _LAST_RUN_RESULT
    sids = _active_session_ids()
    if not sids:
        result = {"status": "no_session", "ts": time.time()}
        _LAST_RUN_RESULT = result
        return result

    per_session = await asyncio.gather(
        *[_run_for_session(sid) for sid in sids],
        return_exceptions=False,
    )
    _LAST_RUN_TS = time.time()
    result = {
        "status": "ok",
        "ts": _LAST_RUN_TS,
        "n_tenants": len(sids),
        "results": per_session,
    }
    _LAST_RUN_RESULT = result
    return result


async def _loop():
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        await _tick()
        sleep_for = _interval_seconds(_now_eastern())
        try:
            await asyncio.wait_for(_STOP_EVENT.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            continue


async def _score_once_if_due() -> dict | None:
    """Run paper_trade.score_recommendations once per UTC date after market close.

    Runs independent of any active Wealthsimple session — open recs may exist
    even if no user is currently logged in. Idempotent: only fires the first
    eligible tick per day.
    """
    global _LAST_SCORE_DATE, _LAST_SCORE_RESULT
    now_et = _now_eastern()
    today = now_et.date().isoformat()
    if _LAST_SCORE_DATE == today:
        return None
    if now_et.weekday() >= 5:
        return None
    if now_et.hour < _SCORE_HOUR_ET:
        return None
    try:
        result = await asyncio.to_thread(paper_trade.score_recommendations)
        _LAST_SCORE_DATE = today
        _LAST_SCORE_RESULT = {"ts": time.time(), "summary": result}
        _LOG.info(
            "scheduler: nightly score complete — %s recs scored",
            (result or {}).get("total", "?"),
        )
        return _LAST_SCORE_RESULT
    except Exception as e:
        _LOG.exception("scheduler: nightly score failed")
        return {"status": "error", "error": str(e)}


async def _score_loop():
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        await _score_once_if_due()
        try:
            await asyncio.wait_for(
                _STOP_EVENT.wait(), timeout=_SCORE_LOOP_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            continue


def start_scheduler() -> None:
    """Spawn scheduler tasks. Idempotent — safe to call multiple times."""
    global _TASK, _SCORE_TASK, _STOP_EVENT
    if _TASK and not _TASK.done():
        return
    _STOP_EVENT = asyncio.Event()
    _TASK = asyncio.create_task(_loop(), name="skill-scheduler")
    _SCORE_TASK = asyncio.create_task(_score_loop(), name="rec-scorer")
    _LOG.info("scheduler: started (skill loop + nightly scorer)")


def stop_scheduler() -> None:
    if _STOP_EVENT:
        _STOP_EVENT.set()
    if _TASK:
        _TASK.cancel()
    if _SCORE_TASK:
        _SCORE_TASK.cancel()
    _LOG.info("scheduler: stopped")


def scheduler_status() -> dict:
    return {
        "running": bool(_TASK and not _TASK.done()),
        "score_running": bool(_SCORE_TASK and not _SCORE_TASK.done()),
        "last_run_ts": _LAST_RUN_TS,
        "last_run_result": _LAST_RUN_RESULT,
        "last_score_date": _LAST_SCORE_DATE,
        "last_score_result": _LAST_SCORE_RESULT,
        "next_interval_seconds": _interval_seconds(_now_eastern()),
        "is_market_hours": _is_market_hours(_now_eastern()),
    }


async def force_tick() -> dict:
    """Manual one-shot tick — used by REST /skills/refresh endpoint."""
    return await _tick()
