import asyncio
import time
import traceback
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import wealthsimple, market_data, macro
from app.services import (
    fundamentals as fundamentals_svc,
    technicals as technicals_svc,
    crypto_data as crypto_svc,
)
from app.services.health_score import compute_health_score
from app.services.portfolio_analytics import (
    concentration_warnings as get_concentration_warnings,
)
from app.services.pii_filter import filter_user_context
from app.services.recommendations import get_recommendations
from app.services.benchmark import compare_to_benchmarks
from app.services.portfolio_optimizer import optimize
from app.services.llm_router import (
    generate_narratives_batch,
    active_provider_names,
    verify_sell_signal,
)

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


async def _get_portfolio(
    session_id: str, session: dict, account_id: str = ""
):
    """Return enriched portfolio (10s in-process cache).
    account_id="" means aggregate across all accounts.
    Lock dedupes concurrent callers — dashboard fires 6 parallel endpoints.
    """
    key = (session_id, account_id)
    entry = _PORTFOLIO_CACHE.get(key)
    if entry and (time.time() - entry[1]) < _PORTFOLIO_CACHE_TTL:
        return entry[0]

    lock = _PORTFOLIO_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # Double-check — another caller may have populated while we waited.
        entry = _PORTFOLIO_CACHE.get(key)
        if entry and (time.time() - entry[1]) < _PORTFOLIO_CACHE_TTL:
            return entry[0]

        profile = session.get("profile")
        per_account = session.get("per_account", {})

        if account_id and account_id in per_account:
            acc = per_account[account_id]
            cash = float(acc.get("cash_balance") or 0.0)
            ws_total = float(acc.get("invested_value") or 0.0)
            pnl = float(acc.get("unrealized_pnl_cad") or 0.0)
            raw = await asyncio.to_thread(
                wealthsimple.get_positions, session_id, account_id
            )
        else:
            cash = sum(a.cash_balance for a in profile.accounts) if profile else 0.0
            ws_total = sum(
                a.invested_value for a in profile.accounts
            ) if profile else 0.0
            pnl = float(session.get("unrealized_pnl_cad") or 0.0)
            raw = await asyncio.to_thread(
                wealthsimple.get_all_positions, session_id
            )

        portfolio = market_data.enrich(raw, cash, ws_total, pnl)
        _PORTFOLIO_CACHE[key] = (portfolio, time.time())
        return portfolio


class LoginRequest(BaseModel):
    email: str
    password: str


class OtpRequest(BaseModel):
    session_id: str
    otp: str


@router.post("/login")
async def login(req: LoginRequest):
    try:
        result = await asyncio.to_thread(
            wealthsimple.login, req.email, req.password
        )
        return result
    except Exception as e:
        print("=" * 60)
        print(f"LOGIN ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        print("=" * 60)
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/verify-otp")
async def verify_otp(req: OtpRequest):
    try:
        result = await asyncio.to_thread(
            wealthsimple.verify_otp, req.session_id, req.otp
        )
        return result
    except Exception as e:
        print("=" * 60)
        print(f"OTP ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        print("=" * 60)
        raise HTTPException(status_code=401, detail=str(e))


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
        print(f"PORTFOLIO ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=f"Wealthsimple error: {e}")


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
            raw = await asyncio.to_thread(
                wealthsimple.get_all_positions, session_id
            )
            portfolio = market_data.enrich(raw, 0)
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
            raw = await asyncio.to_thread(
                wealthsimple.get_all_positions, session_id
            )
            portfolio = market_data.enrich(raw, 0)
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
        raw = await asyncio.to_thread(
            wealthsimple.get_all_positions, session_id
        )
        portfolio = market_data.enrich(raw, 0)
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
        sma50 = close.rolling(50).mean()

        return {
            "symbol": symbol,
            "period": period,
            "dates": [str(d)[:10] for d in close.index.tolist()],
            "close": [round(float(v), 4) for v in close.tolist()],
            "sma_50": [
                round(float(v), 4) if pd.notna(v) else None
                for v in sma50.tolist()
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


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
        alerts.append({
            "type": "concentration",
            "severity": "warning",
            "title": w.get("message", "Concentration alert"),
            "detail": w.get("detail", ""),
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
            raw = await asyncio.to_thread(
                wealthsimple.get_all_positions, session_id
            )
            portfolio = market_data.enrich(raw, 0)
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
        __import__("app.services.fundamentals", fromlist=["get_fundamentals"]).get_fundamentals,
        symbols,
    )
    analyst_targets = {
        sym: fd["analyst_target_price"]
        for sym, fd in fund_data.items()
        if fd.get("analyst_target_price")
    }

    result = await asyncio.to_thread(optimize, positions_dicts, analyst_targets)
    return result


@router.get("/llm-status")
async def llm_status_endpoint(session_id: str = Query(...)):
    """Which LLM providers are configured and available."""
    session = wealthsimple.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")
    return {"available_providers": active_provider_names()}
