"""Top-level sync wrappers for RQ workers (Phase 14).

RQ serializes function references; the target must be importable by the
worker process. These wrappers convert async services into sync entry
points and return JSON-serializable result dicts.

Each task is idempotent at the day level (Phase 0+1 inserts are
ON CONFLICT DO NOTHING; Phase 5+ tasks gate on date markers).

Tasks log every run + outcome to Postgres via the standard repos so
operators can audit via /ops endpoints (Phase 14 surface).
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("aifolimizer.tasks")


def run_skill_tick_for_tenant(sid: str) -> dict:
    """Execute one scheduler tick for a single tenant.

    Wraps scheduler._run_for_session (which already persists snapshots +
    skill evidence to Postgres). Safe to retry — repo inserts are
    idempotent on (tenant_hash, symbol, ts).
    """
    try:
        from app.db import init_pool, close_pool
        from app.cache import init_redis, close_redis
        from app.jobs.scheduler import _run_for_session

        async def _go() -> dict:
            await init_pool()
            await init_redis()
            try:
                return await _run_for_session(sid)
            finally:
                await close_redis()
                await close_pool()

        return asyncio.run(_go())
    except Exception as e:
        log.exception("run_skill_tick_for_tenant failed (sid=%s): %s", sid[:8], e)
        raise


def run_nightly_scorer() -> dict:
    """Mark open recs to market, fill realized return horizons."""
    try:
        from app.services import paper_trade
        result = paper_trade.score_recommendations()
        return {"status": "ok", "summary": result}
    except Exception as e:
        log.exception("run_nightly_scorer failed: %s", e)
        raise


def run_alerts_for_tenant(sid: str) -> dict:
    """Evaluate alerts.py rules for one tenant + push via Telegram if triggered."""
    try:
        from app.services import alerts
        return alerts.run_for_session(sid) if hasattr(alerts, "run_for_session") \
            else {"status": "noop", "reason": "alerts.run_for_session not implemented"}
    except Exception as e:
        log.exception("run_alerts_for_tenant failed (sid=%s): %s", sid[:8], e)
        raise


# Phase 5/9/11/12/13 placeholders — keep import-safe so worker doesn't
# crash before those phases ship. Each replaced with real impl in its
# phase.

def run_weights_tuner() -> dict:
    """Phase 5+11: nightly weights tune.

    Phase 11 auto-selects objective: 'expectancy' once ≥20 horizon-scored
    samples exist for any sub-signal, else 'accuracy' as fallback.
    """
    try:
        from app.db import init_pool, close_pool
        from app.cache import init_redis, close_redis
        from app.services.weights_tuner import recalibrate

        async def _go() -> dict:
            await init_pool()
            await init_redis()
            try:
                return await recalibrate()  # auto-select objective
            finally:
                await close_redis()
                await close_pool()

        return asyncio.run(_go())
    except Exception as e:
        log.exception("run_weights_tuner failed: %s", e)
        raise


def run_calibration() -> dict:
    """Phase 9: compute Brier + ECE on logged win_prob vs realized outcomes."""
    try:
        from app.db import init_pool, close_pool
        from app.services.calibration import calibration_verdict

        async def _go() -> dict:
            await init_pool()
            try:
                return await calibration_verdict(horizon_days=21)
            finally:
                await close_pool()

        return asyncio.run(_go())
    except Exception as e:
        log.exception("run_calibration failed: %s", e)
        raise


def run_risk_gate(tenant_hash: str) -> dict:
    return {"status": "noop", "phase": "12_pending", "tenant": tenant_hash[:8]}


def run_discovery_scan(sid: str) -> dict:
    """Phase 13: nightly S&P500 + TSX60 + watchlist scan for top picks."""
    try:
        from app.db import init_pool, close_pool
        from app.cache import init_redis, close_redis
        from app.services import wealthsimple, discovery
        from app.api.ws import _get_portfolio
        import hashlib

        async def _go() -> dict:
            await init_pool()
            await init_redis()
            try:
                session = wealthsimple.get_session(sid)
                portfolio = None
                if session:
                    try:
                        portfolio = await _get_portfolio(
                            sid, session, "", max_age_s=300,
                        )
                    except Exception:
                        portfolio = None
                thash = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16]
                return await discovery.run_nightly_scan(thash, portfolio)
            finally:
                await close_redis()
                await close_pool()

        return asyncio.run(_go())
    except Exception as e:
        log.exception("run_discovery_scan failed: %s", e)
        raise


def run_llm_skills_for_tenant(sid: str) -> dict:
    """Phase 7: nightly LLM skills (adversarial / earnings-postmortem /
    stock-compare) for top-N holdings of the given session.

    Soft contributor — skipped silently if no LLM providers available.
    """
    try:
        from app.db import init_pool, close_pool
        from app.cache import init_redis, close_redis
        from app.services import wealthsimple, skill_llm_runner
        from app.api.ws import _get_portfolio
        import hashlib

        async def _go() -> dict:
            await init_pool()
            await init_redis()
            try:
                session = wealthsimple.get_session(sid)
                if not session:
                    return {"status": "skip", "reason": "no_session"}
                portfolio = await _get_portfolio(
                    sid, session, "", max_age_s=300,
                )
                if not portfolio or not portfolio.positions:
                    return {"status": "skip", "reason": "empty_portfolio"}
                top = sorted(
                    [
                        {
                            "symbol": p.symbol,
                            "weight": p.weight or 0.0,
                            "sector": getattr(p, "sector", "") or "",
                            "score": None,
                        }
                        for p in portfolio.positions
                        if getattr(p, "symbol", None)
                    ],
                    key=lambda x: -x["weight"],
                )
                thash = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16]
                return await skill_llm_runner.run_nightly_llm_skills(
                    thash, top,
                )
            finally:
                await close_redis()
                await close_pool()

        return asyncio.run(_go())
    except Exception as e:
        log.exception("run_llm_skills_for_tenant failed: %s", e)
        raise
