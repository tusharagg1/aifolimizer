"""
aifolimizer MCP server — exposes Wealthsimple portfolio + quant analytics as MCP tools.

Register with Claude Code:
  claude mcp add aifolimizer "C:\\Users\\Tusha\\Documents\\projects\\aifolimizer\\backend\\.venv\\Scripts\\python.exe" "C:\\Users\\Tusha\\Documents\\projects\\aifolimizer\\backend\\mcp_server.py"

Reads WS_EMAIL and WS_PASSWORD from .env so Claude never sees credentials.
All portfolio data passed through pii_filter before returning to Claude.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

from app.services import (
    wealthsimple, market_data, macro, portfolio_analytics, quant,
    fundamentals as fundamentals_svc,
    technicals as technicals_svc,
    news as news_svc,
    crypto_data as crypto_svc,
    alerts as alerts_svc,
    backtest as backtest_svc,
    positioning as positioning_svc,
    data_router,
    skill_backtest as skill_bt_svc,
    paper_trade as paper_trade_svc,
    signal_history as signal_history_svc,
    alpha_attribution as alpha_svc,
    trust_report as trust_svc,
    community as community_svc,
    options as options_svc,
    trade_ticket as trade_ticket_svc,
    memory as memory_svc,
    decision_memory as decision_mem_svc,
    shadow_account as shadow_svc,
    run_card as run_card_svc,
    skill_runner as skill_runner_svc,
)
from app.services.pii_filter import filter_portfolio, filter_user_context
from app.models.portfolio import PortfolioResponse
from ws_api import WSAPISession

mcp = FastMCP("aifolimizer")

_state: dict[str, Any] = {"session_id": None}
_SESSION_FILE = Path(__file__).parent / ".ws_session.json"

_MAX_SYMBOLS = 100
_VALID_ACCOUNT_TYPES = {"TFSA", "RRSP", "RESP", "Non-Reg", "Crypto", "LIRA", "FHSA", "Cash", ""}

# Diverse 20-symbol fallback universe: 5 US sectors + 4 Canadian + 3 ETFs + 2 mid/small
_DEFAULT_BACKTEST_UNIVERSE = [
    # US tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META",
    # US non-tech (financials, healthcare, energy, consumer)
    "JPM", "JNJ", "XOM", "AMZN", "HD",
    # Canadian equities (banks + energy)
    "TD.TO", "RY.TO", "ENB.TO", "CNQ.TO",
    # ETFs — Canadian broad + US tech + US small-cap
    "XEQT.TO", "VFV.TO", "XIC.TO", "QQQ", "IWM",
]


def _load_cached_session() -> str | None:
    """Try to restore a session from the file written by mcp_login.py."""
    if not _SESSION_FILE.exists():
        return None
    try:
        payload = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        ws_session = WSAPISession.from_json(payload["session_json"])
        email = payload["email"]
        result = wealthsimple._finalize_session(ws_session, email)
        return result["session_id"]
    except Exception as e:
        print(f"[MCP] cached session load failed: {e}", flush=True)
        return None


def _validate_account_id(account_id: str, profile) -> None:
    """Validate account_id against user's actual accounts."""
    if not account_id:
        return
    if account_id not in _VALID_ACCOUNT_TYPES:
        raise ValueError(f"Invalid account type: {account_id}")
    if profile and not any(a.type == account_id for a in profile.accounts):
        raise ValueError(f"Account {account_id} not found in your portfolio")


def _validate_symbols(symbols: list[str]) -> list[str]:
    """Validate symbol list length."""
    if len(symbols) > _MAX_SYMBOLS:
        raise ValueError(f"Too many symbols (max {_MAX_SYMBOLS})")
    return symbols


async def _ensure_session() -> str:
    """Login lazily on first tool call. Re-login on session expiry."""
    if _state["session_id"] and wealthsimple.get_session(_state["session_id"]):
        return _state["session_id"]

    # Try cached session from mcp_login.py first
    sid = await asyncio.to_thread(_load_cached_session)
    if sid:
        _state["session_id"] = sid
        return sid

    # Fall back to .env credentials (no-MFA accounts)
    email = os.getenv("WS_EMAIL", "")
    password = os.getenv("WS_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(
            "No cached session found. Run: cd backend && .venv/Scripts/python mcp_login.py"
        )

    result = await asyncio.to_thread(wealthsimple.login, email, password)
    if result.get("needs_otp"):
        raise RuntimeError(
            "MFA required. Run: cd backend && .venv/Scripts/python mcp_login.py"
        )
    _state["session_id"] = result["session_id"]
    return _state["session_id"]


async def _load_portfolio(account_id: str = "") -> PortfolioResponse:
    """Internal: fetch portfolio object (pre-PII-filter) for analytics."""
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    if not profile:
        raise RuntimeError("Session lost — please re-authenticate")

    per_account = session.get("per_account", {})
    if account_id and account_id in per_account:
        acc = per_account[account_id]
        cash_balance = float(acc.get("cash_balance") or 0.0)
        ws_account_total = float(acc.get("invested_value") or 0.0)
        unrealized_pnl_cad = float(acc.get("unrealized_pnl_cad") or 0.0)
    else:
        cash_balance = sum(a.cash_balance for a in profile.accounts)
        ws_account_total = sum(a.invested_value for a in profile.accounts)
        unrealized_pnl_cad = float(session.get("unrealized_pnl_cad") or 0.0)

    if account_id:
        raw_positions = await asyncio.to_thread(wealthsimple.get_positions, session_id, account_id)
    else:
        raw_positions = await asyncio.to_thread(wealthsimple.get_all_positions, session_id)
    return market_data.enrich(
        raw_positions, cash_balance, ws_account_total, unrealized_pnl_cad
    )


# ────────────────────────────────────────────────────────────────────────────────
# Portfolio data tools
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_profile() -> dict:
    """
    Returns the user's account profile (account types, cash balances, total invested).
    PII-stripped. Use this first to learn account types (TFSA, RRSP, Non-Reg, etc.)
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    if not session or not session.get("profile"):
        raise RuntimeError("Session lost — please re-authenticate via the web UI")
    return filter_user_context(session["profile"].model_dump())


@mcp.tool()
async def get_portfolio(account_id: str = "") -> dict:
    """
    Returns enriched live portfolio. PII-stripped.
    Leave account_id empty to aggregate across all investment accounts.
    Pass an account type ('TFSA', 'RRSP', 'Non-Reg', 'Crypto') to filter.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    portfolio = await _load_portfolio(account_id)
    return filter_portfolio(portfolio.model_dump())


