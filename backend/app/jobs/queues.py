"""RQ task queues (Phase 14).

Two queues:
  default: tick-frequency work (skill runs, alert eval, change detection)
  nightly: 4pm-ET batch work (recommendation scoring, weight tuning,
           calibration, discovery scans)

Connection uses the same Redis container as the rest of the stack
(REDIS_URL). RQ requires a sync `redis.Redis` client - the async client
in app.cache.redis_client is FastAPI-only.
"""

from __future__ import annotations

import logging
from typing import Optional

from redis import Redis
from rq import Queue

from app.core.config import settings

log = logging.getLogger(__name__)

_redis_sync: Optional[Redis] = None
default_q: Optional[Queue] = None
nightly_q: Optional[Queue] = None


def _ensure() -> None:
    global _redis_sync, default_q, nightly_q
    if default_q is not None:
        return
    if not settings.redis_url:
        log.warning("REDIS_URL not set; RQ disabled.")
        return
    _redis_sync = Redis.from_url(settings.redis_url)
    default_q = Queue("default", connection=_redis_sync)
    nightly_q = Queue("nightly", connection=_redis_sync)
    log.info("RQ queues ready (default, nightly)")


def get_default() -> Optional[Queue]:
    _ensure()
    return default_q


def get_nightly() -> Optional[Queue]:
    _ensure()
    return nightly_q


def get_redis_sync() -> Optional[Redis]:
    _ensure()
    return _redis_sync
