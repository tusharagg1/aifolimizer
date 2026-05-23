"""alerts repository."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db.pool import get_pool


async def insert(alert: dict[str, Any]) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alerts (
              tenant_hash, ts, rule, symbol, severity,
              title, body, pushed, dedup_key
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            alert.get("tenant_hash"),
            alert.get("ts") or datetime.utcnow(),
            alert.get("rule", ""), alert.get("symbol"),
            alert.get("severity"), alert.get("title"),
            alert.get("body"), alert.get("pushed", False),
            alert.get("dedup_key"),
        )


async def recent(tenant_hash: Optional[str], hours: int = 24) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if tenant_hash:
            rows = await conn.fetch(
                """
                SELECT * FROM alerts
                WHERE tenant_hash = $1
                  AND ts > now() - ($2::TEXT || ' hours')::INTERVAL
                ORDER BY ts DESC
                """,
                tenant_hash, str(hours),
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM alerts
                WHERE ts > now() - ($1::TEXT || ' hours')::INTERVAL
                ORDER BY ts DESC
                """,
                str(hours),
            )
    return [dict(r) for r in rows]
