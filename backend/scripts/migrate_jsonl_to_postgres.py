"""One-shot migration: import existing .jsonl history into TimescaleDB.

Idempotent — relies on UNIQUE constraints + ON CONFLICT DO NOTHING.

Source files (all gitignored, may not exist on every machine):
  .claude/context/recommendations.jsonl       → recommendations
  .claude/context/scored_recommendations.jsonl → updates rec status/exit fields
  .claude/context/alerts.jsonl                → alerts
  .claude/context/crowding_history.jsonl      → crowding_history
  .claude/context/signal_history.jsonl        → signal_history

Usage:
  cd backend && .venv/Scripts/activate
  python scripts/migrate_jsonl_to_postgres.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running directly from backend/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_pool, close_pool, get_pool  # noqa: E402

log = logging.getLogger("migrate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
# JSONLs are written by services to backend/.claude/context/ (paper_trade.py uses parents[2]=backend).
# Fall back to repo-root/.claude/context/ for older deployments where files lived there.
_CANDIDATES = [BACKEND_ROOT / ".claude" / "context", REPO_ROOT / ".claude" / "context"]
CTX = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0])


def _parse_ts(val: Any) -> datetime:
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(float(val), tz=timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(tz=timezone.utc)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        log.info("skip (missing): %s", path)
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("bad line in %s: %s", path.name, e)
    log.info("read %d rows from %s", len(out), path.name)
    return out


def _default_tenant_hash() -> str:
    """Single-tenant migration: hash 'legacy' as a stable bootstrap tenant."""
    return hashlib.sha1(b"legacy").hexdigest()[:16]


async def _ensure_tenant(tenant_hash: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tenants (tenant_id, tenant_hash)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            "legacy",
            tenant_hash,
        )


async def import_recommendations(tenant_hash: str) -> int:
    rows = _read_jsonl(CTX / "recommendations.jsonl")
    if not rows:
        return 0
    pool = get_pool()
    count = 0
    async with pool.acquire() as conn:
        for r in rows:
            try:
                await conn.execute(
                    """
                    INSERT INTO recommendations (
                      tenant_hash, date, ts, skill, model_version, ticker,
                      action, conviction, horizon_days, thesis, invalidation,
                      entry_price, target_pct, stop_pct, expected_upside_pct,
                      expected_downside_pct, account, sector_etf, benchmark_symbol,
                      benchmarks_entry, features, rationale_hash, status,
                      exit_price, exit_date, return_pct, win
                    )
                    VALUES (
                      $1, $2, $3, $4, $5, $6,
                      $7, $8, $9, $10, $11,
                      $12, $13, $14, $15,
                      $16, $17, $18, $19,
                      $20, $21, $22, $23,
                      $24, $25, $26, $27
                    )
                    ON CONFLICT (tenant_hash, date, skill, ticker, action) DO NOTHING
                    """,
                    tenant_hash,
                    _parse_ts(r.get("date") or r.get("ts")).date(),
                    _parse_ts(r.get("ts") or r.get("date")),
                    r.get("skill", "legacy"),
                    r.get("model_version", "v1"),
                    r.get("ticker", ""),
                    r.get("action", ""),
                    r.get("conviction", ""),
                    r.get("horizon_days"),
                    r.get("thesis"),
                    r.get("invalidation"),
                    r.get("entry_price"),
                    r.get("target_pct"),
                    r.get("stop_pct"),
                    r.get("expected_upside_pct"),
                    r.get("expected_downside_pct"),
                    r.get("account"),
                    r.get("sector_etf"),
                    r.get("benchmark_symbol"),
                    json.dumps(r.get("benchmarks_entry")) if r.get("benchmarks_entry") is not None else None,
                    json.dumps(r.get("features")) if r.get("features") is not None else None,
                    r.get("rationale_hash"),
                    r.get("status", "open"),
                    r.get("exit_price"),
                    _parse_ts(r["exit_date"]).date() if r.get("exit_date") else None,
                    r.get("return_pct"),
                    r.get("win"),
                )
                count += 1
            except Exception as e:
                log.warning("rec insert failed (%s): %s", r.get("ticker"), e)
    log.info("recommendations imported: %d", count)
    return count


async def import_alerts(tenant_hash: str) -> int:
    rows = _read_jsonl(CTX / "alerts.jsonl")
    if not rows:
        return 0
    pool = get_pool()
    count = 0
    async with pool.acquire() as conn:
        for r in rows:
            try:
                await conn.execute(
                    """
                    INSERT INTO alerts (
                      tenant_hash, ts, rule, symbol, severity,
                      title, body, pushed, dedup_key
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    tenant_hash,
                    _parse_ts(r.get("ts")),
                    r.get("rule", "legacy"),
                    r.get("symbol"),
                    r.get("severity"),
                    r.get("title"),
                    r.get("body"),
                    bool(r.get("pushed", False)),
                    r.get("dedup_key"),
                )
                count += 1
            except Exception as e:
                log.warning("alert insert failed: %s", e)
    log.info("alerts imported: %d", count)
    return count


