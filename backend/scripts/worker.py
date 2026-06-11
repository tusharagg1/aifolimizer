"""RQ worker entry point (Phase 14).

Run:
  cd backend
  .venv\\Scripts\\python.exe scripts/worker.py

Or via docker-compose service `worker`.

Picks jobs from `default` + `nightly` queues. Single worker is enough
for solo use; scale by spawning N processes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `python scripts/worker.py` from backend root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs.queues import get_default, get_nightly, get_redis_sync  # noqa: E402
from app.security import configure_logging  # noqa: E402

configure_logging()
log = logging.getLogger("aifolimizer.worker")


def main() -> None:
    import os

    from rq import SimpleWorker, Worker

    # RQ's default Worker forks a work-horse per job; os.fork() is absent on
    # Windows. SimpleWorker runs jobs in-process (no fork) - fine for solo use.
    worker_cls = Worker if hasattr(os, "fork") else SimpleWorker

    r = get_redis_sync()
    if r is None:
        log.error("REDIS_URL not set; worker cannot start.")
        sys.exit(1)

    queues = [q for q in (get_default(), get_nightly()) if q is not None]
    if not queues:
        log.error("no queues available; worker cannot start.")
        sys.exit(1)

    log.info("worker starting (%s) on queues: %s", worker_cls.__name__, [q.name for q in queues])
    worker_cls(queues, connection=r).work()


if __name__ == "__main__":
    main()
