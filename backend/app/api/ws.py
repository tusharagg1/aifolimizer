import asyncio
import json
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.security import (
    set_session_cookie,
    clear_session_cookie,
    enforce_rate_limit,
    get_logger,
)
from app.services import wealthsimple, market_data, macro
from app.services import (
    fundamentals as fundamentals_svc,
    technicals as technicals_svc,
    crypto_data as crypto_svc,
    positioning as positioning_svc,
)
from app.services.health_score import compute_health_score
from app.services.portfolio_analytics import (
    concentration_warnings as get_concentration_warnings,
)
from app.services.pii_filter import filter_user_context
from app.services.recommendations import get_recommendations
from app.services import watchlist as watchlist_svc
from app.services.patterns import detect_patterns
from app.services.screener import run_screener, FULL_UNIVERSE
from app.services.benchmark import compare_to_benchmarks
from app.services.portfolio_optimizer import optimize
from app.services.llm_router import (
    generate_portfolio_commentary,
    generate_narratives_batch,
    active_provider_names,
    verify_sell_signal,
)


_LOG = get_logger("aifolimizer.ws")

router = APIRouter()

# Short-lived cache so concurrent page-load requests share one WS+yfinance fetch.
# Key: (session_id, account_id) — "" = aggregate; per-tab caching for free.
# Invalidated after 10s so data stays fresh on manual refresh.
_PORTFOLIO_CACHE: dict[tuple[str, str], tuple] = {}
_PORTFOLIO_CACHE_TTL = 10
# Per-key lock — concurrent callers wait for one fetch, not a race.
_PORTFOLIO_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}

# Track previous recommendation actions per session to detect signal changes.
# Key: session_id → {symbol: action}
_PREV_REC_ACTIONS: dict[str, dict[str, str]] = {}

# Per-(symbol, period) price-history cache. PriceChart fires this on every
# period toggle; without cache every click round-trips yfinance.
_PRICE_HISTORY_CACHE: dict[tuple[str, str], tuple[dict, float]] = {}
_PRICE_HISTORY_TTL = 300  # 5 min — matches market_data quote freshness


async def _get_portfolio(
    session_id: str, session: dict, account_id: str = "",
    max_age_s: float | None = None,
):
    """Return enriched portfolio. Default 10s cache for interactive endpoints;
    callers polling on a slower cadence (WebSocket stream at 30s) pass a longer
    max_age_s so each frame can re-use the cached portfolio instead of re-hitting
    Wealthsimple GraphQL on every tick.

    account_id="" means aggregate across all accounts.
    Lock dedupes concurrent callers — dashboard fires 6 parallel endpoints.
    """
    ttl = _PORTFOLIO_CACHE_TTL if max_age_s is None else max_age_s
    key = (session_id, account_id)
    entry = _PORTFOLIO_CACHE.get(key)
    if entry and (time.time() - entry[1]) < ttl:
        return entry[0]

    lock = _PORTFOLIO_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # Double-check — another caller may have populated while we waited.
        entry = _PORTFOLIO_CACHE.get(key)
        if entry and (time.time() - entry[1]) < ttl:
            return entry[0]

        profile = session.get("profile")
        per_account = session.get("per_account", {})

        if account_id and account_id in per_account:
            acc = per_account[account_id]
            cash = float(acc.get("cash_balance") or 0.0)
            usd_cash = float(acc.get("usd_cash_balance") or 0.0)
            ws_total = float(acc.get("invested_value") or 0.0)
            pnl = float(acc.get("unrealized_pnl_cad") or 0.0)
            # Per-account: WS doesn't expose per-account net deposits easily;
            # fall back to identity-wide (slightly inaccurate when viewing one
            # account, but better than 0). Account-return is hidden in UI for
            # single-account views.
            net_deposits = float(session.get("net_deposits_cad") or 0.0)
            simple_return = session.get("simple_return_pct")
            raw = await asyncio.to_thread(
                wealthsimple.get_positions, session_id, account_id
            )
        else:
            cash = sum(a.cash_balance for a in profile.accounts) if profile else 0.0
            usd_cash = sum(
                float(per_account.get(a.type, {}).get("usd_cash_balance") or 0.0)
                for a in profile.accounts
            ) if profile else 0.0
            ws_total = sum(
                a.invested_value for a in profile.accounts
            ) if profile else 0.0
            pnl = float(session.get("unrealized_pnl_cad") or 0.0)
            net_deposits = float(session.get("net_deposits_cad") or 0.0)
            simple_return = session.get("simple_return_pct")
            raw = await asyncio.to_thread(
                wealthsimple.get_all_positions, session_id
            )

        portfolio = market_data.enrich(
            raw, cash, ws_total, pnl, usd_cash,
            net_deposits_cad=net_deposits,
            simple_return_pct=simple_return,
        )
        _PORTFOLIO_CACHE[key] = (portfolio, time.time())
        return portfolio


