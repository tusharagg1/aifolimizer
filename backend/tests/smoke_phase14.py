"""Smoke test Phase 14 (RQ workers) + Phase 15 (Sentry init).

Run:  python tests/smoke_phase14.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.sentry import init_sentry  # noqa: E402
from app.jobs.queues import get_default, get_redis_sync  # noqa: E402
from app.jobs.tasks import run_weights_tuner  # noqa: E402


def main() -> None:
    sentry_on = init_sentry(settings)
    print("sentry_enabled:", sentry_on)

    q = get_default()
    r = get_redis_sync()
    if q is None or r is None:
        print("FAIL: RQ queue unavailable; check REDIS_URL")
        sys.exit(1)
    print("queue:", q.name, "redis_ping:", r.ping())

    # run_weights_tuner is a Phase-5 placeholder that returns a noop dict
    # — importable from app.jobs.tasks so RQ can serialize it.
    job = q.enqueue(run_weights_tuner)
    print("enqueued job:", job.id)
    print(
        "queue_stats:",
        {
            "queued": q.count,
            "started": q.started_job_registry.count,
            "finished": q.finished_job_registry.count,
            "failed": q.failed_job_registry.count,
        },
    )

    # Drain queue manually with one burst worker so the smoke test is self-
    # contained (don't rely on a separate worker process).
    from rq import SimpleWorker
    w = SimpleWorker([q], connection=r)
    w.work(burst=True, with_scheduler=False)

    from rq.job import Job
    j = Job.fetch(job.id, connection=r)
    print("job_status:", j.get_status(), "result:", j.result)
    assert isinstance(j.result, dict) and j.result.get("status") == "noop", \
        f"expected noop dict got {j.result!r}"
    print("OK")


if __name__ == "__main__":
    main()
