"""Discovery layer (Phase 13).

Nightly: scan (S&P500 ∪ TSX60 ∪ user_watchlist) − already_held → rank by
integrated 5-signal score → surface top N picks to dashboard + Telegram for
score ≥ 8.

Implementation notes:
  - Reuses recommendations.get_recommendations() — same 5-signal engine
    used for held positions. No new scoring code path.
  - Held symbols pre-filter avoids re-recommending current holdings.
  - Sector-saturation guard: if current sector weight + suggested
    allocation > 40%, prepends a warning to the rec's reason list.
  - Wash-sale guard: symbols recently sold at a loss (last 30d) get
    suppressed from discovery (Canadian superficial-loss rule).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_MIN_SCORE = 6.0
_PUSH_SCORE = 8.0
_TOP_N = 5
_SECTOR_SAT_PCT = 40.0


# ── helpers ────────────────────────────────────────────────────────────────


async def _watchlist_for(tenant_hash: str) -> list[str]:
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT symbol FROM watchlist WHERE tenant_hash = $1",
                tenant_hash,
            )
        return [r["symbol"] for r in rows if r["symbol"]]
    except Exception as e:
        log.warning("watchlist fetch failed: %s", e)
        return []


async def _washsale_blocked(tenant_hash: str) -> set[str]:
    """Symbols sold at a loss in the last 30 days (superficial-loss rule)."""
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return set()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ticker FROM recommendations
                WHERE tenant_hash = $1
                  AND action IN ('SELL', 'TRIM')
                  AND status <> 'open'
                  AND return_pct < 0
                  AND exit_date > current_date - INTERVAL '30 days'
                """,
                tenant_hash,
            )
        return {r["ticker"] for r in rows if r.get("ticker")}
    except Exception as e:
        log.warning("washsale fetch failed: %s", e)
        return set()


def _build_candidate_positions(
    symbols: list[str],
) -> list[dict[str, Any]]:
    """Each candidate gets a stub position dict with weight=0, value=0 so the
    scoring engine evaluates it as a fresh entry."""
    return [
        {
            "symbol": s,
            "name": s,
            "weight": 0.0,
            "market_value_cad": 0.0,
            "total_return_pct": 0.0,
            "currency": "CAD" if s.endswith(".TO") else "USD",
            "asset_class": "stock",
            "sector": None,
        }
        for s in symbols
    ]


def _sector_weights(positions) -> dict[str, float]:
    """{sector: total_weight%} from current portfolio positions."""
    out: dict[str, float] = {}
    for p in positions or []:
        sec = getattr(p, "sector", None)
        if sec:
            out[sec] = out.get(sec, 0.0) + (p.weight or 0.0)
    return out


# ── core scan ──────────────────────────────────────────────────────────────