class LoginRequest(BaseModel):
    email: str
    password: str


class OtpRequest(BaseModel):
    session_id: str
    otp: str


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    # Per-IP + per-email limits — IP bucket blocks spray attacks, email bucket
    # protects a targeted account even if the attacker rotates IPs.
    enforce_rate_limit(request, "login_ip",
                       max_hits=10, window_seconds=300)
    enforce_rate_limit(request, "login_email",
                       max_hits=5, window_seconds=900,
                       identity_override=req.email.lower())
    try:
        result = await asyncio.to_thread(
            wealthsimple.login, req.email, req.password
        )
        sid = result.get("session_id") if isinstance(result, dict) else None
        if sid and not result.get("needs_otp"):
            set_session_cookie(response, sid)
        return result
    except Exception as e:
        _LOG.exception("login failed", extra={"event": "login_error"})
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.post("/restore")
async def restore_session_endpoint():
    """Restore a persisted Wealthsimple session from disk.

    Avoids re-prompting for credentials + OTP after a backend restart. The
    persisted token still respects WS server-side expiry, so a stale token
    returns null and the client falls back to /ws/login.
    """
    sid = await asyncio.to_thread(wealthsimple.restore_session)
    if not sid:
        return {"restored": False}
    session = wealthsimple.get_session(sid)
    profile = session.get("profile") if session else None
    return {
        "restored": True,
        "session_id": sid,
        "profile": profile.model_dump() if profile else None,
    }


@router.post("/verify-otp")
async def verify_otp(req: OtpRequest, request: Request, response: Response):
    # OTP is a 6-digit code → brute-forceable; cap hard.
    enforce_rate_limit(request, "otp_ip",
                       max_hits=8, window_seconds=300)
    enforce_rate_limit(request, "otp_session",
                       max_hits=5, window_seconds=300,
                       identity_override=req.session_id)
    try:
        result = await asyncio.to_thread(
            wealthsimple.verify_otp, req.session_id, req.otp
        )
        sid = result.get("session_id") if isinstance(result, dict) else None
        if sid:
            set_session_cookie(response, sid)
        return result
    except Exception as e:
        _LOG.exception("otp verify failed", extra={"event": "otp_error"})
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.post("/logout")
async def logout(response: Response):
    """Clear the session cookie. WS session token itself expires server-side."""
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/portfolio")
async def get_portfolio(
    session_id: str = Query(...),
    account_id: str = Query(
        "", description="Account type/id; empty = aggregate all"
    ),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=401, detail="Session expired — please log in again"
        )
    try:
        return await _get_portfolio(session_id, session, account_id)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        _LOG.exception("portfolio fetch failed", extra={"event": "portfolio_error"})
        raise HTTPException(status_code=502, detail=f"Wealthsimple error: {e}") from e