async def import_crowding() -> int:
    rows = _read_jsonl(CTX / "crowding_history.jsonl")
    if not rows:
        return 0
    pool = get_pool()
    count = 0
    async with pool.acquire() as conn:
        for r in rows:
            try:
                await conn.execute(
                    """
                    INSERT INTO crowding_history (ts, symbol, score, label)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (ts, symbol) DO NOTHING
                    """,
                    _parse_ts(r.get("date") or r.get("ts")),
                    r.get("symbol", ""),
                    float(r.get("score", 0)),
                    r.get("label", "neutral"),
                )
                count += 1
            except Exception as e:
                log.warning("crowding insert failed: %s", e)
    log.info("crowding_history imported: %d", count)
    return count


async def import_signal_history(tenant_hash: str) -> int:
    rows = _read_jsonl(CTX / "signal_history.jsonl")
    if not rows:
        return 0
    pool = get_pool()
    count = 0
    async with pool.acquire() as conn:
        for r in rows:
            try:
                features = r.get("features") or {}
                outcomes = r.get("outcomes") or {}

                def _ret(h: int) -> Any:
                    o = outcomes.get(f"h{h}") or {}
                    return o.get("ret_pct")

                await conn.execute(
                    """
                    INSERT INTO signal_history (
                      tenant_hash, symbol, ts, action, conviction, score,
                      tech_score, fund_score, macro_score, sentiment_score,
                      skill_consensus, skill_confidence, skill_evidence,
                      rsi, stage, market_regime, analyst_upside_pct, weight,
                      signal_quality, risk_reward, kelly_pct, win_prob,
                      earnings_risk, entry_price,
                      realized_return_1d, realized_return_3d, realized_return_5d,
                      realized_return_10d, realized_return_21d,
                      realized_return_42d, realized_return_63d
                    ) VALUES (
                      $1, $2, $3, $4, $5, $6,
                      $7, $8, $9, $10,
                      $11, $12, $13,
                      $14, $15, $16, $17, $18,
                      $19, $20, $21, $22,
                      $23, $24,
                      $25, $26, $27,
                      $28, $29,
                      $30, $31
                    )
                    ON CONFLICT (tenant_hash, symbol, ts) DO NOTHING
                    """,
                    tenant_hash,
                    r.get("symbol", ""),
                    _parse_ts(r.get("ts") or r.get("date")),
                    r.get("action", "HOLD"),
                    r.get("conviction"),
                    float(r.get("score", 0)),
                    features.get("tech_score"),
                    features.get("fund_score"),
                    features.get("macro_score"),
                    features.get("sentiment"),
                    features.get("skill_consensus"),
                    features.get("skill_confidence"),
                    json.dumps(features.get("skill_evidence")) if features.get("skill_evidence") is not None else None,
                    features.get("rsi"),
                    features.get("stage"),
                    features.get("market_regime"),
                    features.get("analyst_upside_pct"),
                    features.get("weight"),
                    features.get("signal_quality"),
                    features.get("risk_reward"),
                    features.get("kelly_pct"),
                    features.get("win_prob"),
                    features.get("earnings_risk"),
                    r.get("entry_price"),
                    _ret(1),
                    _ret(3),
                    _ret(5),
                    _ret(10),
                    _ret(21),
                    _ret(42),
                    _ret(63),
                )
                count += 1
            except Exception as e:
                log.warning("signal insert failed (%s): %s", r.get("symbol"), e)
    log.info("signal_history imported: %d", count)
    return count


async def main() -> None:
    pool = await init_pool()
    if pool is None:
        log.error("POSTGRES_DSN not set; aborting.")
        return
    try:
        tenant_hash = _default_tenant_hash()
        await _ensure_tenant(tenant_hash)
        log.info("tenant_hash: %s", tenant_hash)

        n_recs = await import_recommendations(tenant_hash)
        n_alerts = await import_alerts(tenant_hash)
        n_crowd = await import_crowding()
        n_sig = await import_signal_history(tenant_hash)

        log.info(
            "migration complete: recs=%d alerts=%d crowding=%d signals=%d",
            n_recs,
            n_alerts,
            n_crowd,
            n_sig,
        )
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