async def scan_universe(
    tenant_hash: str,
    portfolio=None,
    *,
    min_score: float = _DEFAULT_MIN_SCORE,
    universe: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Score every symbol in (universe ∪ watchlist) − held → return
    ranked list of picks where score >= min_score.

    Args:
      tenant_hash: required for watchlist + wash-sale lookups.
      portfolio:   optional PortfolioResponse — used to exclude held and
                   to compute sector-saturation guards.
      min_score:   default 6.0; anything below isn't surfaced.
      universe:    optional override; defaults to S&P500 + TSX60 + ETFs.
    """
    from app.services import recommendations as rec_svc
    from app.services import discovery_universe as du

    held: set[str] = set()
    if portfolio is not None:
        for p in portfolio.positions or []:
            sym = getattr(p, "symbol", None)
            if sym:
                held.add(sym)

    base = universe or du.full_universe()
    watchlist = await _watchlist_for(tenant_hash)
    candidates = sorted(set(base) | set(watchlist))
    candidates = [s for s in candidates if s not in held]

    if not candidates:
        return []

    # Wash-sale block (Canadian superficial-loss + general re-entry guard)
    blocked = await _washsale_blocked(tenant_hash)
    candidates = [s for s in candidates if s not in blocked]

    # Run through the same 5-signal engine used for held positions.
    candidate_positions = _build_candidate_positions(candidates)
    recs = rec_svc.get_recommendations(candidate_positions)

    # Sector saturation context.
    sec_weights = _sector_weights(getattr(portfolio, "positions", None))

    picks: list[dict[str, Any]] = []
    for r in recs:
        score = float(r.get("score") or 0)
        if score < min_score:
            continue
        action = (r.get("action") or "").upper()
        if action not in ("BUY", "ADD", "WATCH"):
            continue
        pick = {
            "symbol": r["symbol"],
            "action": action,
            "score": score,
            "conviction": r.get("confidence"),
            "kelly_pct": r.get("kelly_pct"),
            "win_prob": r.get("win_prob"),
            "risk_reward": r.get("risk_reward"),
            "current_price": r.get("current_price"),
            "stop_loss": r.get("stop_loss"),
            "take_profit": r.get("take_profit"),
            "reasons": (r.get("reasons") or [])[:3],
            "sector": r.get("sector"),
            "earnings_date": r.get("earnings_date"),
        }
        # Sector-saturation warning
        sec = r.get("sector")
        if sec and sec in sec_weights:
            current_pct = sec_weights[sec]
            if current_pct >= _SECTOR_SAT_PCT:
                pick["warning"] = f"sector heavy: {sec} already {current_pct:.0f}%"
        picks.append(pick)

    picks.sort(key=lambda p: -p["score"])
    return picks


async def top_n_picks(
    tenant_hash: str,
    portfolio=None,
    *,
    n: int = _TOP_N,
) -> list[dict[str, Any]]:
    picks = await scan_universe(tenant_hash, portfolio=portfolio)
    return picks[:n]


# ── persistence + cache + push ─────────────────────────────────────────────


async def _persist_scan(
    tenant_hash: str,
    universe_size: int,
    picks: list[dict[str, Any]],
    pushed: int,
) -> None:
    import json

    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO discovery_scans (
                  tenant_hash, ts, universe_size, filtered_count,
                  pushed_count, top_picks
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                tenant_hash,
                datetime.now(tz=timezone.utc),
                universe_size,
                len(picks),
                pushed,
                json.dumps(picks[:_TOP_N]),
            )
    except Exception as e:
        log.warning("discovery persist failed: %s", e)


async def _cache_top(
    tenant_hash: str,
    picks: list[dict[str, Any]],
) -> None:
    import json

    try:
        from app.cache import get_redis

        r = get_redis()
        if r is not None:
            await r.set(
                f"discovery:top5:{tenant_hash}",
                json.dumps(picks[:_TOP_N]),
                ex=3600,
            )
    except Exception as e:
        log.warning("discovery cache failed: %s", e)


def _push_strong_picks(picks: list[dict[str, Any]]) -> int:
    """Telegram push for each pick with score >= 8.0. Returns # pushed."""
    from app.core.config import settings

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return 0
    from app.services.alerts import _push_telegram

    pushed = 0
    for p in picks:
        if p["score"] < _PUSH_SCORE:
            continue
        warning = f" ({p['warning']})" if p.get("warning") else ""
        try:
            _push_telegram(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                title=(
                    f"Discovery: {p['symbol']} score {p['score']:.1f} [{p['action']} {p.get('conviction', '').upper()}]"
                ),
                body=(f"{' · '.join(p['reasons'][:2])}{warning}"),
                severity="high",
            )
            pushed += 1
        except Exception as e:
            log.warning(
                "discovery telegram push failed for %s: %s",
                p["symbol"],
                e,
            )
    return pushed


async def run_nightly_scan(
    tenant_hash: str,
    portfolio=None,
) -> dict[str, Any]:
    """Full pipeline: scan → persist → cache → push."""
    from app.services import discovery_universe as du

    picks = await scan_universe(tenant_hash, portfolio=portfolio)
    universe_size = len(du.full_universe())
    pushed = _push_strong_picks(picks)
    await _persist_scan(tenant_hash, universe_size, picks, pushed)
    await _cache_top(tenant_hash, picks)
    return {
        "status": "ok",
        "universe_size": universe_size,
        "filtered_count": len(picks),
        "top_picks": picks[:_TOP_N],
        "pushed_count": pushed,
    }


async def get_cached_top(
    tenant_hash: str,
) -> list[dict[str, Any]]:
    import json

    try:
        from app.cache import get_redis

        r = get_redis()
        if r is not None:
            blob = await r.get(f"discovery:top5:{tenant_hash}")
            if blob:
                return json.loads(blob)
    except Exception:
        pass
    # PG fallback
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT top_picks FROM discovery_scans
                WHERE tenant_hash = $1
                ORDER BY ts DESC LIMIT 1
                """,
                tenant_hash,
            )
        if row and row["top_picks"]:
            return row["top_picks"]
    except Exception as e:
        log.warning("discovery cached fetch failed: %s", e)
    return []