@router.get("/portfolio/debug-pnl")
async def debug_pnl(
    session_id: str = Query(...),
    account_id: str = Query("", description="Empty = aggregate"),
):
    """TEMPORARY diagnostic — dumps raw WS inputs and derived totals so we can
    pinpoint why total_return_pct is off. Remove once root cause confirmed."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    profile = session.get("profile")
    per_account = session.get("per_account", {})

    if account_id and account_id in per_account:
        acc = per_account[account_id]
        cash = float(acc.get("cash_balance") or 0.0)
        usd_cash = float(acc.get("usd_cash_balance") or 0.0)
        ws_total = float(acc.get("invested_value") or 0.0)
        pnl = float(acc.get("unrealized_pnl_cad") or 0.0)
        per_acc_breakdown = [{
            "type": account_id,
            "cash": cash, "usd_cash": usd_cash,
            "nlv": ws_total, "pnl_cad": pnl,
        }]
    else:
        cash = sum(a.cash_balance for a in profile.accounts) if profile else 0.0
        usd_cash = sum(
            float(per_account.get(a.type, {}).get("usd_cash_balance") or 0.0)
            for a in profile.accounts
        ) if profile else 0.0
        ws_total = sum(
            a.invested_value for a in profile.accounts
        ) if profile else 0.0
        pnl = float(session.get("unrealized_pnl_cad") or 0.0)
        per_acc_breakdown = [
            {
                "type": a.type,
                "cash": a.cash_balance,
                "usd_cash": float(
                    per_account.get(a.type, {}).get("usd_cash_balance") or 0.0
                ),
                "nlv": a.invested_value,
                "pnl_cad": float(
                    per_account.get(a.type, {}).get("unrealized_pnl_cad") or 0.0
                ),
            }
            for a in profile.accounts
        ] if profile else []

    equity_nlv = ws_total - cash
    total_cost_cad = equity_nlv - pnl if pnl and ws_total > 0 else None
    total_return_pct = (
        round((pnl / total_cost_cad) * 100, 2)
        if total_cost_cad and total_cost_cad > 0
        else None
    )

    return {
        "inputs": {
            "ws_account_total_nlv": ws_total,
            "cad_cash": cash,
            "usd_cash_balance": usd_cash,
            "unrealized_pnl_cad": pnl,
        },
        "derived": {
            "equity_nlv": equity_nlv,
            "total_cost_cad": total_cost_cad,
            "total_return_pct": total_return_pct,
            "formula": (
                "total_cost = (NLV - cad_cash) - PnL; "
                "return_pct = PnL / total_cost"
            ),
        },
        "per_account": per_acc_breakdown,
        "notes": [
            "If `unrealized_pnl_cad` looks wrong, WS API is returning a "
            "different metric. Compare with WS app totals.",
            "If `usd_cash_balance` > 0, equity_nlv still contains USD cash "
            "(only CAD cash subtracted) — minor inflation of book cost.",
            "If `pnl_cad` per-account has mixed signs from positions you no "
            "longer hold, WS history baseline differs from your mental model.",
        ],
    }


@router.get("/profile")
async def get_profile(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    profile = session.get("profile")
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    safe_context = filter_user_context(profile.model_dump())
    return safe_context


@router.get("/fundamentals")
async def get_fundamentals(
    session_id: str = Query(...),
    symbols: str = Query(
        "", description="Comma-separated tickers; empty = top 15 holdings"
    ),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        try:
            portfolio = await _get_portfolio(session_id, session)
            top = sorted(
                portfolio.positions, key=lambda p: p.weight, reverse=True
            )[:15]
            sym_list = [p.symbol for p in top]
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    return await asyncio.to_thread(fundamentals_svc.get_fundamentals, sym_list)


@router.get("/technicals")
async def get_technicals(
    session_id: str = Query(...),
    symbols: str = Query(
        "", description="Comma-separated tickers; empty = top 15 holdings"
    ),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        try:
            portfolio = await _get_portfolio(session_id, session)
            top = sorted(
                portfolio.positions, key=lambda p: p.weight, reverse=True
            )[:15]
            sym_list = [p.symbol for p in top]
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    return await asyncio.to_thread(
        technicals_svc.get_technicals, sym_list
    )


@router.get("/earnings-calendar")
async def get_earnings_calendar(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    try:
        portfolio = await _get_portfolio(session_id, session)
        symbols = [p.symbol for p in portfolio.positions]
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    fund_data = await asyncio.to_thread(
        fundamentals_svc.get_fundamentals, symbols
    )

    today = date.today()
    results = []
    for sym, data in fund_data.items():
        ed = data.get("earnings_date")
        if not ed:
            continue
        try:
            ed_date = date.fromisoformat(ed[:10])
            days = (ed_date - today).days
            results.append({
                "symbol": sym,
                "earnings_date": ed[:10],
                "days_until": days,
                "is_upcoming": 0 <= days <= 14,
            })
        except Exception:
            continue
    return sorted(results, key=lambda r: r["earnings_date"])


@router.get("/price-history")
async def get_price_history(
    session_id: str = Query(...),
    symbol: str = Query(..., description="Ticker e.g. AAPL or XEQT.TO"),
    period: str = Query("1y", description="1mo, 3mo, 6mo, 1y, 2y"),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    cache_key = (symbol.upper(), period)
    entry = _PRICE_HISTORY_CACHE.get(cache_key)
    if entry and (time.time() - entry[1]) < _PRICE_HISTORY_TTL:
        return entry[0]

    try:
        import yfinance as yf
        import pandas as pd
        df = await asyncio.to_thread(
            lambda: yf.download(
                symbol, period=period, interval="1d",
                progress=False, auto_adjust=True,
            )
        )
        if df is None or df.empty:
            raise HTTPException(
                status_code=404, detail=f"No data for {symbol}"
            )

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"].squeeze().dropna()
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()

        def _ser(s: "pd.Series") -> list:
            return [round(float(v), 4) if pd.notna(v) else None for v in s.tolist()]

        # Weekly close resampled to same date index for multi-TF overlay
        weekly_close = close.resample("W").last().reindex(close.index, method="ffill")
        weekly_sma20 = weekly_close.rolling(20).mean()

        result = {
            "symbol": symbol,
            "period": period,
            "dates": [str(d)[:10] for d in close.index.tolist()],
            "close": [round(float(v), 4) for v in close.tolist()],
            "sma_20": _ser(sma20),
            "sma_50": _ser(sma50),
            "sma_200": _ser(sma200),
            "weekly_sma_20": _ser(weekly_sma20),
        }
        _PRICE_HISTORY_CACHE[cache_key] = (result, time.time())
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/patterns")
async def patterns_endpoint(
    session_id: str = Query(...),
    symbol: str = Query(..., description="Ticker e.g. AAPL or XEQT.TO"),
    period: str = Query("1y", description="1mo 3mo 6mo 1y 2y"),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return await asyncio.to_thread(detect_patterns, symbol, period)


@router.get("/health-score")
async def health_score_endpoint(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return compute_health_score(portfolio)


@router.get("/alerts")
async def alerts_endpoint(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    alerts: list[dict] = []

    warnings = get_concentration_warnings(portfolio, 10.0, 35.0)
    for w in warnings:
        label = w.get("symbol") or w.get("sector") or "Position"
        weight_pct = float(w.get("weight_pct") or 0)
        alerts.append({
            "type": "concentration",
            "severity": "warning",
            "title": f"Concentration: {label} {weight_pct:.1f}%",
            "detail": w.get("note", ""),
        })

    try:
        symbols = [p.symbol for p in portfolio.positions]
        fund_data = await asyncio.to_thread(
            fundamentals_svc.get_fundamentals, symbols
        )
        today = date.today()
        for sym, data in fund_data.items():
            ed = data.get("earnings_date")
            if not ed:
                continue
            try:
                ed_date = date.fromisoformat(ed[:10])
                days = (ed_date - today).days
                if 0 <= days <= 14:
                    label = f"in {days} day{'s' if days != 1 else ''}"
                    alerts.append({
                        "type": "earnings",
                        "severity": "high" if days <= 7 else "warning",
                        "title": f"{sym} earnings {label}",
                        "detail": f"Earnings on {ed[:10]} — review position",
                    })
            except Exception:
                pass
    except Exception:
        pass

    severity_rank = {"high": 0, "warning": 1, "info": 2}
    return sorted(
        alerts, key=lambda a: severity_rank.get(a["severity"], 9)
    )


@router.get("/market-breadth")
async def market_breadth_endpoint(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return await asyncio.to_thread(macro.market_breadth)


@router.get("/macro")
async def macro_endpoint(session_id: str = Query(...)):
    """Combined macro snapshot: FRED rates + market breadth + Fear & Greed."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    breadth, snapshot = await asyncio.gather(
        asyncio.to_thread(macro.market_breadth),
        asyncio.to_thread(macro.macro_snapshot),
    )
    return {**breadth, "fred": snapshot}


