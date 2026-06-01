"""One-shot smoke test: imports + DB/Redis connect.

Run:  python tests/smoke_phase0.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_pool, close_pool  # noqa: E402
from app.cache import init_redis, close_redis  # noqa: E402
from app.services import skill_evidence  # noqa: E402, F401


async def main() -> None:
    p = await init_pool()
    r = await init_redis()
    print("pool:", p is not None, "redis:", r is not None)
    if p is not None:
        async with p.acquire() as conn:
            n = await conn.fetchval("SELECT COUNT(*) FROM tenants")
            print("tenants:", n)
            wv = await conn.fetchval("SELECT MAX(version) FROM weights")
            print("weights_latest_version:", wv)
    if r is not None:
        pong = await r.ping()
        print("redis_ping:", pong)
    await close_redis()
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
