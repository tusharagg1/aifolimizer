"""asyncpg connection pool for TimescaleDB.

Initialized at FastAPI startup, closed at shutdown.
DSN from POSTGRES_DSN env var.
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from app.core.config import settings

_pool: Optional[asyncpg.Pool] = None
log = logging.getLogger(__name__)


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    dsn = settings.postgres_dsn
    if not dsn:
        log.warning("POSTGRES_DSN not set; DB-backed features disabled.")
        return None  # type: ignore[return-value]
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    log.info("Postgres pool initialized (%s)", dsn.split("@")[-1])
    return _pool


def get_pool() -> Optional[asyncpg.Pool]:
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("Postgres pool closed.")