@router.get("/crypto")
async def crypto_endpoint(
    session_id: str = Query(...),
    symbols: str = Query(
        "", description="Comma-separated crypto tickers e.g. BTC,ETH"
    ),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        try:
            portfolio = await _get_portfolio(session_id, session)
            sym_list = crypto_svc.crypto_symbols_from_portfolio(
                [p.symbol for p in portfolio.positions]
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    if not sym_list:
        return {}
    return await asyncio.to_thread(crypto_svc.get_crypto_data, sym_list)


@router.get("/recommendations")
async def recommendations_endpoint(session_id: str = Query(...)):
    """Multi-signal recommendations with LLM SELL verification."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    positions_dicts = [p.model_dump() for p in portfolio.positions]
    recs = await asyncio.to_thread(get_recommendations, positions_dicts)

    # LLM sanity-check SELL signals that lack high confidence — demote noise to WATCH
    sell_candidates = [r for r in recs if r["action"] == "SELL" and r.get("confidence") != "high"]
    if sell_candidates and active_provider_names():
        verdicts = await asyncio.gather(*[verify_sell_signal(r) for r in sell_candidates])
        demoted = {r["symbol"] for r, keep in zip(sell_candidates, verdicts) if not keep}
        if demoted:
            recs = [
                {**r, "action": "WATCH", "llm_demoted": True} if r["symbol"] in demoted else r
                for r in recs
            ]

    # Detect action changes vs previous run — surface as signal_changes
    prev = _PREV_REC_ACTIONS.get(session_id, {})
    signal_changes = []
    for r in recs:
        old = prev.get(r["symbol"])
        new = r["action"]
        if old and old != new:
            # Only flag meaningful escalations, not HOLD↔WATCH noise
            meaningful = {
                ("HOLD", "BUY"), ("HOLD", "SELL"), ("WATCH", "BUY"), ("WATCH", "SELL"),
                ("BUY", "SELL"), ("BUY", "WATCH"), ("SELL", "BUY"),
                ("HOLD", "WATCH"), ("WATCH", "HOLD"),
            }
            if (old, new) in meaningful:
                signal_changes.append({
                    "symbol": r["symbol"],
                    "name": r.get("name", r["symbol"]),
                    "from_action": old,
                    "to_action": new,
                    "score": r["score"],
                    "confidence": r.get("confidence"),
                    "top_reason": r["reasons"][0] if r.get("reasons") else None,
                    "ev_dollars": r.get("ev_dollars"),
                })
    # Persist current actions for next comparison
    _PREV_REC_ACTIONS[session_id] = {r["symbol"]: r["action"] for r in recs}

    return {"recommendations": recs, "signal_changes": signal_changes}


@router.get("/ai-commentary")
async def ai_commentary_endpoint(session_id: str = Query(...)):
    """AI portfolio commentary: 2-3 sentence assessment + 2-4 actionable bullets."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    if not active_provider_names():
        return {"commentary": None, "actions": [], "provider": None, "error": "no_llm_keys"}

    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    positions_dicts = [p.model_dump() for p in portfolio.positions]
    recs = await asyncio.to_thread(get_recommendations, positions_dicts)
    health = compute_health_score(portfolio)
    summary = {
        **portfolio.summary.model_dump(),
        "grade": health.get("grade"),
        "score": health.get("score"),
    }

    result = await generate_portfolio_commentary(summary, recs)
    if not result:
        return {"commentary": None, "actions": [], "provider": None, "error": "llm_failed"}
    return result


@router.get("/ai-narratives")
async def ai_narratives_endpoint(session_id: str = Query(...)):
    """AI narrative for each recommendation. Uses best available free LLM."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    providers = active_provider_names()
    if not providers:
        return {"narratives": {}, "provider": None, "error": "no_llm_keys"}

    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    positions_dicts = [p.model_dump() for p in portfolio.positions]
    recs = await asyncio.to_thread(get_recommendations, positions_dicts)
    narratives = await generate_narratives_batch(recs)
    return {"narratives": narratives, "providers": providers}


@router.get("/benchmark")
async def benchmark_endpoint(session_id: str = Query(...)):
    """Portfolio total return vs XEQT, SPY, QQQ, TSX across 1mo/3mo/6mo/1y/3y."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    positions_dicts = [p.model_dump() for p in portfolio.positions]
    return await asyncio.to_thread(compare_to_benchmarks, positions_dicts)


@router.get("/optimize")
async def optimize_endpoint(session_id: str = Query(...)):
    """Efficient Frontier optimization — optimal weights vs current weights."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    positions_dicts = [p.model_dump() for p in portfolio.positions]

    # Collect analyst targets from fundamentals for Black-Litterman views
    symbols = [p["symbol"] for p in positions_dicts]
    fund_data = await asyncio.to_thread(
        fundamentals_svc.get_fundamentals, symbols
    )
    analyst_targets = {
        sym: fd["analyst_target_price"]
        for sym, fd in fund_data.items()
        if fd.get("analyst_target_price")
    }

    result = await asyncio.to_thread(optimize, positions_dicts, analyst_targets)
    return result


@router.get("/crowding")
async def crowding_endpoint(
    session_id: str = Query(...),
    top_n: int = Query(15, ge=1, le=50),
):
    """Crowding / positioning signals for top N holdings. Surfaces consensus-vs-contrarian
    score per ticker so frontend can color-code late-entry risk.
    """
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        portfolio = await _get_portfolio(session_id, session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    top = sorted(
        portfolio.positions, key=lambda p: p.weight, reverse=True
    )[:top_n]
    symbols = [p.symbol for p in top]
    if not symbols:
        return {}
    return await asyncio.to_thread(positioning_svc.get_positioning, symbols)


@router.get("/llm-status")
async def llm_status_endpoint(session_id: str = Query(...)):
    """Which LLM providers are configured and available."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return {"available_providers": active_provider_names()}


# ── Phase 3: integrated signals (5-signal breakdown) ────────────────────────

def _session_tenant_hash(session_id: str) -> str:
    import hashlib
    return hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:16]


@router.get("/signals")
async def get_integrated_signals(
    session_id: str = Query(...),
):
    """Latest integrated signal per holding (5-signal breakdown).

    Reads from Redis hot cache if present; falls back to Postgres latest row
    per symbol. PII-stripped (no account_id, no email, no portfolio totals).
    """
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    thash = _session_tenant_hash(session_id)

    # Hot cache attempt
    try:
        from app.cache import get_redis
        r = get_redis()
        if r is not None:
            blob = await r.get(f"signals:{thash}")
            if blob:
                return json.loads(blob)
    except Exception:
        pass

    # Postgres fallback
    try:
        from app.db.repositories import signals_repo
        rows = await signals_repo.latest_for_tenant(thash)
    except Exception as e:
        raise HTTPException(503, f"signals unavailable: {e}")

    signals = [
        {
            "symbol": r["symbol"],
            "action": r["action"],
            "conviction": r.get("conviction"),
            "score": float(r["score"]) if r.get("score") is not None else None,
            "sub_signals": {
                "tech": float(r["tech_score"])
                    if r.get("tech_score") is not None else None,
                "fund": float(r["fund_score"])
                    if r.get("fund_score") is not None else None,
                "macro": float(r["macro_score"])
                    if r.get("macro_score") is not None else None,
                "sentiment": float(r["sentiment_score"])
                    if r.get("sentiment_score") is not None else None,
                "skill_consensus": r.get("skill_consensus"),
                "skill_confidence": float(r["skill_confidence"])
                    if r.get("skill_confidence") is not None else None,
            },
            "skill_evidence": r.get("skill_evidence"),
            # Phase 11 Kelly audit: surface position-sizing recommendation.
            "kelly_pct": float(r["kelly_pct"])
                if r.get("kelly_pct") is not None else None,
            "win_prob": float(r["win_prob"])
                if r.get("win_prob") is not None else None,
            "risk_reward": float(r["risk_reward"])
                if r.get("risk_reward") is not None else None,
            "ts": r["ts"].isoformat() if r.get("ts") else None,
        }
        for r in rows
    ]
    return {"as_of": rows[0]["ts"].isoformat() if rows else None,
            "signals": signals}


@router.get("/signals/history")
async def get_signal_history(
    session_id: str = Query(...),
    symbol: str = Query(..., min_length=1),
    days: int = Query(30, ge=1, le=365),
):
    """Time-series of integrated signal for one symbol."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    thash = _session_tenant_hash(session_id)
    try:
        from app.db.repositories import signals_repo
        rows = await signals_repo.history_for_symbol(
            thash, symbol.upper(), days=days,
        )
    except Exception as e:
        raise HTTPException(503, f"history unavailable: {e}")

    return {
        "symbol": symbol.upper(),
        "days": days,
        "points": [
            {
                "ts": r["ts"].isoformat() if r.get("ts") else None,
                "score": float(r["score"]) if r.get("score") is not None else None,
                "action": r.get("action"),
                "tech": float(r["tech_score"])
                    if r.get("tech_score") is not None else None,
                "fund": float(r["fund_score"])
                    if r.get("fund_score") is not None else None,
                "macro": float(r["macro_score"])
                    if r.get("macro_score") is not None else None,
                "sentiment": float(r["sentiment_score"])
                    if r.get("sentiment_score") is not None else None,
                "skill": r.get("skill_consensus"),
            }
            for r in rows
        ],
    }


@router.get("/discovery/top")
async def get_discovery_top(
    session_id: str = Query(...),
    n: int = Query(5, ge=1, le=20),
):
    """Phase 13: cached top N discovery picks (nightly scan output)."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(session_id)
    try:
        from app.services import discovery
        picks = await discovery.get_cached_top(thash)
        return {"picks": picks[:n], "n": min(n, len(picks))}
    except Exception as e:
        raise HTTPException(503, f"discovery unavailable: {e}")


@router.get("/discovery/scan")
async def post_discovery_scan(
    session_id: str = Query(...),
    min_score: float = Query(6.0, ge=0.0, le=10.0),
):
    """Phase 13: on-demand fresh scan (slower than /discovery/top)."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(session_id)
    try:
        from app.services import discovery
        portfolio = await _get_portfolio(
            session_id, session, "", max_age_s=300,
        )
        picks = await discovery.scan_universe(
            thash, portfolio=portfolio, min_score=min_score,
        )
        return {"picks": picks}
    except Exception as e:
        raise HTTPException(503, f"discovery scan failed: {e}")


class WatchlistAddV2Request(BaseModel):
    session_id: str
    symbol: str
    note: str | None = None


@router.post("/discovery/watchlist")
async def add_watchlist(body: WatchlistAddV2Request):
    """Phase 13: add a ticker to discovery watchlist (Postgres)."""
    session = wealthsimple.get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(body.session_id)
    try:
        from app.db.pool import get_pool
        pool = get_pool()
        if pool is None:
            raise HTTPException(503, "DB unavailable")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO watchlist (tenant_hash, symbol, note)
                VALUES ($1, $2, $3)
                ON CONFLICT (tenant_hash, symbol) DO UPDATE
                  SET note = EXCLUDED.note
                """,
                thash, body.symbol.upper(), body.note,
            )
        return {"status": "ok", "symbol": body.symbol.upper()}
    except Exception as e:
        raise HTTPException(503, f"watchlist add failed: {e}")