# ────────────────────────────────────────────────────────────────────────────────
# Analytics tools (use the reference repo's quant + analytics)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_xray(account_id: str = "") -> dict:
    """
    X-ray analysis: expand ETF holdings into underlying exposure (US/Canada/International/etc).
    Returns a map of {exposure_label: weight} sorted by weight desc.
    Use to spot hidden overlaps and true geographic / asset-class concentration.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    portfolio = await _load_portfolio(account_id)
    return {
        "xray_exposures": portfolio_analytics.xray_exposures(portfolio),
        "sector_breakdown": portfolio_analytics.sector_concentration(portfolio),
        "asset_class_breakdown": portfolio_analytics.asset_class_breakdown(portfolio),
    }


@mcp.tool()
async def get_concentration_warnings(account_id: str = "", single_max_pct: float = 10.0, sector_max_pct: float = 35.0) -> list[dict]:
    """
    Flags positions and sectors over concentration thresholds.
    single_max_pct: any single ticker over this % of portfolio gets flagged
    sector_max_pct: any sector over this %
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    portfolio = await _load_portfolio(account_id)
    return portfolio_analytics.concentration_warnings(portfolio, single_max_pct, sector_max_pct)


@mcp.tool()
async def get_tax_loss_candidates(account_id: str = "", threshold_pct: float = -5.0) -> list[dict]:
    """
    Lists positions currently below threshold % return — review for tax-loss harvesting.
    Returns symbol, unrealized_loss, unrealized_loss_pct, and a Canadian-tax note.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    portfolio = await _load_portfolio(account_id)
    return portfolio_analytics.tax_loss_candidates(portfolio, threshold_pct)


@mcp.tool()
async def get_risk_metrics(account_id: str = "", period: str = "1y", top_n: int = 15) -> dict:
    """
    Portfolio-level risk metrics: annualized volatility, Sharpe, Sortino, VaR 95%, expected shortfall.
    Pulls daily yfinance returns for the top_n holdings by weight, weights them, and computes metrics.
    period: '6mo', '1y', '2y', '5y'
    """
    portfolio = await _load_portfolio(account_id)
    if not portfolio.positions:
        return {"error": "No positions in portfolio"}

    # Pick top holdings by weight
    top_positions = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:top_n]
    symbols = [p.symbol for p in top_positions]
    weights = {p.symbol: p.weight / 100.0 for p in top_positions}

    returns = await asyncio.to_thread(market_data.fetch_returns, symbols, period)
    return {
        "period": period,
        "symbols_analyzed": symbols,
        "total_weight_covered_pct": round(sum(weights.values()) * 100, 2),
        "metrics": quant.portfolio_risk_metrics(returns, weights),
    }


@mcp.tool()
async def get_correlation_matrix(account_id: str = "", period: str = "1y", top_n: int = 10) -> dict:
    """
    Pairwise correlation between top N holdings. Use to spot redundant exposure.
    Values close to 1.0 = moves together; close to 0 = uncorrelated; negative = hedge.
    """
    portfolio = await _load_portfolio(account_id)
    top_positions = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:top_n]
    symbols = [p.symbol for p in top_positions]
    returns = await asyncio.to_thread(market_data.fetch_returns, symbols, period)
    return {
        "period": period,
        "symbols": symbols,
        "matrix": quant.correlation_matrix(returns, min_observations=30),
    }


# ────────────────────────────────────────────────────────────────────────────────
# Macro
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_macro_snapshot() -> dict:
    """
    Latest macro readings from FRED: Fed funds rate, 10-year yield, US unemployment,
    US CPI, CAD/USD, BoC overnight rate, Canada CPI. Each entry has {date, value, series_id}.
    No API key needed. Cached 12h.
    """
    return await asyncio.to_thread(macro.macro_snapshot)


@mcp.tool()
async def get_market_breadth() -> dict:
    """
    Market regime signals: VIX (fear gauge), SPY vs SMA200 (bull/bear), composite regime label.
    vix: current CBOE VIX. vix_signal: fear/neutral/complacency.
    spy_regime: bull (SPY > SMA200) or bear.
    market_regime: bull_low_fear | bull_high_fear | bear_high_fear | bear_low_fear.
    regime_signal: plain-English interpretation for portfolio positioning.
    Use before sector-rotation or macro-impact skills to calibrate risk stance.
    Cached 1h. No API key needed.
    """
    return await asyncio.to_thread(macro.market_breadth)


# ────────────────────────────────────────────────────────────────────────────────
# Fundamentals, technicals, news
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_fundamentals(account_id: str = "", symbols: list[str] = []) -> dict:
    """
    Fundamental data per ticker: P/E, EPS, dividend yield, payout ratio, market cap,
    next earnings date, analyst target + recommendation, institutional/insider ownership,
    short interest, beta. Cached 6h per symbol.
    If symbols=[], uses top 15 holdings by weight.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:15]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)
    return await asyncio.to_thread(fundamentals_svc.get_fundamentals, symbols)


@mcp.tool()
async def get_sec_financials(symbols: list[str]) -> dict:
    """
    SEC EDGAR XBRL: annual revenue, net income, EPS for last 4 fiscal years.
    Authoritative multi-year trend data directly from SEC filings.
    US-listed symbols only (EDGAR has no .TO / Canadian filings).
    Returns revenue_annual, net_income_annual, eps_annual, revenue_cagr_3yr,
    income_cagr_3yr, revenue_trend, income_trend. Cached 24h.
    Use alongside get_fundamentals to validate yfinance income statement data.
    """
    symbols = _validate_symbols([s.upper() for s in symbols])
    results = {}
    for sym in symbols:
        results[sym] = await asyncio.to_thread(
            fundamentals_svc.get_sec_financials, sym
        )
    return results


@mcp.tool()
async def get_technicals(account_id: str = "", symbols: list[str] = []) -> dict:
    """
    Technical indicators per ticker: SMA20/50/200, RSI(14), MACD, Bollinger Bands,
    volume SMA20, trend direction (uptrend/downtrend/sideways), RSI signal.
    Uses 1y daily OHLCV. Cached 1h per symbol.
    If symbols=[], uses top 15 holdings by weight.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:15]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)
    return await asyncio.to_thread(technicals_svc.get_technicals, symbols)


@mcp.tool()
async def get_earnings_calendar(account_id: str = "") -> list[dict]:
    """
    Next earnings dates for all portfolio holdings, sorted ascending.
    Flags entries in the next 14 days as is_upcoming=True.
    Useful for pre-earnings analysis — call this before earnings_analyzer skill.
    """
    portfolio = await _load_portfolio(account_id)
    symbols = [p.symbol for p in portfolio.positions]
    fund_data = await asyncio.to_thread(fundamentals_svc.get_fundamentals, symbols)

    from datetime import date, timedelta
    today = date.today()
    cutoff = today + timedelta(days=14)
    results = []
    for sym, data in fund_data.items():
        ed = data.get("earnings_date")
        if not ed:
            continue
        try:
            ed_date = date.fromisoformat(ed[:10])
            days_until = (ed_date - today).days
            results.append({
                "symbol": sym,
                "earnings_date": ed[:10],
                "days_until": days_until,
                "is_upcoming": today <= ed_date <= cutoff,
            })
        except Exception:
            continue
    return sorted(results, key=lambda r: r["earnings_date"])


@mcp.tool()
async def get_earnings_results(
    account_id: str = "", symbols: list[str] = [], quarters: int = 4
) -> dict:
    """
    Historical EPS beat/miss results per ticker: last N quarters with
    actual vs estimate EPS, surprise %, and beat/meet/miss outcome.
    Use for post-earnings analysis (postmortem) — pairs with get_earnings_calendar
    which only shows next upcoming date. Cached 12h per symbol (reported data is fixed).
    If symbols=[], uses top 15 holdings by weight. quarters clamped 1-12.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:15]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)
    return await asyncio.to_thread(fundamentals_svc.get_earnings_history, symbols, quarters)


