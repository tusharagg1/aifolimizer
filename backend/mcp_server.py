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
)
from app.services.pii_filter import filter_portfolio, filter_user_context
from app.models.portfolio import PortfolioResponse
from ws_api import WSAPISession

mcp = FastMCP("aifolimizer")

_state: dict[str, Any] = {"session_id": None}
_SESSION_FILE = Path(__file__).parent / ".ws_session.json"

_MAX_SYMBOLS = 100
_VALID_ACCOUNT_TYPES = {"TFSA", "RRSP", "RESP", "Non-Reg", "Crypto", "LIRA", "FHSA", "Cash", ""}


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
def list_analysis_modes() -> list[dict]:
    """Lists the 9 institutional analysis frameworks available as Claude Code skills."""
    return [
        {"name": "portfolio_health", "style": "BlackRock Portfolio Builder",
         "tools_used": ["get_profile", "get_portfolio", "get_xray", "get_concentration_warnings"]},
        {"name": "risk_assessment", "style": "Bridgewater Risk Assessment",
         "tools_used": ["get_portfolio", "get_risk_metrics", "get_correlation_matrix", "get_concentration_warnings"]},
        {"name": "stock_analysis", "style": "Goldman Sachs + Citadel TA",
         "tools_used": ["get_portfolio", "get_fundamentals", "get_technicals", "get_news_headlines"]},
        {"name": "macro_impact", "style": "McKinsey Macro",
         "tools_used": ["get_portfolio", "get_macro_snapshot", "get_market_breadth"]},
        {"name": "dividend_strategy", "style": "Harvard Endowment Dividend",
         "tools_used": ["get_profile", "get_portfolio", "get_fundamentals"]},
        {"name": "earnings_analyzer", "style": "JPMorgan Earnings",
         "tools_used": ["get_portfolio", "get_earnings_calendar", "get_fundamentals"]},
        {"name": "sector_rotation", "style": "Renaissance / Sector Rotation",
         "tools_used": ["get_portfolio", "get_xray", "get_market_breadth"]},
        {"name": "tax_loss_review", "style": "Canadian tax-loss harvesting",
         "tools_used": ["get_tax_loss_candidates", "get_profile"]},
        {"name": "adversarial_research", "style": "Bull/Bear parallel agent synthesis",
         "tools_used": ["get_portfolio", "get_fundamentals", "get_technicals", "get_news_headlines"]},
    ]


if __name__ == "__main__":
    mcp.run()