@router.delete("/discovery/watchlist/{symbol}")
async def remove_watchlist(symbol: str, session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(session_id)
    try:
        from app.db.pool import get_pool
        pool = get_pool()
        if pool is None:
            raise HTTPException(503, "DB unavailable")
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM watchlist "
                "WHERE tenant_hash = $1 AND symbol = $2",
                thash, symbol.upper(),
            )
        return {"status": "ok", "removed": symbol.upper()}
    except Exception as e:
        raise HTTPException(503, f"watchlist remove failed: {e}")


@router.get("/discovery/watchlist")
async def list_watchlist(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(session_id)
    try:
        from app.db.pool import get_pool
        pool = get_pool()
        if pool is None:
            raise HTTPException(503, "DB unavailable")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT symbol, note, added_at FROM watchlist "
                "WHERE tenant_hash = $1 ORDER BY added_at DESC",
                thash,
            )
        return {
            "watchlist": [
                {
                    "symbol": r["symbol"],
                    "note": r["note"],
                    "added_at": (
                        r["added_at"].isoformat() if r.get("added_at") else None
                    ),
                }
                for r in rows
            ],
        }
    except Exception as e:
        raise HTTPException(503, f"watchlist list failed: {e}")


@router.get("/risk-gate")
async def get_risk_gate(session_id: str = Query(...)):
    """Phase 12: current portfolio-level risk gate state."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(session_id)
    try:
        from app.services import risk_gate
        state = await risk_gate.get_current(thash)
        if state is None:
            return {"gate": None}
        return {"gate": state.to_dict()}
    except Exception as e:
        raise HTTPException(503, f"risk_gate unavailable: {e}")


class RiskGateOverrideRequest(BaseModel):
    session_id: str
    reason: str
    hours: int = 24


@router.post("/risk-gate/override")
async def post_risk_gate_override(body: RiskGateOverrideRequest):
    """Phase 12: manual override (reset gate to 'trade' for N hours).
    Logged with reason. Max 24h.
    """
    session = wealthsimple.get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(body.session_id)
    try:
        from app.services import risk_gate
        state = await risk_gate.override(
            thash, reason=body.reason, hours=body.hours,
        )
        return {"gate": state.to_dict()}
    except Exception as e:
        raise HTTPException(503, f"override failed: {e}")


@router.get("/kpis")
async def get_live_kpis(
    session_id: str = Query(...),
    window: int = Query(30, ge=7, le=180),
):
    """Phase 10: EV / PF / Sharpe / DD / regime-breakdown for the live
    paper-trade book over the trailing window."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    thash = _session_tenant_hash(session_id)
    try:
        from app.services import live_metrics
        latest = await live_metrics.latest(thash, window_days=window)
        if latest is None:
            # No snapshot yet → compute now (returns empty if no closed recs).
            kpi_now = await live_metrics.kpis(thash, window_days=window)
            return {"kpis": kpi_now, "from": "live"}
        return {"kpis": latest, "from": "snapshot"}
    except Exception as e:
        raise HTTPException(503, f"kpis unavailable: {e}")


