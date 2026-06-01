"""skill_snapshots repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.db.pool import get_pool


async def upsert(tenant_hash: str, skill: str, snapshot: dict[str, Any]) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO skill_snapshots (
              tenant_hash, skill, computed_at, expires_at, status,
              ttl_minutes, summary, actionable, alerts, key_insights, error
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (tenant_hash, skill, computed_at) DO NOTHING
            """,
            tenant_hash,
            skill,
            snapshot.get("computed_at") or datetime.utcnow(),
            snapshot.get("expires_at") or datetime.utcnow(),
            snapshot.get("status", "ok"),
            snapshot.get("ttl_minutes"),
            json.dumps(snapshot.get("summary")) if snapshot.get("summary") is not None else None,
            json.dumps(snapshot.get("actionable")) if snapshot.get("actionable") is not None else None,
            json.dumps(snapshot.get("alerts")) if snapshot.get("alerts") is not None else None,
            json.dumps(snapshot.get("key_insights")) if snapshot.get("key_insights") is not None else None,
            snapshot.get("error"),
        )


async def latest(tenant_hash: str, skill: str) -> Optional[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM skill_snapshots
            WHERE tenant_hash = $1 AND skill = $2
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            tenant_hash,
            skill,
        )
    return dict(row) if row else None


async def list_all_latest(tenant_hash: str) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (skill) *
            FROM skill_snapshots
            WHERE tenant_hash = $1
            ORDER BY skill, computed_at DESC
            """,
            tenant_hash,
        )
    return [dict(r) for r in rows]


async def archive_older_than(days: int) -> int:
    pool = get_pool()
    if pool is None:
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM skill_snapshots
            WHERE computed_at < now() - ($1::TEXT || ' days')::INTERVAL
            """,
            str(days),
        )
    # result like 'DELETE 42'
    try:
        return int(result.split()[-1])
    except Exception:
        return 0
