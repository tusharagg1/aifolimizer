"""crowding_history repository."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.pool import get_pool


async def upsert_day(ts: datetime, symbol: str, score: float, label: str) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO crowding_history (ts, symbol, score, label)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (ts, symbol) DO UPDATE
              SET score = EXCLUDED.score, label = EXCLUDED.label
            """,
            ts, symbol, float(score), label,
        )


async def series_for_symbols(
    symbols: list[str], days: int = 30
) -> dict[str, list[dict[str, Any]]]:
    pool = get_pool()
    if pool is None or not symbols:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, symbol, score, label
            FROM crowding_history
            WHERE symbol = ANY($1::TEXT[])
              AND ts > now() - ($2::TEXT || ' days')::INTERVAL
            ORDER BY symbol, ts ASC
            """,
            symbols, str(days),
        )
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    for r in rows:
        out.setdefault(r["symbol"], []).append(dict(r))
    return out