@router.get("/calibration")
async def get_calibration(
    session_id: str = Query(...),
    horizon: int = Query(21, ge=1, le=63),
):
    """Phase 9: latest Brier + ECE + reliability bins report."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        from app.services.calibration import latest_report
        r = await latest_report(horizon_days=horizon)
        if r is None:
            return {"report": None, "reason": "no report yet"}
        return {"report": r}
    except Exception as e:
        raise HTTPException(503, f"calibration unavailable: {e}")


@router.get("/regime")
async def get_current_regime(session_id: str = Query(...)):
    """Phase 8: current market regime classification + per-skill multipliers
    in effect for the current composite."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        from app.services import market_regime
        cur = await market_regime.get_current()
        if cur is None:
            return {"regime": None, "multipliers": {}}
        return {
            "regime": cur.to_dict(),
            "multipliers": market_regime.initial_multipliers_for(cur.composite),
        }
    except Exception as e:
        raise HTTPException(503, f"regime unavailable: {e}")


@router.get("/weights")
async def get_weights(
    session_id: str = Query(...),
    limit: int = Query(30, ge=1, le=200),
):
    """Current 5-signal weights + last N audit versions."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    try:
        from app.db.repositories import weights_repo
        current = await weights_repo.current()
        history = await weights_repo.history(limit=limit)
    except Exception as e:
        raise HTTPException(503, f"weights unavailable: {e}")

    return {
        "current": {
            "version": current.get("version"),
            "ts": current["ts"].isoformat() if current.get("ts") else None,
            "w_tech": float(current.get("w_tech") or 0),
            "w_fund": float(current.get("w_fund") or 0),
            "w_macro": float(current.get("w_macro") or 0),
            "w_sentiment": float(current.get("w_sentiment") or 0),
            "w_skill": float(current.get("w_skill") or 0),
            "reason": current.get("reason"),
            "objective": current.get("objective"),
        },
        "history": [
            {
                "version": h.get("version"),
                "ts": h["ts"].isoformat() if h.get("ts") else None,
                "w_tech": float(h.get("w_tech") or 0),
                "w_fund": float(h.get("w_fund") or 0),
                "w_macro": float(h.get("w_macro") or 0),
                "w_sentiment": float(h.get("w_sentiment") or 0),
                "w_skill": float(h.get("w_skill") or 0),
                "reason": h.get("reason"),
                "objective": h.get("objective"),
            }
            for h in history
        ],
    }


# ── Watchlist ──────────────────────────────────────────────────────────────────

class WatchlistAddRequest(BaseModel):
    session_id: str
    symbol: str
    notes: str = ""


@router.get("/watchlist")
async def get_watchlist(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return watchlist_svc.load_watchlist()


@router.post("/watchlist")
async def add_to_watchlist(req: WatchlistAddRequest):
    session = wealthsimple.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="symbol required")
    try:
        return watchlist_svc.add_symbol(req.symbol, req.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/watchlist/{symbol}")
async def remove_from_watchlist(
    symbol: str,
    session_id: str = Query(...),
):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return watchlist_svc.remove_symbol(symbol)


@router.get("/watchlist/recommendations")
async def watchlist_recommendations(session_id: str = Query(...)):
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    recs = await asyncio.to_thread(
        watchlist_svc.get_watchlist_recommendations
    )
    return {"recommendations": recs}


# ── Screener ──────────────────────────────────────────────────────────────────

@router.get("/screener")
async def screener_endpoint(
    session_id: str = Query(...),
    universe: str = Query(
        "full",
        description="'tsx', 'spx', or 'full'"
    ),
    max_results: int = Query(30, ge=1, le=100),
):
    """Stage-2 setup screener across TSX + S&P 500 universe."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    tsx = [s for s in FULL_UNIVERSE if s.endswith(".TO") or s.endswith(".V")]
    spx = [s for s in FULL_UNIVERSE if not s.endswith(".TO") and not s.endswith(".V")]
    sym_list = tsx if universe == "tsx" else spx if universe == "spx" else None

    results = await asyncio.to_thread(run_screener, sym_list, max_results)
    return {"results": results, "universe": universe, "count": len(results)}


