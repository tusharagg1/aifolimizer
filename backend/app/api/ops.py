"""Operator endpoints (Phase 14): RQ queue + job visibility.

Read-only / safe-retry only. No destructive operations from the UI.
Mount under /ops in main.py.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.jobs.queues import get_default, get_nightly, get_redis_sync

router = APIRouter()
log = logging.getLogger(__name__)


def _queue_stats(queue) -> dict[str, int]:
    if queue is None:
        return {"queued": 0, "started": 0, "finished": 0, "failed": 0, "deferred": 0}
    return {
        "queued": queue.count,
        "started": queue.started_job_registry.count,
        "finished": queue.finished_job_registry.count,
        "failed": queue.failed_job_registry.count,
        "deferred": queue.deferred_job_registry.count,
    }


@router.get("/queues")
async def queues() -> dict[str, Any]:
    return {
        "default": _queue_stats(get_default()),
        "nightly": _queue_stats(get_nightly()),
    }


@router.get("/jobs/failed")
async def failed_jobs(limit: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in (get_default(), get_nightly()):
        if q is None:
            continue
        reg = q.failed_job_registry
        ids = reg.get_job_ids(0, max(1, limit) - 1)
        for jid in ids:
            from rq.job import Job

            try:
                job = Job.fetch(jid, connection=get_redis_sync())
                out.append(
                    {
                        "id": job.id,
                        "queue": q.name,
                        "func_name": job.func_name,
                        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
                        "failed_at": job.ended_at.isoformat() if job.ended_at else None,
                        "exc_short": (job.exc_info or "").splitlines()[-1] if job.exc_info else None,
                    }
                )
            except Exception as e:
                log.warning("failed to fetch job %s: %s", jid, e)
    return out[:limit]


@router.get("/sentry/digest")
async def sentry_digest(limit: int = 10, force: bool = False) -> dict[str, Any]:
    """Latest Sentry issues digest. `force=true` triggers a live fetch
    instead of returning the cached scheduler value."""
    if force:
        from app.services import sentry_monitor

        try:
            return sentry_monitor.build_digest(limit=limit)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
    from app.jobs.scheduler import get_last_sentry_digest

    cached = get_last_sentry_digest()
    if cached is None:
        return {"count": 0, "issues": [], "cached": True, "note": "no digest yet"}
    return {**cached, "cached": True}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> dict[str, Any]:
    from rq.job import Job

    r = get_redis_sync()
    if r is None:
        raise HTTPException(503, "Redis not available")
    try:
        job = Job.fetch(job_id, connection=r)
    except Exception as e:
        raise HTTPException(404, f"job not found: {e}")
    try:
        job.requeue()
        return {"status": "requeued", "job_id": job_id}
    except Exception as e:
        raise HTTPException(500, f"requeue failed: {e}")
