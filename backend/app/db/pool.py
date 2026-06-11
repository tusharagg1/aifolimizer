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
_migrated: bool = False
log = logging.getLogger(__name__)

# Additive, idempotent schema migrations applied at startup so already-
# initialized containers (whose data dir skips docker-entrypoint-initdb.d)
# pick up new columns. Mirror any change here into schema.sql for fresh DBs.
_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS entry_price         NUMERIC",
    "ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS realized_return_3d  NUMERIC",
    "ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS realized_return_10d NUMERIC",
    "ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS realized_return_42d NUMERIC",
)


async def _apply_migrations(pool: asyncpg.Pool) -> None:
    global _migrated
    if _migrated:
        return
    async with pool.acquire() as conn:
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(stmt)
            except Exception as exc:  # never block startup on a migration
                log.warning("migration skipped (%s): %s", stmt.split("ADD COLUMN")[-1].strip(), exc)
    _migrated = True


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
    await _apply_migrations(_pool)
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
