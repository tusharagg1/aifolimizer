"""recommendations repository."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from app.db.pool import get_pool


async def insert(rec: dict[str, Any]) -> Optional[int]:
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO recommendations (
              tenant_hash, date, ts, skill, model_version, ticker, action, conviction,
              horizon_days, thesis, invalidation, entry_price, target_pct, stop_pct,
              expected_upside_pct, expected_downside_pct, account, sector_etf,
              benchmark_symbol, benchmarks_entry, features, rationale_hash, status
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, $8,
              $9, $10, $11, $12, $13, $14,
              $15, $16, $17, $18,
              $19, $20, $21, $22, $23
            )
            ON CONFLICT (tenant_hash, date, skill, ticker, action) DO NOTHING
            RETURNING id
            """,
            rec.get("tenant_hash", ""),
            rec.get("date") or date.today(),
            rec.get("ts") or datetime.utcnow(),
            rec.get("skill", ""),
            rec.get("model_version", "v1"),
            rec.get("ticker", ""),
            rec.get("action", ""),
            rec.get("conviction", ""),
            rec.get("horizon_days"),
            rec.get("thesis"),
            rec.get("invalidation"),
            rec.get("entry_price"),
            rec.get("target_pct"),
            rec.get("stop_pct"),
            rec.get("expected_upside_pct"),
            rec.get("expected_downside_pct"),
            rec.get("account"),
            rec.get("sector_etf"),
            rec.get("benchmark_symbol"),
            json.dumps(rec.get("benchmarks_entry")) if rec.get("benchmarks_entry") is not None else None,
            json.dumps(rec.get("features")) if rec.get("features") is not None else None,
            rec.get("rationale_hash"),
            rec.get("status", "open"),
        )
    return row["id"] if row else None


async def open_recs(tenant_hash: Optional[str] = None) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if tenant_hash:
            rows = await conn.fetch(
                "SELECT * FROM recommendations WHERE status = 'open' AND tenant_hash = $1",
                tenant_hash,
            )
        else:
            rows = await conn.fetch("SELECT * FROM recommendations WHERE status = 'open'")
    return [dict(r) for r in rows]


async def mark_closed(
    rec_id: int, *, exit_price: float, exit_date: date, return_pct: float, win: bool, status: str
) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE recommendations
            SET status = $1, exit_price = $2, exit_date = $3,
                return_pct = $4, win = $5
            WHERE id = $6
            """,
            status,
            exit_price,
            exit_date,
            return_pct,
            win,
            rec_id,
        )


async def track_record_windows(windows_days: list[int], tenant_hash: Optional[str] = None) -> dict[int, dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return {}
    out: dict[int, dict[str, Any]] = {}
    async with pool.acquire() as conn:
        for w in windows_days:
            if tenant_hash:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS n,
                           AVG(return_pct) AS avg_return_pct,
                           AVG(CASE WHEN win THEN 1.0 ELSE 0.0 END) AS win_rate
                    FROM recommendations
                    WHERE tenant_hash = $1
                      AND status <> 'open'
                      AND exit_date > current_date - ($2::TEXT || ' days')::INTERVAL
                    """,
                    tenant_hash,
                    str(w),
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS n,
                           AVG(return_pct) AS avg_return_pct,
                           AVG(CASE WHEN win THEN 1.0 ELSE 0.0 END) AS win_rate
                    FROM recommendations
                    WHERE status <> 'open'
                      AND exit_date > current_date - ($1::TEXT || ' days')::INTERVAL
                    """,
                    str(w),
                )
            out[w] = {
                "n": int(row["n"] or 0),
                "avg_return_pct": float(row["avg_return_pct"] or 0),
                "win_rate": float(row["win_rate"] or 0),
            }
    return out
