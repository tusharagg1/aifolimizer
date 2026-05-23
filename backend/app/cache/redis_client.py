"""Redis async client.

Key conventions (documented in plan v4.2):
  signals:{tenant_hash}              JSON of latest integrated signals (TTL 1h)
  last_signals:{tenant_hash}         JSON of prev signals for change detector (no TTL)
  weights:current                    JSON of active weights
  cache:fundamentals:{symbol}        JSON, TTL 6h
  cache:technicals:{symbol}          JSON, TTL 1h
  cache:news:{symbol}                JSON, TTL 30m
  regime:current                     JSON of latest regime classification, TTL 1h
  discovery:top5:{tenant_hash}       JSON of nightly top-5 discovery picks

Pub/sub channels:
  events:signal_change               When scheduler detects a signal flip
  events:weights_updated             When weights tuner writes a new version
"""
from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings

_client: Optional[aioredis.Redis] = None
log = logging.getLogger(__name__)


async def init_redis() -> Optional[aioredis.Redis]:
    global _client
    if _client is not None:
        return _client
    url = settings.redis_url
    if not url:
        log.warning("REDIS_URL not set; Redis-backed features disabled.")
        return None
    _client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        await _client.ping()
        log.info("Redis connected (%s)", url)
    except Exception as exc:
        log.warning("Redis ping failed (%s): %s", url, exc)
        _client = None
    return _client


def get_redis() -> Optional[aioredis.Redis]:
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("Redis closed.")
