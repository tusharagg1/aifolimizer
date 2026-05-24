"""weights repository (5 sub-signal weights, audit-versioned)."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.db.pool import get_pool

DEFAULT_WEIGHTS = {
    "w_tech": 1.0, "w_fund": 1.0, "w_macro": 1.0,
    "w_sentiment": 1.0, "w_skill": 0.0,
}


async def current() -> dict[str, Any]:
    pool = get_pool()
    if pool is None:
        return {"version": 0, **DEFAULT_WEIGHTS}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM weights ORDER BY version DESC LIMIT 1"
        )
    if not row:
        return {"version": 0, **DEFAULT_WEIGHTS}
    return dict(row)


async def insert_version(
    weights: dict[str, float],
    reason: str = "manual",
    objective: str = "baseline",
    attribution: Optional[dict] = None,
) -> int:
    pool = get_pool()
    if pool is None:
        return 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO weights (
              w_tech, w_fund, w_macro, w_sentiment, w_skill,
              reason, objective, attribution
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING version
            """,
            float(weights.get("w_tech", 1.0)),
            float(weights.get("w_fund", 1.0)),
            float(weights.get("w_macro", 1.0)),
            float(weights.get("w_sentiment", 1.0)),
            float(weights.get("w_skill", 0.0)),
            reason, objective,
            json.dumps(attribution) if attribution else None,
        )
    return int(row["version"]) if row else 0


async def history(limit: int = 30) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM weights ORDER BY version DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]
