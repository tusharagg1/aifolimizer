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

from app.services import skill_runner
from app.services import wealthsimple


_LOG = logging.getLogger("aifolimizer.scheduler")

_TASK: asyncio.Task | None = None
_STOP_EVENT: asyncio.Event | None = None
_LAST_RUN_TS: float | None = None
_LAST_RUN_RESULT: dict | None = None


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


def _first_active_session_id() -> str | None:
    """Return the first known active session id, or None.

    wealthsimple._SESSIONS is a dict[str, dict] in-process. We never persist
    it — if the server restarts, scheduler stays idle until user logs in.
    """
    sessions = getattr(wealthsimple, "_SESSIONS", None) or {}
    for sid, sess in sessions.items():
        if sess and sess.get("access_token"):
            return sid
    return None


async def _fetch_portfolio():
    sid = _first_active_session_id()
    if not sid:
        return None
    session = wealthsimple.get_session(sid)
    if not session:
        return None
    # Reuse the API helper so caching + locks behave identically.
    try:
        from app.api.ws import _get_portfolio
        return await _get_portfolio(sid, session, "", max_age_s=300)
    except Exception as e:
        _LOG.warning("scheduler: _get_portfolio failed: %s", e)
        return None


async def _tick() -> dict:
    global _LAST_RUN_TS, _LAST_RUN_RESULT
    portfolio = await _fetch_portfolio()
    if portfolio is None:
        result = {"status": "no_session", "ts": time.time()}
        _LAST_RUN_RESULT = result
        return result

    try:
        out = await asyncio.to_thread(skill_runner.run_all_skills, portfolio)
        _LAST_RUN_TS = time.time()
        result = {
            "status": "ok",
            "ts": _LAST_RUN_TS,
            "skills": list(out.keys()),
            "errors": [
                s for s, snap in out.items() if snap.get("status") == "error"
            ],
        }
        _LAST_RUN_RESULT = result
        return result
    except Exception as e:
        _LOG.exception("scheduler: skill run failed")
        result = {"status": "error", "error": str(e), "ts": time.time()}
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


def start_scheduler() -> None:
    """Spawn the scheduler task. Idempotent — safe to call multiple times."""
    global _TASK, _STOP_EVENT
    if _TASK and not _TASK.done():
        return
    _STOP_EVENT = asyncio.Event()
    _TASK = asyncio.create_task(_loop(), name="skill-scheduler")
    _LOG.info("scheduler: started")


def stop_scheduler() -> None:
    if _STOP_EVENT:
        _STOP_EVENT.set()
    if _TASK:
        _TASK.cancel()
    _LOG.info("scheduler: stopped")


def scheduler_status() -> dict:
    return {
        "running": bool(_TASK and not _TASK.done()),
        "last_run_ts": _LAST_RUN_TS,
        "last_run_result": _LAST_RUN_RESULT,
        "next_interval_seconds": _interval_seconds(_now_eastern()),
        "is_market_hours": _is_market_hours(_now_eastern()),
    }


async def force_tick() -> dict:
    """Manual one-shot tick — used by REST /skills/refresh endpoint."""
    return await _tick()
