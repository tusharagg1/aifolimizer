"""signal_changes repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db.pool import get_pool


async def insert(
    tenant_hash: str,
    symbol: str,
    ts: datetime,
    prev_action: Optional[str],
    new_action: str,
    prev_conviction: Optional[str],
    new_conviction: Optional[str],
    prev_score: Optional[float],
    new_score: float,
    reasons: list[str],
    pushed: bool,
    dedup_key: str,
) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO signal_changes (
              tenant_hash, symbol, ts,
              prev_action, new_action, prev_conviction, new_conviction,
              prev_score, new_score, reasons, pushed, push_dedup_key
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
            )
            ON CONFLICT (tenant_hash, symbol, ts) DO NOTHING
            """,
            tenant_hash,
            symbol,
            ts,
            prev_action,
            new_action,
            prev_conviction,
            new_conviction,
            prev_score,
            new_score,
            reasons,
            pushed,
            dedup_key,
        )


async def dedup_exists(dedup_key: str) -> bool:
    pool = get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM signal_changes WHERE push_dedup_key = $1 LIMIT 1",
            dedup_key,
        )
    return row is not None


async def recent(tenant_hash: str, hours: int = 24) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM signal_changes
            WHERE tenant_hash = $1
              AND ts > now() - ($2::TEXT || ' hours')::INTERVAL
            ORDER BY ts DESC
            """,
            tenant_hash,
            str(hours),
        )
    return [dict(r) for r in rows]
