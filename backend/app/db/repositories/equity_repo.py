"""portfolio_equity repository."""

from __future__ import annotations

from datetime import date as date_t
from typing import Any, Optional

from app.db.pool import get_pool


async def upsert_day(
    tenant_hash: str,
    dt: date_t,
    total_value_cad: float,
    cash_cad: Optional[float] = None,
) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO portfolio_equity (tenant_hash, date, total_value_cad, cash_cad)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (tenant_hash, date) DO UPDATE
              SET total_value_cad = EXCLUDED.total_value_cad,
                  cash_cad = EXCLUDED.cash_cad
            """,
            tenant_hash,
            dt,
            float(total_value_cad),
            float(cash_cad) if cash_cad is not None else None,
        )


async def series(tenant_hash: str, days: int = 90) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, total_value_cad, cash_cad
            FROM portfolio_equity
            WHERE tenant_hash = $1
              AND date > current_date - ($2::TEXT || ' days')::INTERVAL
            ORDER BY date ASC
            """,
            tenant_hash,
            str(days),
        )
    return [dict(r) for r in rows]
