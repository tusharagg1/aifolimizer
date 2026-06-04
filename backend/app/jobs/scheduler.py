"""Asyncio scheduler for codified skill runs.

Cadence (US market reference — TZ-aware):
  Market hours (Mon-Fri 09:30-16:00 America/New_York): 15 min
  Off-hours weekday:                                    60 min
  Weekend:                                              360 min (6 h)

Picks the first active Wealthsimple session to source portfolio data. For
single-user mode this is sufficient; multi-user mode requires per-session
scheduling and namespaced snapshots — TODO.

Lifecycle hooked from app/main.py startup. Uses asyncio.create_task so it
never blocks the FastAPI event loop. Each tick is wrapped in try/except so
a transient failure does not kill the loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path as _Path

from app.services import paper_trade
from app.services import skill_runner
from app.services import skill_evidence
from app.services import wealthsimple
from app.db.repositories import signals_repo, snapshots_repo


_LOG = logging.getLogger("aifolimizer.scheduler")

_TASK: asyncio.Task | None = None
_SCORE_TASK: asyncio.Task | None = None
_SENTRY_TASK: asyncio.Task | None = None
_REGISTRY_TASK: asyncio.Task | None = None
_STOP_EVENT: asyncio.Event | None = None
_REGISTRY_FIRED: set[tuple[str, str]] = set()  # (agent_name, YYYYMMDD-HHMM)
_REGISTRY_LOOP_INTERVAL_S = 60
_LAST_RUN_TS: float | None = None
_LAST_RUN_RESULT: dict | None = None
_LAST_SCORE_DATE: str | None = None
_LAST_SCORE_RESULT: dict | None = None
_LAST_SENTRY_TS: float | None = None
_LAST_SENTRY_DIGEST: dict | None = None

# Persisted state - survives restart so a host that was asleep / down at the
# usual run hour catches up on wake instead of waiting for the next normal
# tick. Path is gitignored alongside other runtime caches.
_STATE_DIR = _Path(__file__).resolve().parents[2] / ".cache"
_STATE_FILE = _STATE_DIR / "scheduler_state.json"


def _load_scheduler_state() -> None:
    """Hydrate `_LAST_*` globals from disk on process start."""
    global _LAST_SCORE_DATE, _LAST_SENTRY_TS
    try:
        if not _STATE_FILE.is_file():
            return
        import json

        payload = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            d = payload.get("last_score_date")
            if isinstance(d, str):
                _LAST_SCORE_DATE = d
            ts = payload.get("last_sentry_ts")
            if isinstance(ts, (int, float)):
                _LAST_SENTRY_TS = float(ts)
    except Exception as e:
        _LOG.warning("scheduler: load state failed: %s", e)


def _save_scheduler_state() -> None:
    """Persist the run-once-per-day cursor + Sentry timestamp."""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        import json

        _STATE_FILE.write_text(
            json.dumps(
                {
                    "last_score_date": _LAST_SCORE_DATE,
                    "last_sentry_ts": _LAST_SENTRY_TS,
                    "saved_ts": time.time(),
                }
            ),
            encoding="utf-8",
        )
    except Exception as e:
        _LOG.warning("scheduler: save state failed: %s", e)


def _last_due_score_date() -> str:
    """Return the most recent ET weekday whose post-close window has passed.

    A run is "due" once the clock crosses 4pm ET on a weekday. If today is
    pre-close or a weekend, the most recent due date is the prior weekday.
    """
    et_now = _now_eastern()
    candidate = et_now.date()
    # Step back until we land on a weekday whose 4pm ET has already passed.
    while True:
        is_weekday = candidate.weekday() < 5
        crossed_close = candidate < et_now.date() or (candidate == et_now.date() and et_now.hour >= _SCORE_HOUR_ET)
        if is_weekday and crossed_close:
            return candidate.isoformat()
        candidate = candidate - timedelta(days=1)


async def catch_up_missed_runs() -> dict | None:
    """Fire the nightly score immediately if it's overdue at startup.

    Without this, a machine that was asleep / off at 4pm ET would wait
    until the next 4pm ET to score yesterday's signals, leaving a one-day
    gap in the audit chain.
    """
    last = _LAST_SCORE_DATE
    due = _last_due_score_date()
    if last == due:
        return None
    _LOG.info(
        "scheduler: catch-up — last_score=%s due=%s, firing now",
        last,
        due,
    )
    return await _score_once_if_due(force=True)


# Sentry digest — hourly poll for live errors.
_SENTRY_LOOP_INTERVAL_S = 60 * 60

# Per-tenant scheduling: max parallel tenants per tick to bound load.
_MAX_TENANT_FANOUT = 5

# Phase 6: dedup Telegram session-expired pushes per (tenant_hash, date).
# In-process — fine because the scheduler is a single process.
_SESSION_EXPIRED_PUSHED: set[tuple[str, str]] = set()

# Phase 15: event_dispatcher state — track last seen regime composite and
# per-tenant risk-gate status so material flips can fire LLM skill re-runs.
_PREV_REGIME = None
_PREV_GATE_STATUS: dict[str, str] = {}

# Nightly score: any tick at/after this Eastern-time hour triggers it once per day.
_SCORE_HOUR_ET = 16  # 4pm ET — 30 min post US close, captures end-of-day prices
_SCORE_LOOP_INTERVAL_S = 30 * 60  # check every 30 min whether to fire


def _now_eastern() -> datetime:
    # America/New_York is UTC-5 (standard) / UTC-4 (DST). Approximate via
    # zoneinfo to stay correct across DST boundaries.
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(tz=ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(tz=timezone.utc) - timedelta(hours=5)


def _is_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def _interval_seconds(now: datetime) -> int:
    if _is_market_hours(now):
        return 15 * 60
    if now.weekday() >= 5:
        return 360 * 60
    return 60 * 60


def _active_session_ids(limit: int = _MAX_TENANT_FANOUT) -> list[str]:
    """Return all known active session ids, capped at `limit`.

    wealthsimple._sessions is in-process; if the server restarts the scheduler
    stays idle until users log in.
    """
    sessions = getattr(wealthsimple, "_sessions", None) or {}
    out: list[str] = []
    for sid, sess in sessions.items():
        if sess and sess.get("state") == "authed":
            out.append(sid)
            if len(out) >= limit:
                break
    return out


async def _fetch_portfolio_for(sid: str):
    session = wealthsimple.get_session(sid)
    if not session:
        return None
    try:
        from app.api.ws import _get_portfolio

        return await _get_portfolio(sid, session, "", max_age_s=300)
    except Exception as e:
        _LOG.warning(
            "scheduler: _get_portfolio failed for sid=%s: %s",
            sid[:8],
            e,
        )
        return None


def _tenant_hash(sid: str) -> str:
    import hashlib

    return hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16]


def _build_evidence_map(
    portfolio,
    snapshots: dict[str, dict],
    regime_composite: str | None = None,
) -> dict[str, dict]:
    """Phase 1+ / Phase 8: build per-symbol skill evidence map, optionally
    regime-gated. Pure function — no DB writes.
    """
    symbols = [p.symbol for p in portfolio.positions if getattr(p, "symbol", None)]
    return skill_evidence.build(
        snapshots,
        symbols,
        regime_composite=regime_composite,
    )


async def _persist_integrated_signals(tenant_hash: str, recs: list[dict], evidence_map: dict[str, dict]) -> int:
    """Phase 2: write one signal_history row per holding using the integrated
    score + 5-signal breakdown + skill evidence. Replaces the Phase 1
    EVIDENCE_ONLY placeholders.
    """
    from datetime import datetime, timezone

    ts = datetime.now(tz=timezone.utc)
    written = 0
    for rec in recs or []:
        sym = rec.get("symbol")
        if not sym:
            continue
        ev = evidence_map.get(sym) or {}
        try:
            await signals_repo.insert_signal(
                tenant_hash=tenant_hash,
                symbol=sym,
                ts=ts,
                action=rec.get("action") or "HOLD",
                conviction=rec.get("confidence"),
                score=float(rec.get("score") or 0),
                tech_score=rec.get("tech_score"),
                fund_score=rec.get("fund_score"),
                macro_score=rec.get("macro_score"),
                sentiment_score=rec.get("sentiment"),
                skill_consensus=int(ev.get("skill_consensus") or 0),
                skill_confidence=float(ev.get("skill_confidence") or 0),
                skill_evidence={k: v for k, v in ev.items() if k not in {"skill_consensus", "skill_confidence"}},
                features={
                    "rsi": rec.get("rsi"),
                    "stage": rec.get("stage"),
                    "market_regime": rec.get("market_regime"),
                    "analyst_upside_pct": rec.get("analyst_upside_pct"),
                    "weight": rec.get("weight"),
                    "signal_quality": rec.get("signal_quality"),
                    "risk_reward": rec.get("risk_reward"),
                    "kelly_pct": rec.get("kelly_pct"),
                    "win_prob": rec.get("win_prob"),
                    "earnings_risk": rec.get("earnings_risk"),
                    "evidence_tier": rec.get("evidence_tier"),
                },
            )
            written += 1
        except Exception as e:
            _LOG.warning("integrated signal insert failed for %s: %s", sym, e)
    return written


async def _persist_snapshots(tenant_hash: str, snapshots: dict[str, dict]) -> None:
    """Mirror disk snapshots to skill_snapshots table for queryable history."""
    for skill, snap in (snapshots or {}).items():
        try:
            await snapshots_repo.upsert(tenant_hash, skill, snap)
        except Exception as e:
            _LOG.warning("snapshot persist failed for %s: %s", skill, e)


async def _handle_session_expired(sid: str) -> None:
    """Phase 6: when scheduler discovers a dead WS session:
    - push one Telegram message per tenant per day ("Wealthsimple session expired")
    - write a session_expired snapshot row per portfolio-dependent skill so
      the dashboard banner has something to read.
    """
    from datetime import date as date_t

    thash = _tenant_hash(sid)
    today = date_t.today().isoformat()
    dedup_key = (thash, today)

    if dedup_key in _SESSION_EXPIRED_PUSHED:
        return

    # 1. Mark snapshots so frontend shows session-expired banner.
    portfolio_dependent_skills = (
        "portfolio-health",
        "risk-assessment",
        "cash-deployment",
        "stock-analysis",
        "earnings-analyzer",
        "tax-loss-review",
        "dividend-strategy",
        "sector-rotation",
        "daily-briefing",
    )
    expired_snapshot = {
        "computed_at": datetime.now(tz=timezone.utc),
        "expires_at": datetime.now(tz=timezone.utc),
        "status": "session_expired",
        "ttl_minutes": 60,
        "error": "ws_session_expired",
        "summary": {"reason": "Wealthsimple session expired"},
        "actionable": [],
        "alerts": [],
        "key_insights": [],
    }
    for skill_name in portfolio_dependent_skills:
        try:
            await snapshots_repo.upsert(thash, skill_name, expired_snapshot)
        except Exception as e:
            _LOG.warning(
                "session_expired snapshot insert failed for %s: %s",
                skill_name,
                e,
            )

    # 2. Telegram push (deduped per day per tenant).
    try:
        from app.core.config import settings as _cfg

        if _cfg.telegram_bot_token and _cfg.telegram_chat_id:
            from app.services.alerts import _push_telegram

            _push_telegram(
                _cfg.telegram_bot_token,
                _cfg.telegram_chat_id,
                title="Wealthsimple session expired",
                body="Reopen Claude and re-enter MFA to resume monitoring.",
                severity="high",
            )
    except Exception as e:
        _LOG.warning("session_expired telegram push failed: %s", e)

    _SESSION_EXPIRED_PUSHED.add(dedup_key)


async def _run_for_session(sid: str) -> dict:
    global _PREV_REGIME
    # Phase 8: refresh market regime once per tick (cheap, cached 1h
    # inside market_breadth so back-to-back ticks reuse the same data).
    try:
        from app.services import market_regime

        new_regime = await market_regime.classify_and_persist()
        # Phase 15: event-driven regime flip → fire LLM skills out-of-band.
        try:
            from app.services import event_dispatcher

            await event_dispatcher.on_regime_flip(
                _PREV_REGIME,
                new_regime,
                tenant_hashes=[_tenant_hash(sid)],
            )
        except Exception as e:
            _LOG.warning("regime flip dispatch failed: %s", e)
        _PREV_REGIME = new_regime
    except Exception as e:
        _LOG.warning("regime classify failed: %s", e)

    portfolio = await _fetch_portfolio_for(sid)
    if portfolio is None:
        # Phase 6: Telegram push + snapshot tagging.
        try:
            await _handle_session_expired(sid)
        except Exception as e:
            _LOG.warning("session expired handler failed: %s", e)
        return {"tenant": sid[:8], "status": "session_expired"}
    try:
        # 1. Run codified skills (parallel, ThreadPool inside run_all_skills).
        out = await asyncio.to_thread(
            skill_runner.run_all_skills,
            portfolio,
            tenant_id=sid,
        )
        thash = _tenant_hash(sid)

        # 2. Mirror snapshots to PG (Phase 0).
        await _persist_snapshots(thash, out)

        # 3. Build per-symbol skill evidence, regime-gated (Phase 1 + 8).
        regime_composite = None
        try:
            from app.services import market_regime

            current = await market_regime.get_current()
            if current:
                regime_composite = current.composite
        except Exception as e:
            _LOG.warning("regime fetch failed: %s", e)
        evidence_map = _build_evidence_map(
            portfolio,
            out,
            regime_composite=regime_composite,
        )

        # 4. Re-score portfolio with skill evidence as 5th sub-signal (Phase 2,
        #    w_skill=0.5 from Postgres weights). Cache key inside
        #    get_recommendations includes evidence digest.
        from app.services import recommendations as rec_svc

        positions = [
            {
                "symbol": p.symbol,
                "name": p.name,
                "weight": p.weight,
                "market_value_cad": p.market_value_cad,
                "total_return_pct": p.total_return_pct,
                "currency": p.currency,
                "asset_class": p.asset_class,
                "sector": p.sector,
            }
            for p in portfolio.positions
        ]
        recs = await asyncio.to_thread(
            rec_svc.get_recommendations,
            positions,
            evidence_map,
        )

        # 5. Persist integrated signals (per-holding 5-signal row).
        n_signals = await _persist_integrated_signals(thash, recs, evidence_map)

        # 5b. Phase 12: risk gate evaluation + suppression of BUYs on halt.
        try:
            from app.services import risk_gate

            gate = await risk_gate.evaluate_and_persist(thash)
            # Phase 15: event-driven drawdown breach → fire risk LLM skill.
            try:
                from app.services import event_dispatcher

                prev_status = _PREV_GATE_STATUS.get(thash)
                await event_dispatcher.on_drawdown_breach(
                    thash,
                    prev_status,
                    gate.status,
                    context={"reasons": gate.reasons, "triggers": gate.triggers},
                )
                _PREV_GATE_STATUS[thash] = gate.status
            except Exception as e:
                _LOG.warning("drawdown breach dispatch failed: %s", e)
            if gate.status == "halt":
                recs = [r for r in recs if (r.get("action") or "").upper() not in ("BUY", "ADD")]
            elif gate.status == "reduce_size" and gate.size_multiplier:
                for r in recs:
                    if (r.get("action") or "").upper() in ("BUY", "ADD"):
                        if r.get("kelly_pct"):
                            r["kelly_pct"] = round(
                                r["kelly_pct"] * gate.size_multiplier,
                                1,
                            )
        except Exception as e:
            _LOG.warning("risk_gate evaluate failed: %s", e)

        # 6. Phase 4: detect material flips vs last tick → Telegram + Postgres log.
        change_stats = {"detected": 0, "pushed": 0, "deduped": 0}
        try:
            from app.services import signal_change_detector
            from app.core.config import settings as _cfg

            # Pass topic only if configured — detector silently no-ops on push
            # but still records detected count + updates last_signals snapshot.
            change_stats = await signal_change_detector.detect_and_dispatch(
                thash,
                recs,
                telegram_bot_token=(_cfg.telegram_bot_token or None),
                telegram_chat_id=(_cfg.telegram_chat_id or None),
            )
        except Exception as e:
            _LOG.warning("change detector failed: %s", e)

        return {
            "tenant": sid[:8],
            "status": "ok",
            "skills": list(out.keys()),
            "errors": [s for s, snap in out.items() if snap.get("status") == "error"],
            "signal_rows": n_signals,
            "evidence_symbols": len(evidence_map),
            "changes": change_stats,
        }
    except Exception as e:
        _LOG.exception("scheduler: per-session run failed")
        return {"tenant": sid[:8], "status": "error", "error": str(e)}


async def _tick() -> dict:
    """Phase 14: enqueue per-tenant work to RQ instead of running inline.

    Falls back to inline execution if RQ/Redis unavailable so dev still works
    without docker running.
    """
    global _LAST_RUN_TS, _LAST_RUN_RESULT
    sids = _active_session_ids()
    if not sids:
        result = {"status": "no_session", "ts": time.time()}
        _LAST_RUN_RESULT = result
        return result

    try:
        from app.jobs.queues import get_default
        from app.jobs.tasks import run_skill_tick_for_tenant
        from rq import Retry

        q = get_default()
        if q is not None:
            enqueued = []
            for sid in sids:
                job = q.enqueue(
                    run_skill_tick_for_tenant,
                    sid,
                    job_timeout=600,
                    retry=Retry(max=3, interval=[60, 180, 600]),
                    result_ttl=3600,
                    failure_ttl=86400,
                )
                enqueued.append({"tenant": sid[:8], "job_id": job.id})
            _LAST_RUN_TS = time.time()
            result = {
                "status": "enqueued",
                "ts": _LAST_RUN_TS,
                "n_tenants": len(sids),
                "jobs": enqueued,
            }
            _LAST_RUN_RESULT = result
            return result
    except Exception as e:
        _LOG.warning("RQ enqueue failed, falling back to inline: %s", e)

    # Inline fallback (legacy path).
    per_session = await asyncio.gather(
        *[_run_for_session(sid) for sid in sids],
        return_exceptions=False,
    )
    _LAST_RUN_TS = time.time()
    result = {
        "status": "ok",
        "ts": _LAST_RUN_TS,
        "n_tenants": len(sids),
        "results": per_session,
    }
    _LAST_RUN_RESULT = result
    return result


async def _loop():
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        await _tick()
        sleep_for = _interval_seconds(_now_eastern())
        try:
            await asyncio.wait_for(_STOP_EVENT.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            continue


async def _score_once_if_due(force: bool = False) -> dict | None:
    """Run paper_trade.score_recommendations once per UTC date after market close.

    Runs independent of any active Wealthsimple session — open recs may exist
    even if no user is currently logged in. Idempotent: only fires the first
    eligible tick per day.

    `force=True` bypasses the time-of-day + weekday guards. Callers (e.g.
    `catch_up_missed_runs` on host wakeup / restart) use this to fill a
    missed slot the moment the process is back, rather than waiting for the
    next 4pm ET tick. The same-date dedupe guard still applies, so calling
    it twice in a row is a no-op.
    """
    global _LAST_SCORE_DATE, _LAST_SCORE_RESULT
    now_et = _now_eastern()
    today = now_et.date().isoformat()
    if _LAST_SCORE_DATE == today:
        return None
    if not force:
        if now_et.weekday() >= 5:
            return None
        if now_et.hour < _SCORE_HOUR_ET:
            return None
    try:
        result = await asyncio.to_thread(paper_trade.score_recommendations)
        _LAST_SCORE_DATE = today
        _LAST_SCORE_RESULT = {"ts": time.time(), "summary": result}
        _LOG.info(
            "scheduler: nightly score complete — %s recs scored",
            (result or {}).get("total", "?"),
        )

        # Phase 13: nightly discovery scan (S&P500 + TSX60 + watchlist).
        try:
            from app.services import discovery
            from app.api.ws import _get_portfolio as _gp_disc
            import hashlib as _h2

            disco_results: list[dict] = []
            for sid in _active_session_ids():
                session = wealthsimple.get_session(sid)
                portfolio = None
                if session:
                    try:
                        portfolio = await _gp_disc(
                            sid,
                            session,
                            "",
                            max_age_s=300,
                        )
                    except Exception:
                        portfolio = None
                thash = _h2.sha1(sid.encode("utf-8")).hexdigest()[:16]
                try:
                    disco_results.append(
                        {
                            "tenant": sid[:8],
                            "result": await discovery.run_nightly_scan(
                                thash,
                                portfolio,
                            ),
                        }
                    )
                except Exception as e:
                    _LOG.warning("discovery scan failed: %s", e)
            _LAST_SCORE_RESULT["discovery"] = disco_results
        except Exception as e:
            _LOG.warning("nightly discovery loop failed: %s", e)

        # Phase 7: run LLM skills for top holdings of each active session
        # BEFORE tuner so evidence is fresh.
        try:
            from app.services import skill_llm_runner
            from app.api.ws import _get_portfolio as _gp_llm
            import hashlib

            llm_results: list[dict] = []
            for sid in _active_session_ids():
                session = wealthsimple.get_session(sid)
                if not session:
                    continue
                portfolio = await _gp_llm(
                    sid,
                    session,
                    "",
                    max_age_s=300,
                )
                if not portfolio or not portfolio.positions:
                    continue
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
                llm_result = await skill_llm_runner.run_nightly_llm_skills(
                    thash,
                    top,
                )
                llm_results.append({"tenant": sid[:8], "result": llm_result})
            _LAST_SCORE_RESULT["llm_skills"] = llm_results
        except Exception as e:
            _LOG.warning("nightly LLM skills failed: %s", e)

        # Phase 15: event-driven LLM skills for recent earnings surprises +
        # crowding regime shifts. Runs out-of-band from fixed scheduler so
        # material market events trigger fresh LLM reasoning immediately.
        try:
            from app.services import (
                event_dispatcher,
                fundamentals,
                positioning,
            )
            from app.api.ws import _get_portfolio as _gp_evt
            import hashlib as _h_evt
            from datetime import datetime as _dt, timedelta as _td

            event_results: list[dict] = []
            recent_cutoff = (_dt.utcnow() - _td(days=7)).date().isoformat()

            for sid in _active_session_ids():
                session = wealthsimple.get_session(sid)
                if not session:
                    continue
                try:
                    portfolio = await _gp_evt(
                        sid,
                        session,
                        "",
                        max_age_s=300,
                    )
                except Exception:
                    continue
                if not portfolio or not portfolio.positions:
                    continue

                thash = _h_evt.sha1(sid.encode("utf-8")).hexdigest()[:16]
                symbols = [p.symbol for p in portfolio.positions if getattr(p, "symbol", None)]
                if not symbols:
                    continue

                # --- Earnings surprises (last 7d, latest quarter only) ---
                try:
                    history = await asyncio.to_thread(
                        fundamentals.get_earnings_history,
                        symbols,
                        1,
                    )
                    for sym, quarters in (history or {}).items():
                        if not quarters:
                            continue
                        latest = quarters[0]
                        q_date = (latest.get("quarter") or "")[:10]
                        if q_date < recent_cutoff:
                            continue
                        surprise = latest.get("surprise_pct")
                        if surprise is None:
                            continue
                        result = await event_dispatcher.on_earnings_surprise(
                            thash,
                            sym,
                            float(surprise),
                            context={
                                "earnings_date": q_date,
                                "eps_actual": latest.get("eps_actual"),
                                "eps_estimate": latest.get("eps_estimate"),
                                "outcome": latest.get("outcome"),
                            },
                        )
                        if result.get("status") == "ok":
                            event_results.append(
                                {
                                    "tenant": sid[:8],
                                    "event": "earnings_surprise",
                                    "ticker": sym,
                                    "surprise_pct": surprise,
                                }
                            )
                except Exception as e:
                    _LOG.warning("earnings event scan failed: %s", e)

                # --- Crowding regime shifts ---
                try:
                    top_syms = [
                        p.symbol
                        for p in sorted(
                            portfolio.positions,
                            key=lambda p: p.weight or 0,
                            reverse=True,
                        )[:15]
                        if getattr(p, "symbol", None)
                    ]
                    await asyncio.to_thread(
                        positioning.snapshot_to_history,
                        top_syms,
                    )
                    shifts = await asyncio.to_thread(
                        positioning.detect_regime_shifts,
                        top_syms,
                        30,
                        25.0,
                    )
                    for shift in shifts or []:
                        sym = shift.get("symbol")
                        if not sym:
                            continue
                        result = await event_dispatcher.on_crowding_flip(
                            thash,
                            sym,
                            float(shift.get("from_score") or 0),
                            float(shift.get("to_score") or 0),
                            context={
                                "from_label": shift.get("from_label"),
                                "to_label": shift.get("to_label"),
                                "direction": shift.get("direction"),
                            },
                        )
                        if result.get("status") == "ok":
                            event_results.append(
                                {
                                    "tenant": sid[:8],
                                    "event": "crowding_flip",
                                    "ticker": sym,
                                    "delta": shift.get("delta"),
                                }
                            )
                except Exception as e:
                    _LOG.warning("crowding event scan failed: %s", e)

            _LAST_SCORE_RESULT["event_triggers"] = event_results
        except Exception as e:
            _LOG.warning("event-driven skills loop failed: %s", e)

        # Phase 10: snapshot live KPIs (PF, Sharpe, DD) for each active tenant.
        try:
            from app.services import live_metrics
            import hashlib as _h

            kpi_results: list[dict] = []
            for sid in _active_session_ids():
                thash = _h.sha1(sid.encode("utf-8")).hexdigest()[:16]
                for window in (7, 30, 90):
                    try:
                        snap = await live_metrics.kpis(thash, window_days=window)
                        kpi_results.append(
                            {
                                "tenant": sid[:8],
                                "window": window,
                                "pf": snap.get("profit_factor"),
                                "sharpe": snap.get("sharpe"),
                                "n": snap.get("n_trades"),
                            }
                        )
                    except Exception as e:
                        _LOG.warning(
                            "kpis window=%s failed: %s",
                            window,
                            e,
                        )
            _LAST_SCORE_RESULT["live_kpis"] = kpi_results
        except Exception as e:
            _LOG.warning("live KPI snapshot failed: %s", e)

        # Self-improvement loop, Step 1: score every directional signal at
        # the standard 7 forward horizons. Without this nightly call, the
        # decay-curve / accuracy / source-attribution endpoints stay empty.
        try:
            from app.services import signal_history as signal_history_svc

            score_horizons_result = await asyncio.to_thread(
                signal_history_svc.score_horizons,
                signal_history_svc._DEFAULT_HORIZONS,
            )
            _LAST_SCORE_RESULT["signal_horizons"] = score_horizons_result
            _LOG.info(
                "scheduler: signal horizons scored — %s rows",
                (score_horizons_result or {}).get("scored_new", "?"),
            )
        except Exception as e:
            _LOG.warning("signal horizons score failed: %s", e)

        # Step 2: resolve open trade decisions to target_hit / stop_hit /
        # expired so the reflection loop has fresh outcomes to inject into
        # tomorrow's analyses.
        try:
            from app.services import decision_memory
            from app.services import data_router as _dr

            open_decisions = decision_memory.get_open_decisions()
            symbols = sorted({d.get("ticker") for d in open_decisions if d.get("ticker")})
            price_map: dict[str, float] = {}
            if symbols:
                quotes = await asyncio.to_thread(_dr.get_quotes_batch, symbols)
                for sym, q in (quotes or {}).items():
                    p = q.get("price") if isinstance(q, dict) else None
                    if p is not None:
                        price_map[sym] = float(p)
            outcomes_result = await asyncio.to_thread(decision_memory.resolve_outcomes, price_map)
            _LAST_SCORE_RESULT["decision_outcomes"] = outcomes_result
            _LOG.info(
                "scheduler: decision outcomes — %s",
                outcomes_result,
            )
        except Exception as e:
            _LOG.warning("decision outcome resolution failed: %s", e)

        # Phase 9: compute calibration on logged probs vs realized outcomes.
        # Loop over multiple horizons so 5d/63d skills aren't measured by
        # a single 21d snapshot.
        try:
            from app.services.calibration import calibration_verdict

            cal_results: dict[int, dict] = {}
            for horizon in (5, 21, 63):
                try:
                    cal_results[horizon] = await calibration_verdict(horizon_days=horizon)
                except Exception as e:
                    _LOG.warning("calibration horizon=%s failed: %s", horizon, e)
            primary = cal_results.get(21) or {}
            _LAST_SCORE_RESULT["calibration"] = {
                "brier": primary.get("brier_score"),
                "ece": primary.get("ece"),
                "verdict": primary.get("verdict"),
                "n": primary.get("n_samples"),
                "by_horizon": {
                    str(h): {
                        "brier": v.get("brier_score"),
                        "ece": v.get("ece"),
                        "verdict": v.get("verdict"),
                        "n": v.get("n_samples"),
                    }
                    for h, v in cal_results.items()
                },
            }
            _LOG.info(
                "scheduler: calibration (h=21) — Brier=%s ECE=%s verdict=%s",
                primary.get("brier_score"),
                primary.get("ece"),
                primary.get("verdict"),
            )
        except Exception as e:
            _LOG.warning("calibration failed: %s", e)

        # Phase 5: tune weights after scoring (requires fresh realized returns).
        try:
            # Phase 11: omit objective → auto-select accuracy/expectancy
            # depending on how much horizon-scored data has accumulated.
            from app.services.weights_tuner import recalibrate

            tuner_result = await recalibrate()
            _LAST_SCORE_RESULT["tuner"] = tuner_result
            _LOG.info(
                "scheduler: weights tuner result — %s",
                tuner_result.get("status"),
            )
        except Exception as e:
            _LOG.warning("weights tuner failed: %s", e)

        # Phase 11b: learn (skill, regime) multipliers from realized data.
        try:
            from app.services.adaptive_regime import recalibrate_multipliers

            adaptive = await recalibrate_multipliers()
            _LAST_SCORE_RESULT["adaptive_regime"] = adaptive
        except Exception as e:
            _LOG.warning("adaptive regime recalibration failed: %s", e)

        # Threshold tuner — promote calibrated buy/sell thresholds.
        try:
            from app.services.threshold_tuner import recalibrate as thr_recal

            thr_result = await asyncio.to_thread(thr_recal)
            _LAST_SCORE_RESULT["threshold_tuner"] = thr_result
        except Exception as e:
            _LOG.warning("threshold tuner failed: %s", e)

        # Skill-health gate (live forward-tested hit-rate / profit-factor).
        try:
            from app.services.skill_health import enforce as skill_health_enforce

            health = await asyncio.to_thread(skill_health_enforce)
            _LAST_SCORE_RESULT["skill_health"] = health
        except Exception as e:
            _LOG.warning("skill health gate failed: %s", e)

        # Backtest gate (deflated Sharpe over walk-forward runs).
        try:
            from app.services.backtest_gate import enforce_dsr_gate

            bt_gate = await asyncio.to_thread(enforce_dsr_gate)
            _LAST_SCORE_RESULT["backtest_gate"] = bt_gate
        except Exception as e:
            _LOG.warning("backtest gate failed: %s", e)

        # Source drift detector (data-router auto-rerank).
        try:
            from app.services.source_drift import detect_and_demote

            drift = await asyncio.to_thread(detect_and_demote)
            _LAST_SCORE_RESULT["source_drift"] = drift
        except Exception as e:
            _LOG.warning("source drift failed: %s", e)

        _save_scheduler_state()
        return _LAST_SCORE_RESULT
    except Exception as e:
        _LOG.exception("scheduler: nightly score failed")
        return {"status": "error", "error": str(e)}


async def _score_loop():
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        await _score_once_if_due()
        try:
            await asyncio.wait_for(
                _STOP_EVENT.wait(),
                timeout=_SCORE_LOOP_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            continue


async def _sentry_digest_once() -> dict | None:
    """Pull latest Sentry digest; log high-severity issues."""
    global _LAST_SENTRY_TS, _LAST_SENTRY_DIGEST
    from app.core.config import settings

    if not settings.sentry_auth_token or not settings.sentry_org:
        return None
    try:
        from app.services import sentry_monitor

        digest = await asyncio.to_thread(sentry_monitor.build_digest, 10)
        _LAST_SENTRY_TS = time.time()
        _LAST_SENTRY_DIGEST = digest
        if digest.get("count", 0) > 0:
            _LOG.warning(
                "scheduler: sentry digest — %s unresolved issues (top: %s)",
                digest["count"],
                digest["issues"][0].get("short_id"),
            )
        return digest
    except Exception:
        _LOG.exception("scheduler: sentry digest failed")
        return None


async def _sentry_loop():
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        await _sentry_digest_once()
        try:
            await asyncio.wait_for(
                _STOP_EVENT.wait(),
                timeout=_SENTRY_LOOP_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            continue


def get_last_sentry_digest() -> dict | None:
    return _LAST_SENTRY_DIGEST


async def _registry_cron_tick() -> dict:
    """Fire any agent_registry cron-due agents whose backend runner resolves.

    Runs once per minute. Per-minute dedup via _REGISTRY_FIRED.
    Single-user mode: uses first active session as tenant.
    """
    from app.services import agent_registry as ar
    from app.db.repositories import snapshots_repo

    now = datetime.now(tz=timezone.utc)
    minute_key = now.strftime("%Y%m%d-%H%M")
    due = ar.cron_due_agents(now=now)
    if not due:
        return {"status": "idle", "minute": minute_key, "due": 0}

    sids = _active_session_ids(limit=1)
    if not sids:
        return {"status": "no_session", "minute": minute_key}
    sid = sids[0]
    thash = _tenant_hash(sid)

    fired = []
    for spec in due:
        key = (spec.name, minute_key)
        if key in _REGISTRY_FIRED:
            continue
        runner = ar.resolve_runner(spec)
        if runner is None:
            continue
        try:
            ctx = {"tenant_hash": thash, "session_id": sid}
            if asyncio.iscoroutinefunction(runner):
                snap = await runner(ctx)
            else:
                snap = await asyncio.to_thread(runner, ctx)
            try:
                await snapshots_repo.upsert(thash, snap["skill"], snap)
            except Exception as e:
                _LOG.warning(
                    "registry snapshot persist failed %s: %s",
                    spec.name,
                    e,
                )
            ar.mark_run(spec.name, snap.get("status") or "ok")
            _REGISTRY_FIRED.add(key)
            fired.append(
                {
                    "agent": spec.name,
                    "status": snap.get("status"),
                }
            )
        except Exception as e:
            _LOG.warning("registry agent %s failed: %s", spec.name, e)
            ar.mark_run(spec.name, "error")

    # Garbage-collect dedup set so it doesn't grow forever
    if len(_REGISTRY_FIRED) > 5000:
        cutoff_key = (now - timedelta(hours=2)).strftime("%Y%m%d-%H%M")
        _REGISTRY_FIRED.intersection_update({k for k in _REGISTRY_FIRED if k[1] >= cutoff_key})

    return {"status": "ok", "minute": minute_key, "fired": fired}


async def _registry_cron_loop():
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        try:
            await _registry_cron_tick()
        except Exception:
            _LOG.exception("registry cron tick failed")
        try:
            await asyncio.wait_for(
                _STOP_EVENT.wait(),
                timeout=_REGISTRY_LOOP_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            continue


def start_scheduler() -> None:
    """Spawn scheduler tasks. Idempotent — safe to call multiple times."""
    global _TASK, _SCORE_TASK, _SENTRY_TASK, _REGISTRY_TASK, _STOP_EVENT
    if _TASK and not _TASK.done():
        return
    # Hydrate persisted run cursor first; the catch-up coroutine below
    # uses it to decide whether the host missed a window while asleep.
    _load_scheduler_state()
    _STOP_EVENT = asyncio.Event()
    _TASK = asyncio.create_task(_loop(), name="skill-scheduler")
    _SCORE_TASK = asyncio.create_task(_score_loop(), name="rec-scorer")
    _SENTRY_TASK = asyncio.create_task(_sentry_loop(), name="sentry-digest")
    _REGISTRY_TASK = asyncio.create_task(
        _registry_cron_loop(),
        name="agent-registry-cron",
    )
    # Fire-and-forget catch-up. The coroutine no-ops when the cursor is
    # already current; otherwise it forces a single overdue score so a
    # host that was asleep at 4pm ET doesn't leave a gap until tomorrow.
    asyncio.create_task(
        catch_up_missed_runs(),
        name="rec-scorer-catchup",
    )
    _LOG.info("scheduler: started (skill loop + scorer + sentry + registry-cron + catch-up)")


def stop_scheduler() -> None:
    if _STOP_EVENT:
        _STOP_EVENT.set()
    if _TASK:
        _TASK.cancel()
    if _SCORE_TASK:
        _SCORE_TASK.cancel()
    if _SENTRY_TASK:
        _SENTRY_TASK.cancel()
    if _REGISTRY_TASK:
        _REGISTRY_TASK.cancel()
    _LOG.info("scheduler: stopped")


def scheduler_status() -> dict:
    return {
        "running": bool(_TASK and not _TASK.done()),
        "score_running": bool(_SCORE_TASK and not _SCORE_TASK.done()),
        "registry_running": bool(_REGISTRY_TASK and not _REGISTRY_TASK.done()),
        "last_run_ts": _LAST_RUN_TS,
        "last_run_result": _LAST_RUN_RESULT,
        "last_score_date": _LAST_SCORE_DATE,
        "last_score_result": _LAST_SCORE_RESULT,
        "next_interval_seconds": _interval_seconds(_now_eastern()),
        "is_market_hours": _is_market_hours(_now_eastern()),
    }


async def force_tick() -> dict:
    """Manual one-shot tick — used by REST /skills/refresh endpoint."""
    return await _tick()