# ── WebSocket streaming ────────────────────────────────────────────────────────

_STREAM_INTERVAL = 30  # seconds between portfolio push frames


@router.websocket("/stream")
async def portfolio_stream(
    websocket: WebSocket,
    session_id: str = Query(...),
):
    """Push portfolio summary + health score every 30s.
    Client sends JSON ping to keep alive; server sends JSON frames.
    """
    session = wealthsimple.get_session(session_id)
    if not session:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        while True:
            session = wealthsimple.get_session(session_id)
            if not session:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "session_expired"})
                )
                break

            try:
                # Stream frames every 30s; allow cached portfolio up to that age
                # so WS does not stampede Wealthsimple GraphQL every tick.
                portfolio = await _get_portfolio(
                    session_id, session,
                    max_age_s=float(_STREAM_INTERVAL),
                )
                health = compute_health_score(portfolio)
                frame = {
                    "type": "portfolio_update",
                    "summary": portfolio.summary.model_dump(),
                    "health": health,
                    "position_count": len(portfolio.positions),
                }
            except Exception as e:
                frame = {"type": "error", "detail": str(e)}

            await websocket.send_text(json.dumps(frame))

            # Wait for next interval, yield to event loop between ticks.
            # Also consume any client pings that arrive during the wait.
            try:
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=_STREAM_INTERVAL
                )
            except asyncio.TimeoutError:
                pass  # normal — no ping received, just send next frame

    except WebSocketDisconnect:
        pass
