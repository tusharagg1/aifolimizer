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
    alpha_attribution as alpha_svc,
    trust_report as trust_svc,
    community as community_svc,
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


if __name__ == "__main__":
    mcp.run()