@mcp.tool()
async def get_positioning_signals(
    account_id: str = "", symbols: list[str] = []
) -> dict:
    """
    Crowding / positioning signals per ticker. Use BEFORE recommending an add
    to a name — flags when a position is already consensus-crowded (negative
    expected alpha for late entries) vs contrarian (potential edge).

    Fields per symbol:
      institutional_ownership_pct, short_pct_float, insider_ownership_pct,
      analyst_count, analyst_recommendation, headlines_7d, headlines_30d,
      headline_velocity_ratio (per-day ratio 7d vs 30d, >1 = surge),
      crowding_score (0-100), crowding_label (consensus|neutral|contrarian),
      consensus_flag (score >= 70), contrarian_flag (score <= 30).

    Cached 6h per symbol. If symbols=[], uses top 15 holdings by weight.
    Goldman / BlackRock 2025: AI-driven retail + quant crowding is the new
    structural risk — late entries into consensus names underperform.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:15]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)
    return await asyncio.to_thread(positioning_svc.get_positioning, symbols)


@mcp.tool()
async def snapshot_positioning_history(
    account_id: str = "", symbols: list[str] = [], top_n: int = 15
) -> dict:
    """
    Append today's crowding scores to .claude/context/crowding_history.jsonl.
    Idempotent — same symbol on same day is skipped. Used to detect regime
    shifts (consensus → contrarian and vice versa) over time.
    If symbols=[], snapshots top_n holdings by weight.
    """
    top_n = max(1, min(int(top_n), 25))
    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:top_n]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)
    return await asyncio.to_thread(positioning_svc.snapshot_to_history, symbols)


@mcp.tool()
async def get_crowding_shifts(
    account_id: str = "",
    symbols: list[str] = [],
    lookback_days: int = 30,
    score_delta_threshold: float = 25.0,
) -> dict:
    """
    Detect symbols whose crowding score has shifted materially over lookback.
    Reads from .claude/context/crowding_history.jsonl (written by
    snapshot_positioning_history). Returns shifts where |delta| >= threshold.
    Each shift: {symbol, from_score, to_score, from_label, to_label, delta,
    first_seen, last_seen, direction}.
    direction: 'crowding_up' (more consensus) or 'crowding_down' (more contrarian).
    Call snapshot_positioning_history regularly (daily) for this to have data.
    """
    lookback_days = max(2, min(int(lookback_days), 365))
    score_delta_threshold = max(5.0, min(float(score_delta_threshold), 100.0))
    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:25]
        symbols = [p.symbol for p in top]
    shifts = await asyncio.to_thread(
        positioning_svc.detect_regime_shifts,
        symbols, lookback_days, score_delta_threshold,
    )
    return {
        "lookback_days": lookback_days,
        "score_delta_threshold": score_delta_threshold,
        "count": len(shifts),
        "shifts": shifts,
    }


@mcp.tool()
async def get_news_headlines(ticker: str = "", limit: int = 5) -> dict:
    """
    Recent news headlines for a specific ticker or top holdings.
    If ticker="", returns news for the top 5 holdings by weight.
    limit: max articles per ticker (1-10).
    """
    if ticker:
        symbols = [ticker.upper()]
    else:
        portfolio = await _load_portfolio()
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:5]
        symbols = [p.symbol for p in top]
    raw = await asyncio.to_thread(news_svc.get_news, symbols)
    return {sym: articles[:max(1, min(limit, 10))] for sym, articles in raw.items()}


@mcp.tool()
async def get_community_sentiment(ticker: str) -> dict:
    """
    Reddit community sentiment for a ticker — scored from public posts.

    Searches r/stocks, r/investing, r/canadianinvestor for the past week.
    Returns community_score (0=all bearish, 50=neutral, 100=all bullish),
    bull/bear signal counts, post count, and sample post titles.
    Cached 30 min. No API key required (Reddit public JSON API).

    Use alongside get_positioning_signals to separate institutional crowding
    from retail/community sentiment — they frequently diverge.
    """
    return await asyncio.to_thread(community_svc.get_reddit_sentiment, ticker.upper())


@mcp.tool()
async def get_stocktwits_sentiment(ticker: str) -> dict:
    """StockTwits public stream — real-time retail trader sentiment, no API key required.

    Returns bull/bear message counts with explicit sentiment labels from StockTwits
    users. Complements Reddit (which is keyword-inferred) with labeled intent data.

    community_score: 0=all bear, 50=neutral, 100=all bull
    TSX tickers (.TO suffix) are handled automatically.
    Cached 15 minutes (shorter than Reddit — StockTwits is real-time retail flow).
    """
    return await asyncio.to_thread(community_svc.get_stocktwits_sentiment, ticker.upper())


# ────────────────────────────────────────────────────────────────────────────────
# Meta
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_crypto_data(
    account_id: str = "", symbols: list[str] = []
) -> dict:
    """
    CoinGecko data for crypto positions: price CAD, market cap, rank,
    24h/7d/30d change %, ATH drawdown %, circulating supply, volume.
    If symbols=[], auto-detects crypto holdings from portfolio.
    Cached 5 min. No API key required (CoinGecko free tier).
    Supported tickers: BTC ETH SOL ADA DOT AVAX LINK MATIC DOGE XRP
                       LTC BCH ATOM UNI ALGO NEAR FTM SAND MANA AAVE
    """
    if not symbols:
        portfolio = await _load_portfolio(account_id)
        all_syms = [p.symbol for p in portfolio.positions]
        symbols = crypto_svc.crypto_symbols_from_portfolio(all_syms)
    if not symbols:
        return {}
    return await asyncio.to_thread(crypto_svc.get_crypto_data, symbols)


@mcp.tool()
async def get_triggered_alerts(since_hours: int = 24, limit: int = 50) -> dict:
    """
    Returns recent alert events from the local alert log (no live re-eval).
    Source: .claude/context/alerts.jsonl, written by backend/scripts/run_alerts.py.
    Each alert: {rule, symbol, severity, title, body, ts}.
    Rules: price_drop_intraday, rsi_oversold, rsi_overbought,
    earnings_imminent, concentration_single, concentration_sector.
    since_hours: lookback window (default 24h). limit: max records (default 50).
    """
    since_hours = max(1, min(int(since_hours), 24 * 30))
    limit = max(1, min(int(limit), 500))
    items = await asyncio.to_thread(
        alerts_svc.read_recent_history, since_hours, limit
    )
    return {"since_hours": since_hours, "count": len(items), "alerts": items}


@mcp.tool()
async def run_alerts_now(
    account_id: str = "",
    price_drop_pct: float = 5.0,
    dry_run: bool = True,
) -> dict:
    """
    Evaluate alert rules against live portfolio and append triggers to history.
    dry_run=True (default): log + dedup but do NOT push to ntfy.
    dry_run=False: also push to ntfy.sh/<NTFY_TOPIC> if env var is set.
    Returns counts: {triggered, pushed, deduped}.
    Use sparingly — this fetches live WS + yfinance data.
    """
    portfolio = await _load_portfolio(account_id)
    triggered = await asyncio.to_thread(
        alerts_svc.evaluate, portfolio, price_drop_pct=price_drop_pct
    )
    topic = None if dry_run else os.getenv("NTFY_TOPIC")
    counts = await asyncio.to_thread(
        alerts_svc.dispatch, triggered, ntfy_topic=topic
    )
    return {"account": account_id or "all", "ntfy": "off" if not topic else "on", **counts}


@mcp.tool()
async def backtest_portfolio(
    account_id: str = "",
    symbols: list[str] = [],
    lookback_days: int = 365,
    strategies: list[str] = [],
    top_n: int = 15,
    tx_cost_bps: float = 5.0,
    walk_forward: bool = False,
    train_frac: float = 0.7,
) -> dict:
    """
    Replay simple rule-based strategies on historical OHLCV for portfolio holdings.
    Strategies:
      - 'buy_hold' (baseline)
      - 'rsi_swing' (RSI<30 buy / >70 sell)
      - 'sma_cross' (close vs SMA50)
      - 'crowd_fade' (sma_cross but skip symbols currently flagged consensus-crowded)
      - 'crowd_buy'  (sma_cross only on currently contrarian-flagged symbols)
    Defaults to ['buy_hold', 'rsi_swing', 'sma_cross', 'crowd_fade'] if strategies=[].
    tx_cost_bps: deducted per leg (entry + exit). 5 bps default (~0.05% per side).
    walk_forward=True splits each window into in-sample (train_frac) / out-of-sample.
    Per-symbol result then includes in_sample, out_of_sample, oos_minus_is_pct fields.
    Returns per-symbol metrics, weight-aggregated portfolio totals, delta-vs-buy-hold
    (positive = strategy beat passive). lookback_days clamped 30..730.
    If symbols=[], uses top_n holdings by weight. Cached 1h per (symbol, strategy,
    lookback, tx_cost, walk_forward, train_frac).
    """
    lookback_days = max(30, min(int(lookback_days), 730))
    top_n = max(1, min(int(top_n), 25))
    tx_cost_bps = max(0.0, min(float(tx_cost_bps), 100.0))
    train_frac = max(0.3, min(float(train_frac), 0.9))
    if not strategies:
        strategies = ["buy_hold", "rsi_swing", "sma_cross", "crowd_fade"]

    portfolio = await _load_portfolio(account_id)
    if not symbols:
        top = sorted(
            portfolio.positions, key=lambda p: p.weight, reverse=True
        )[:top_n]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)

    weights = {p.symbol: p.weight for p in portfolio.positions if p.symbol in symbols}
    return await asyncio.to_thread(
        backtest_svc.backtest_portfolio,
        symbols, weights, lookback_days, strategies, tx_cost_bps,
        walk_forward, train_frac,
    )


@mcp.tool()
def list_analysis_modes() -> list[dict]:
    """Lists the 13 institutional analysis frameworks available as Claude Code skills."""
    return [
        {"name": "daily_briefing", "style": "Morning digest composing 7 MCP tools",
         "tools_used": ["get_profile", "get_portfolio", "get_macro_snapshot", "get_concentration_warnings", "get_triggered_alerts", "get_earnings_calendar", "get_positioning_signals"]},
        {"name": "portfolio_health", "style": "BlackRock Portfolio Builder",
         "tools_used": ["get_profile", "get_portfolio", "get_xray", "get_concentration_warnings"]},
        {"name": "risk_assessment", "style": "Bridgewater Risk Assessment",
         "tools_used": ["get_portfolio", "get_risk_metrics", "get_correlation_matrix", "get_concentration_warnings"]},
        {"name": "stock_analysis", "style": "Goldman Sachs + Citadel TA",
         "tools_used": ["get_portfolio", "get_fundamentals", "get_technicals", "get_news_headlines", "get_positioning_signals"]},
        {"name": "stock_compare", "style": "Head-to-head A vs B matchup",
         "tools_used": ["get_profile", "get_portfolio", "get_fundamentals", "get_technicals", "get_news_headlines"]},
        {"name": "macro_impact", "style": "McKinsey Macro",
         "tools_used": ["get_portfolio", "get_macro_snapshot", "get_market_breadth"]},
        {"name": "dividend_strategy", "style": "Harvard Endowment Dividend",
         "tools_used": ["get_profile", "get_portfolio", "get_fundamentals"]},
        {"name": "earnings_analyzer", "style": "JPMorgan Earnings",
         "tools_used": ["get_portfolio", "get_earnings_calendar", "get_fundamentals"]},
        {"name": "earnings_postmortem", "style": "Post-report EPS beat/miss analysis",
         "tools_used": ["get_profile", "get_portfolio", "get_earnings_results", "get_fundamentals", "get_news_headlines"]},
        {"name": "sector_rotation", "style": "Renaissance / Sector Rotation",
         "tools_used": ["get_portfolio", "get_xray", "get_market_breadth"]},
        {"name": "tax_loss_review", "style": "Canadian tax-loss harvesting",
         "tools_used": ["get_tax_loss_candidates", "get_profile"]},
        {"name": "adversarial_research", "style": "Bull/Bear/Consensus parallel agent synthesis",
         "tools_used": ["get_portfolio", "get_fundamentals", "get_technicals", "get_news_headlines", "get_positioning_signals"]},
        {"name": "cash_deployment", "style": "Add-to-winners cash deployment",
         "tools_used": ["get_profile", "get_portfolio", "get_concentration_warnings", "get_fundamentals", "get_technicals", "get_positioning_signals"]},
    ]


@mcp.tool()
async def log_recommendation(
    skill: str,
    ticker: str,
    action: str,
    conviction: str,
    rationale: str,
    target_pct: float | None = None,
    stop_pct: float | None = None,
    account: str | None = None,
) -> dict:
    """Log a skill recommendation for forward paper-trade tracking.

    action: BUY | SELL | HOLD | ADD | TRIM
    conviction: HIGH | MED | LOW
    rationale: plain-text thesis (hashed for privacy before storage)
    target_pct / stop_pct: optional exit thresholds in % from entry

    Entry price fetched live from data_router at call time.
    Records persist in .claude/context/recommendations.jsonl (gitignored).
    """
    return await asyncio.to_thread(
        paper_trade_svc.log_recommendation,
        skill, ticker, action, conviction, rationale,
        target_pct, stop_pct, account,
    )


@mcp.tool()
async def score_recommendations(max_age_days: int = 90) -> dict:
    """Mark-to-market all open recommendations from the last N days.

    Fetches current price per ticker via data_router, computes unrealized P&L,
    flags stops/targets hit. Writes scored_recommendations.jsonl.
    Returns win-rate, avg return, and per-conviction breakdown.
    """
    return await asyncio.to_thread(
        paper_trade_svc.score_recommendations, max_age_days
    )


@mcp.tool()
async def get_live_track_record(windows_days: list[int] | None = None) -> dict:
    """Rolling forward-test stats from scored recommendations.

    Returns win-rate, avg return, by-conviction breakdown for 7/30/90d.
    Covers only live recommendations logged via log_recommendation —
    NOT the historical backtest (use get_skill_track_record for that).
    """
    return await asyncio.to_thread(
        paper_trade_svc.get_track_record, windows_days
    )


@mcp.tool()
async def score_signal_horizons(horizons: list[int] | None = None) -> dict:
    """Fill realized H-day forward returns on every logged signal.

    For every directional signal (BUY/ADD/SELL/TRIM) whose H-day window has
    elapsed, fetches historical bars and computes realized return at each
    horizon. SELL/TRIM returns are sign-flipped so positive == correct call.

    Default horizons: 5 and 21 trading days.
    Writes back to .claude/context/signal_history.jsonl in place.
    Returns counts: scored_new, skipped_window, skipped_data, total_rows.
    """
    h = tuple(horizons) if horizons else (5, 21)
    return await asyncio.to_thread(signal_history_svc.score_horizons, h)


@mcp.tool()
async def get_signal_accuracy(horizon: int = 21, min_count: int = 5) -> dict:
    """Buy/sell accuracy report at one horizon.

    Returns precision/recall/F1, win rate, expectancy %, and breakdowns by
    action class (BUY/SELL/ADD/TRIM), score bucket, and confidence level.
    Run score_signal_horizons first to populate realized returns.
    """
    return await asyncio.to_thread(
        signal_history_svc.accuracy_report, horizon, min_count=min_count
    )


@mcp.tool()
async def calibrate_signal_thresholds(
    horizon: int = 21, min_count: int = 10
) -> dict:
    """Grid-search BUY/SELL score thresholds that maximize expectancy on history.

    Returns current thresholds (buy=7.5, sell_below=3.5) and best-found
    thresholds with n_traded + expectancy. Apply manually after sanity check.
    Small-sample caveat: only trust once n_scored >= a few hundred.
    """
    return await asyncio.to_thread(
        signal_history_svc.calibrate_thresholds, horizon, min_count=min_count
    )


@mcp.tool()
async def snapshot_portfolio_equity(total_value_cad: float) -> dict:
    """Log today's total portfolio value for equity-curve tracking.

    Call daily (after market close) to build the historical NAV series
    needed by get_alpha_attribution. Idempotent per day.
    total_value_cad: total portfolio value in Canadian dollars.
    """
    return await asyncio.to_thread(alpha_svc.snapshot_equity, total_value_cad)


@mcp.tool()
async def get_alpha_attribution(lookback_days: int = 365) -> dict:
    """Annualized alpha, beta, Sharpe, info ratio vs SPY/XEQT/TSX/QQQ.

    Requires daily equity snapshots via snapshot_portfolio_equity.
    Also returns Wealthsimple Managed published returns for AUM comparison.

    lookback_days: how many days of portfolio history to include (default 365).
    """
    return await asyncio.to_thread(
        alpha_svc.get_alpha_attribution, None, lookback_days
    )


@mcp.tool()
async def get_quote_with_source(symbol: str, max_age_s: int = 300) -> dict:
    """Live quote with explicit data-source attribution.

    Tries yfinance -> finnhub -> tiingo -> stooq. Returns price, prev_close,
    day_change_pct, source, and as_of timestamp. Use to verify provenance
    or when freshness matters more than cache speed.
    """
    return await asyncio.to_thread(
        data_router.get_quote, symbol, float(max_age_s)
    )


@mcp.tool()
async def get_skill_track_record(
    universe: list[str] | None = None,
    lookback_days: int = 1825,
    tx_cost_bps: float = 5.0,
    fresh: bool = False,
) -> dict:
    """Backtest all 13 skills as codified strategies over historical bars.

    Returns per-skill: total_return_pct, cagr_pct, sharpe, sortino,
    max_drawdown_pct, hit_rate_pct, num_trades, alpha_vs_spy_pct,
    alpha_vs_xeqt_pct. Honest caveat: skills are codified to deterministic
    rules — LLM thesis nuance not replayed.

    If universe is None, uses current portfolio top-10 holdings.
    fresh=True forces re-run; otherwise the latest cached run is returned.
    """
    if not fresh:
        cached = await asyncio.to_thread(skill_bt_svc.latest_results)
        if cached:
            return cached
    if not universe:
        session_id = _state.get("session_id")
        if session_id:
            try:
                ws = WSAPISession.from_token(session_id)
                portfolio = await asyncio.to_thread(
                    wealthsimple.get_portfolio_response, ws
                )
                top = sorted(
                    portfolio.positions, key=lambda p: p.weight, reverse=True
                )[:10]
                universe = [p.symbol for p in top]
            except Exception:
                universe = _DEFAULT_BACKTEST_UNIVERSE
        else:
            universe = _DEFAULT_BACKTEST_UNIVERSE

    return await asyncio.to_thread(
        skill_bt_svc.backtest_all_skills,
        universe, int(lookback_days), float(tx_cost_bps), True,
    )


@mcp.tool()
async def get_data_source_reliability(window_days: int = 7) -> dict:
    """Per-source success rate and latency over the trailing window.

    Used by the trust-signal report (TRACK_RECORD.md) so users can audit
    which providers actually served their data and how reliably.
    """
    stats = await asyncio.to_thread(
        data_router.get_source_reliability, float(window_days) * 86400
    )
    return {
        "window_days": window_days,
        "configured": data_router.configured_sources(),
        "stats": stats,
    }


@mcp.tool()
async def get_quotes_batch(
    symbols: list[str], max_age_s: int = 300
) -> dict:
    """Fetch live quotes for multiple symbols in one batched HTTP call.

    ~13x faster than calling get_quote per symbol. Uses yfinance.download
    batching. Returns {symbol: {price, prev_close, day_change_pct, source}}.
    Missing symbols silently absent (fetch failed all sources).
    """
    return await asyncio.to_thread(
        data_router.get_quotes_batch, symbols, float(max_age_s)
    )


@mcp.tool()
async def generate_trust_report() -> dict:
    """Generate TRACK_RECORD.md (public) + track_record_full.jsonl (private).

    Pulls latest backtest results, live scored recommendations, and
    data-source reliability stats. Writes TRACK_RECORD.md to repo root
    (commit it to publish a git-timestamped trust signal).
    Returns paths + summary counts.
    """
    return await asyncio.to_thread(trust_svc.generate_report)


# ────────────────────────────────────────────────────────────────────────────────
# Options analytics
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_options_chain(
    ticker: str,
    expiry: str = "",
) -> dict:
    """
    Full options chain with Black-Scholes Greeks for every strike.

    ticker: symbol (e.g. "AAPL", "SHOP.TO")
    expiry: "YYYY-MM-DD" or "" for nearest expiry.
    Returns calls + puts with delta, gamma, vega, theta, rho, IV%,
    volume, open interest, and theoretical BS price.
    Cached 15 min. No API key required.

    Use alongside get_fundamentals and get_technicals before
    entering an options position.
    """
    return await asyncio.to_thread(
        options_svc.get_options_chain,
        ticker.upper(),
        expiry or None,
    )


@mcp.tool()
async def get_covered_call_screen(
    ticker: str,
    min_annual_yield_pct: float = 10.0,
    max_delta: float = 0.40,
) -> dict:
    """
    Screen OTM covered call strikes for income generation on a holding.

    Checks next 4 expiries. Each candidate shows:
    annual_yield_pct, delta, prob_keep_shares_pct, upside_to_strike_pct,
    breakeven, max_profit_per_contract (100 shares).

    min_annual_yield_pct: minimum annualised premium yield (default 10%).
    max_delta: maximum delta — higher delta = higher chance of assignment
    (default 0.40, i.e. ~40% chance of getting called away).

    Use when you want income on a long position without selling it.
    Cached 30 min.
    """
    return await asyncio.to_thread(
        options_svc.screen_covered_calls,
        ticker.upper(),
        float(min_annual_yield_pct),
        float(max_delta),
    )


@mcp.tool()
async def get_protective_put_screen(
    ticker: str,
    max_annual_cost_pct: float = 5.0,
    min_protection_pct: float = 5.0,
) -> dict:
    """
    Screen protective put options for downside hedging on a holding.

    Checks next 4 expiries. Each candidate shows:
    annual_cost_pct, protection_floor_pct, breakeven, cost_per_contract.

    max_annual_cost_pct: max annualised cost as % of position (default 5%).
    min_protection_pct: minimum downside protected (default 5%).

    Use when you want to hold a position through uncertainty
    without full risk exposure. Cached 30 min.
    """
    return await asyncio.to_thread(
        options_svc.screen_protective_puts,
        ticker.upper(),
        float(max_annual_cost_pct),
        float(min_protection_pct),
    )


# ────────────────────────────────────────────────────────────────────────────────
# Trade ticket
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_trade_ticket(
    ticker: str,
    action: str,
    conviction: str = "MED",
    account_id: str = "",
) -> dict:
    """
    Generate a precise, immediately actionable trade ticket.

    Returns: entry_price, quantity, dollar_amount_cad, stop_loss_price,
    target_price, risk_reward_ratio, max_loss_cad, position_size_pct,
    order_type (LIMIT/MARKET), limit_price, time_in_force,
    account_recommendation, and a plain-English instruction line.

    action: BUY | SELL | ADD | TRIM | EXIT
    conviction: HIGH | MED | LOW
      HIGH → 7% portfolio size, 8% stop, 3:1 R/R target
      MED  → 5% portfolio size, 6% stop, 2.5:1 R/R target
      LOW  → 3% portfolio size, 4% stop, 2:1 R/R target

    Stop is placed at SMA20 - 1% when price > SMA20 (natural support),
    otherwise uses conviction-based % distance.
    Limit buy set 0.2% below current price to avoid chasing.

    Call get_profile first — portfolio_value and available_cash
    are loaded automatically from the live session.
    """
    portfolio = await _load_portfolio(account_id)
    portfolio_value = portfolio.total_value_cad
    available_cash = portfolio.cash_balance

    position = next(
        (p for p in portfolio.positions if p.symbol == ticker.upper()),
        None,
    )
    position_value = float(position.market_value_cad) if position else 0.0

    return await asyncio.to_thread(
        trade_ticket_svc.generate_trade_ticket,
        ticker.upper(),
        action.upper(),
        float(portfolio_value),
        float(position_value),
        float(available_cash),
        conviction.upper(),
        account_id,
    )


# ────────────────────────────────────────────────────────────────────────────────
# Insider activity
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_insider_activity(ticker: str) -> dict:
    """
    Recent insider transactions + top institutional holders.

    Returns:
    - recent_transactions: last 10 insider buys/sells (name, title,
      shares, value, date)
    - top_holders: top 5 institutions with % held
    - insider_buy_sell_ratio: fraction of transactions that were buys
    - net_insider_signal: BULLISH (ratio ≥ 0.6) / BEARISH (≤ 0.3) / NEUTRAL

    Insiders buying their own stock = historically strong alpha signal.
    Cluster buys (multiple insiders buying same quarter) are strongest.
    Data from yfinance SEC filings (US stocks only; limited for .TO/.TSX).
    Cached 6h.
    """
    return await asyncio.to_thread(
        fundamentals_svc.get_insider_activity,
        ticker.upper(),
    )


# ────────────────────────────────────────────────────────────────────────────────
# Persistent investor memory
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def remember_preference(
    memory_type: str,
    content: str,
    tags: list[str] = [],
) -> dict:
    """Store a persistent investor preference or insight across sessions.

    memory_type: preference | insight | rule | note | observation
    content: plain-English description (e.g. "Prefer CAD-hedged ETFs for TFSA")
    tags: optional keywords for retrieval (e.g. ["TFSA", "ETF", "currency"])

    Stored in ~/.aifolimizer/memory/ — survives session restarts.
    Recalled automatically via recall_preferences when relevant.
    """
    return await asyncio.to_thread(
        memory_svc.remember, memory_type, content, tags
    )


@mcp.tool()
async def recall_preferences(query: str, top_k: int = 5) -> list[dict]:
    """Retrieve the most relevant stored investor memories for a query.

    Uses keyword scoring (metadata hits 2x body hits) to surface top-k
    relevant preferences, rules, or insights stored via remember_preference.
    Call at the start of any skill analysis to load investor context.
    """
    return await asyncio.to_thread(memory_svc.recall, query, top_k)


@mcp.tool()
async def list_memories(memory_type: str = "") -> list[dict]:
    """List all stored investor memories, optionally filtered by type.

    memory_type: "" (all) | preference | insight | rule | note | observation
    Returns newest-first sorted list.
    """
    return await asyncio.to_thread(
        memory_svc.list_memories, memory_type or None
    )


@mcp.tool()
async def forget_memory(query: str) -> dict:
    """Delete stored memories whose content contains query as substring.

    Use to remove outdated preferences (e.g. "forget SHOP" removes all
    memories mentioning SHOP). Returns count of deleted records.
    """
    return await asyncio.to_thread(memory_svc.forget, query)


# ────────────────────────────────────────────────────────────────────────────────
# Trade decision memory — per-ticker log with outcome tracking (TradingAgents pattern)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def log_trade_decision(
    ticker: str,
    action: str,
    conviction: str,
    entry_price: float,
    target_price: float,
    stop_price: float,
    thesis_summary: str,
    skill_used: str = "",
) -> dict:
    """Phase A — record a trade decision for forward outcome tracking.

    Call at the end of adversarial-research or cash-deployment after producing
    a final recommendation. Enables Phase B/C: outcome resolution and lesson
    injection into future analyses of the same ticker.

    action: BUY | SELL | HOLD
    conviction: Strong Buy | Buy | Neutral | Sell | Strong Sell
    thesis_summary: 1-2 sentence rationale (used for reflection generation)
    skill_used: adversarial-research | cash-deployment | stock-analysis | etc.
    """
    return await asyncio.to_thread(
        decision_mem_svc.log_decision,
        ticker, action, conviction, entry_price, target_price, stop_price,
        thesis_summary, skill_used,
    )


@mcp.tool()
async def resolve_trade_outcomes(days_expiry: int = 90) -> dict:
    """Phase B — mark-to-market all open trade decisions using live prices.

    Fetches current price for each open-decision ticker via get_quotes_batch,
    then marks each as target_hit / stop_hit / expired.
    Generates a reflection note on each resolved decision for future context injection.

    Run periodically (e.g. weekly) or before a new analysis of the same ticker.
    Returns count of resolved decisions by outcome.
    """
    records = await asyncio.to_thread(decision_mem_svc._load_all)
    open_tickers = list({r["ticker"] for r in records if r.get("outcome") == "open"})
    if not open_tickers:
        return {"resolved": {}, "total_open_remaining": 0, "note": "no open decisions"}

    from app.services import data_router
    price_map: dict[str, float] = {}
    for ticker in open_tickers:
        try:
            q = await asyncio.to_thread(data_router.get_quote, ticker)
            if q and q.get("price"):
                price_map[ticker] = float(q["price"])
        except Exception:
            continue

    return await asyncio.to_thread(
        decision_mem_svc.resolve_outcomes, price_map, days_expiry
    )


@mcp.tool()
async def get_ticker_decision_history(ticker: str, max_decisions: int = 5) -> list[dict]:
    """Phase C — retrieve past trade decisions for a ticker (newest first).

    Call at the START of adversarial-research or cash-deployment for the same
    ticker to inject prior decisions, outcomes, and reflections as context.
    Prevents repeating failed theses; reinforces strategies that worked.

    Returns up to max_decisions records with: date, action, conviction,
    entry/target/stop prices, outcome, outcome_price, and reflection note.
    """
    return await asyncio.to_thread(
        decision_mem_svc.get_ticker_history, ticker, max_decisions
    )


@mcp.tool()
async def get_cross_ticker_lessons(max_lessons: int = 3) -> list[dict]:
    """Phase C — top resolved wins and losses across all tickers for cross-portfolio lessons.

    Returns newest-first wins (target_hit) and losses (stop_hit), capped at
    max_lessons each. Each record includes ticker, action, outcome, P&L %, and
    a generated reflection. Inject into skill prompts to surface portfolio-level
    patterns (e.g. 'last 3 TSX banks stopped out at SMA50 — avoid that entry').
    """
    return await asyncio.to_thread(
        decision_mem_svc.get_cross_ticker_lessons, max_lessons
    )


# ────────────────────────────────────────────────────────────────────────────────
# Shadow account — behavioral rule extraction
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def analyze_shadow_account(transactions: list[dict]) -> dict:
    """Extract behavioral trading rules from your transaction history.

    Pairs buy→sell roundtrips via FIFO, clusters them by holding period and
    entry timing, and surfaces the implicit rules driving your actual trades.
    Answers: "what trading patterns am I actually executing, and are they
    consistent with a rule-based strategy?"

    Input format for each transaction dict:
      {"symbol": "AAPL", "side": "buy"|"sell", "price": 150.0,
       "quantity": 10, "date": "2024-01-15T10:30:00"}

    Returns:
    - summary: win-rate, avg return, avg holding days, symbols traded
    - extracted_rules: per-cluster behavioral rule with holding bounds + win-rate
    - roundtrips: up to 100 FIFO-paired roundtrips with return_pct

    No external dependencies — pure numpy k-means clustering.
    """
    return await asyncio.to_thread(shadow_svc.analyze_shadow_account, transactions)


# ────────────────────────────────────────────────────────────────────────────────
# Run card provenance
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def list_run_cards(limit: int = 20) -> list[dict]:
    """List recent backtest run cards with SHA256 provenance.

    Each run card records: run_id (strategy_hash + config_hash), timestamp,
    strategy name, symbols, config hash, and portfolio-level metrics.
    Backtest claims are auditable: same strategy + config always produces
    the same run_id, so historical results can be verified.

    limit: max cards to return (default 20, max 100).
    """
    limit = max(1, min(int(limit), 100))
    return await asyncio.to_thread(run_card_svc.list_run_cards, limit)


# ────────────────────────────────────────────────────────────────────────────────
# Accuracy layer — walk-forward, signal decay, attribution, calibration
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def walk_forward_backtest_skill(
    skill: str,
    lookback_days: int = 365 * 5,
    window_days: int = 252,
    step_days: int = 63,
    tx_cost_bps: float = 5.0,
) -> dict:
    """Run walk-forward OOS backtest of one skill across the default 40+ symbol unbiased universe.

    Returns aggregate stats, per-window stability, regime split (bull/bear/sideways),
    and deflated Sharpe (Bailey–López de Prado) to flag potential overfit.
    """
    return await asyncio.to_thread(
        skill_bt_svc.walk_forward_backtest,
        skill,
        None,
        lookback_days=lookback_days,
        window_days=window_days,
        step_days=step_days,
        tx_cost_bps=tx_cost_bps,
    )


@mcp.tool()
async def walk_forward_backtest_all(
    lookback_days: int = 365 * 5,
    window_days: int = 252,
    step_days: int = 63,
    tx_cost_bps: float = 5.0,
) -> dict:
    """Walk-forward all codified skills across the default 40+ symbol universe.

    Persists to .cache/backtests/walk_forward_*.json. Use to gate any skill
    whose deflated_sharpe < 0.5 — those are statistically indistinguishable
    from luck and should not emit live recommendations.
    """
    return await asyncio.to_thread(
        skill_bt_svc.walk_forward_all_skills,
        None,
        lookback_days=lookback_days,
        window_days=window_days,
        step_days=step_days,
        tx_cost_bps=tx_cost_bps,
        persist=True,
    )


@mcp.tool()
async def get_signal_decay_curve(
    action_filter: str | None = None,
    min_count: int = 5,
) -> dict:
    """Empirical decay curve across 1d/3d/5d/10d/21d/42d/63d horizons.

    Identifies the peak holding period for the signal type. Anything held
    beyond peak is signal decay — exits should happen at or before peak.
    """
    return await asyncio.to_thread(
        signal_history_svc.signal_decay_curve,
        signal_history_svc._DEFAULT_HORIZONS,
        action_filter=action_filter,
        min_count=min_count,
    )


@mcp.tool()
async def get_signal_source_attribution(
    horizon: int = 21,
    min_count: int = 5,
) -> dict:
    """Per-sub-signal alpha attribution (tech / fund / macro / sentiment).

    Buckets signals where one sub-score dominates (others near zero) so the
    dominant source's stand-alone alpha can be measured. Sources with
    avg_ret <= 0 or win_rate < 50% are adding noise — candidates for
    down-weighting in the composite engine.
    """
    return await asyncio.to_thread(
        signal_history_svc.per_signal_source_attribution,
        horizon, min_count=min_count,
    )


@mcp.tool()
async def calibrate_confidence_labels(horizon: int = 21) -> dict:
    """Map confidence (high/medium/low) to empirical hit rate.

    If HIGH does not outperform MEDIUM/LOW by >=10pp win-rate, the
    confidence label is uncalibrated and should not be relied on.
    """
    return await asyncio.to_thread(
        signal_history_svc.calibrate_confidence,
        horizon,
    )


# ────────────────────────────────────────────────────────────────────────────────
# Codified skill snapshots — read pre-computed background runner output
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_skill_snapshot(skill: str) -> dict:
    """Read the latest cached snapshot for a codified skill.

    Codified skills (10 of 13) run on a schedule and cache to disk so Claude
    can read pre-computed results without invoking a full skill walk. Snapshot
    includes summary, actionable items, alerts, and a `fresh` flag.

    LLM-only skills (adversarial-research / earnings-postmortem / stock-compare)
    are not codified — invoke them via Claude on demand.
    """
    snap = await asyncio.to_thread(skill_runner_svc.read_snapshot, skill)
    if snap is None:
        return {"error": f"no snapshot for skill={skill}",
                "available": skill_runner_svc.codified_skills()}
    return snap


@mcp.tool()
async def list_skill_snapshots() -> dict:
    """List all cached skill snapshots with freshness flags."""
    snaps = await asyncio.to_thread(skill_runner_svc.list_snapshots)
    return {
        "snapshots": snaps,
        "codified_skills": skill_runner_svc.codified_skills(),
        "llm_only_skills": skill_runner_svc.llm_only_skills(),
    }


# ── Phase 3: integrated signal MCP tools ────────────────────────────────────

def _active_tenant_hash() -> str | None:
    sid = _state.get("session_id")
    if not sid:
        return None
    import hashlib
    return hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16]


@mcp.tool()
async def get_integrated_signals() -> dict:
    """Return latest integrated 5-signal buy/sell signal per holding.

    Combines tech, fund, macro, sentiment, and skill evidence into one score
    + action per symbol. Reads from Postgres signal_history (latest row per
    symbol). PII-stripped.
    """
    thash = _active_tenant_hash()
    if not thash:
        return {"error": "no active session"}
    try:
        from app.db import init_pool, close_pool
        from app.db.repositories import signals_repo
        await init_pool()
        rows = await signals_repo.latest_for_tenant(thash)
        return {
            "as_of": rows[0]["ts"].isoformat() if rows else None,
            "signals": [
                {
                    "symbol": r["symbol"],
                    "action": r["action"],
                    "conviction": r.get("conviction"),
                    "score": float(r["score"])
                        if r.get("score") is not None else None,
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
                    "skill_evidence": r.get("skill_evidence"),
                }
                for r in rows
            ],
        }
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_signal_history(symbol: str, days: int = 30) -> dict:
    """Time-series of integrated signal for one symbol."""
    thash = _active_tenant_hash()
    if not thash:
        return {"error": "no active session"}
    try:
        from app.db import init_pool, close_pool
        from app.db.repositories import signals_repo
        await init_pool()
        rows = await signals_repo.history_for_symbol(
            thash, symbol.upper(), days=days,
        )
        return {
            "symbol": symbol.upper(),
            "days": days,
            "points": [
                {
                    "ts": r["ts"].isoformat() if r.get("ts") else None,
                    "score": float(r["score"])
                        if r.get("score") is not None else None,
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
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_discovery_picks(n: int = 5) -> dict:
    """Phase 13: top N new-symbol discovery picks from the last nightly
    scan (S&P500 + TSX60 + watchlist - already-held). Use to find new
    BUY ideas beyond your current portfolio.
    """
    thash = _active_tenant_hash()
    if not thash:
        return {"error": "no active session"}
    try:
        from app.db import init_pool, close_pool
        from app.services import discovery
        await init_pool()
        picks = await discovery.get_cached_top(thash)
        return {"picks": picks[:n], "n": min(n, len(picks))}
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_risk_gate_state() -> dict:
    """Phase 12: current portfolio-level risk gate state. Tells you if the
    system is currently allowing new BUYs, scaling them down, or halting
    them entirely due to drawdown / VIX / loss-streak / calibration
    triggers. Includes valid_until — gate auto-clears after that.
    """
    thash = _active_tenant_hash()
    if not thash:
        return {"error": "no active session"}
    try:
        from app.db import init_pool, close_pool
        from app.services import risk_gate
        await init_pool()
        state = await risk_gate.get_current(thash)
        return state.to_dict() if state else {"gate": None}
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_live_kpis(window_days: int = 30) -> dict:
    """Phase 10: live EV / PF / Sharpe / Sortino / Max DD / regime-breakdown
    over trailing window for the active session. The headline metrics you
    actually want to optimize for — not 'accuracy'.
    """
    thash = _active_tenant_hash()
    if not thash:
        return {"error": "no active session"}
    try:
        from app.db import init_pool, close_pool
        from app.services import live_metrics
        await init_pool()
        latest = await live_metrics.latest(thash, window_days=window_days)
        if latest is None:
            return await live_metrics.kpis(thash, window_days=window_days)
        return latest
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_calibration_report(horizon_days: int = 21) -> dict:
    """Phase 9: latest calibration report (Brier + ECE + reliability bins).
    Use to check whether predicted win-probabilities match realized win
    rates. ECE > 0.15 + overconfident verdict = the model is too sure of
    itself; trust the signal less for sizing.
    """
    try:
        from app.db import init_pool, close_pool
        from app.services.calibration import latest_report
        await init_pool()
        r = await latest_report(horizon_days=horizon_days)
        return {"report": r} if r else {"report": None,
                                         "reason": "no report yet"}
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_current_regime() -> dict:
    """Phase 8: return current market regime classification + per-skill
    multipliers in effect. Use to debug why a skill's score is up/down vs
    its baseline this tick.
    """
    try:
        from app.db import init_pool, close_pool
        from app.services import market_regime
        await init_pool()
        cur = await market_regime.get_current()
        if cur is None:
            return {"regime": None, "multipliers": {}}
        return {
            "regime": cur.to_dict(),
            "multipliers": market_regime.initial_multipliers_for(
                cur.composite,
            ),
        }
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_weights_history(limit: int = 30) -> dict:
    """Current 5-signal weights + last N audit versions from the nightly
    tuner (Phase 5 / 11). Use to see how the system is learning to weight
    different signal sources over time.
    """
    try:
        from app.db import init_pool, close_pool
        from app.db.repositories import weights_repo
        await init_pool()
        current = await weights_repo.current()
        history = await weights_repo.history(limit=limit)
        return {
            "current": {
                "version": current.get("version"),
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
                    "w_skill": float(h.get("w_skill") or 0),
                    "w_tech": float(h.get("w_tech") or 0),
                    "w_fund": float(h.get("w_fund") or 0),
                    "w_macro": float(h.get("w_macro") or 0),
                    "w_sentiment": float(h.get("w_sentiment") or 0),
                    "reason": h.get("reason"),
                }
                for h in history
            ],
        }
    finally:
        try:
            await close_pool()
        except Exception:
            pass


@mcp.tool()
async def get_sentry_issues(limit: int = 10) -> dict:
    """
    Pull top unresolved Sentry errors from the last 24h with stack frames.
    Use this to triage live production bugs. Returns issue count, title,
    culprit, occurrence count, affected users, and in-app stack frames
    (file/function/line/context) so Claude can propose fixes.
    """
    from app.services import sentry_monitor
    try:
        return sentry_monitor.build_digest(limit=limit)
    except RuntimeError as e:
        return {"error": str(e), "issues": []}


if __name__ == "__main__":
    mcp.run()
