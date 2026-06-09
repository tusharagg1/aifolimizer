"""
aifolimizer MCP server — exposes Wealthsimple portfolio + quant analytics as MCP tools.

Register with Claude Code:
  claude mcp add aifolimizer "<REPO_ROOT>\\backend\\.venv\\Scripts\\python.exe" "<REPO_ROOT>\\backend\\mcp_server.py"

Reads WS_EMAIL and WS_PASSWORD from .env so Claude never sees credentials.
All portfolio data passed through pii_filter before returning to Claude.
"""

import asyncio
import importlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# Sentry init — opt-in via SENTRY_DSN. Gated by env-var presence to avoid
# importing app.core.config (and its dependency chain) when sentry is off,
# preserving the fast MCP cold-start budget.
if os.environ.get("SENTRY_DSN"):
    from app.core.config import settings as _settings
    from app.core.sentry import init_sentry as _init_sentry

    _init_sentry(_settings)


class _LazyModule:
    """Defer import until first attribute access.

    MCP handshake requires fast cold-start: top-level eager imports of all
    service modules (yfinance/ta/pandas) push startup past Claude Code's
    schema-fetch window, leaving aifolimizer tools absent from the session.
    """

    __slots__ = ("_target", "_mod")

    def __init__(self, target: str) -> None:
        self._target = target
        self._mod = None

    def __getattr__(self, attr: str) -> Any:
        if self._mod is None:
            self._mod = importlib.import_module(self._target)
        return getattr(self._mod, attr)


wealthsimple = _LazyModule("app.services.wealthsimple")
market_data = _LazyModule("app.services.market_data")
macro = _LazyModule("app.services.macro")
portfolio_analytics = _LazyModule("app.services.portfolio_analytics")
quant = _LazyModule("app.services.quant")
fundamentals_svc = _LazyModule("app.services.fundamentals")
technicals_svc = _LazyModule("app.services.technicals")
technicals_intraday_svc = _LazyModule("app.services.technicals_intraday")
news_svc = _LazyModule("app.services.news")
crypto_svc = _LazyModule("app.services.crypto_data")
alerts_svc = _LazyModule("app.services.alerts")
backtest_svc = _LazyModule("app.services.backtest")
positioning_svc = _LazyModule("app.services.positioning")
data_router = _LazyModule("app.services.data_router")
skill_bt_svc = _LazyModule("app.services.skill_backtest")
paper_trade_svc = _LazyModule("app.services.paper_trade")
signal_history_svc = _LazyModule("app.services.signal_history")
alpha_svc = _LazyModule("app.services.alpha_attribution")
trust_svc = _LazyModule("app.services.trust_report")
community_svc = _LazyModule("app.services.community")
options_svc = _LazyModule("app.services.options")
trade_ticket_svc = _LazyModule("app.services.trade_ticket")
memory_svc = _LazyModule("app.services.memory")
decision_mem_svc = _LazyModule("app.services.decision_memory")
shadow_svc = _LazyModule("app.services.shadow_account")
run_card_svc = _LazyModule("app.services.run_card")
skill_runner_svc = _LazyModule("app.services.skill_runner")
geopolitical_svc = _LazyModule("app.services.geopolitical")
recommendations_svc = _LazyModule("app.services.recommendations")
watchlist_svc = _LazyModule("app.services.watchlist")
trade_ideas_svc = _LazyModule("app.services.trade_ideas")
optimizer_svc = _LazyModule("app.services.portfolio_optimizer")
dcf_svc = _LazyModule("app.services.dcf")
backtest_stats_svc = _LazyModule("app.services.backtest_stats")
hypotheses_svc = _LazyModule("app.services.hypotheses")
boc_svc = _LazyModule("app.services.boc_valet")
crypto_sentiment_svc = _LazyModule("app.services.crypto_sentiment")
statcan_svc = _LazyModule("app.services.statcan")
finnhub_extras_svc = _LazyModule("app.services.finnhub_extras")
google_trends_svc = _LazyModule("app.services.google_trends")
fama_french_svc = _LazyModule("app.services.fama_french")
defillama_svc = _LazyModule("app.services.defillama")
edgar_filings_svc = _LazyModule("app.services.edgar_filings")

from app.services.pii_filter import (
    filter_personal_context_full,
    filter_portfolio,
    filter_user_context,
)
from app.services import personal_context as personal_context_svc
from app.models.personal_context import PersonalContext
from app.models.portfolio import PortfolioResponse
from ws_api import WSAPISession

mcp = FastMCP("aifolimizer")

_state: dict[str, Any] = {"session_id": None, "file_mtime": 0.0}
_session_lock = asyncio.Lock()

# WS blocking calls run on a dedicated pool, NOT the default asyncio.to_thread
# executor. A stalled Wealthsimple/Cloudflare socket used to occupy default-pool
# workers and starve every other tool — including ones with no network at all
# (get_personal_context) — so the whole MCP server appeared to "stick".
_WS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ws-op")
# Hard async-layer ceiling on a single WS operation. The requests-level timeout
# in wealthsimple.py is the first line of defense; this guarantees the tool
# call RAISES (visible error) instead of hanging forever even if that path is
# bypassed (stale ws-api, slow-drip socket). Override via WS_OP_TIMEOUT_S.
_WS_OP_TIMEOUT_S = float(os.getenv("WS_OP_TIMEOUT_S", "90") or 90)


async def _ws_call(fn, *args):
    """Run a blocking Wealthsimple call on the dedicated pool with a hard ceiling.

    Replaces bare ``asyncio.to_thread(ws_fn, ...)`` for every WS-touching call so
    a network stall surfaces as a bounded RuntimeError, never an infinite stick,
    and can't contend with non-WS tools for the shared thread pool.
    """
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(_WS_EXECUTOR, lambda: fn(*args))
    try:
        return await asyncio.wait_for(fut, timeout=_WS_OP_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            f"Wealthsimple request timed out after {_WS_OP_TIMEOUT_S:.0f}s "
            "(network stall or stale token). Retry; if it persists, re-auth: "
            "cd backend && .venv/Scripts/python mcp_login.py"
        ) from e


# Unified WS session file — same path _persist_session rewrites on token
# refresh, so headless runs survive rotation for the full refresh-token life.
_SESSION_FILE = Path.home() / ".aifolimizer" / "ws_session.json"


def _session_file_mtime() -> float:
    try:
        return _SESSION_FILE.stat().st_mtime
    except OSError:
        return 0.0


_MAX_SYMBOLS = 100
_VALID_ACCOUNT_TYPES = {"TFSA", "RRSP", "RESP", "Non-Reg", "Crypto", "LIRA", "FHSA", "Cash", ""}


def _load_cached_session() -> str | None:
    """Try to restore a session from the file written by mcp_login.py.

    Reads the persisted ws_session.json with bounded retries so a writer
    mid-rotation (mfa_popup, _persist_session) is given a small window to
    finish before we declare the file corrupt and force a full re-login.
    Atomic-rename writers make a partial-read window unlikely, but slow
    disks + AV scanners can still race.
    """
    if not _SESSION_FILE.exists():
        return None
    payload = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            payload = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
            break
        except (json.JSONDecodeError, OSError) as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    if payload is None:
        print(f"[MCP] session file unreadable after retries: {type(last_err).__name__}: {last_err}", flush=True)
        return None
    try:
        ws_session = WSAPISession.from_json(payload["session_json"])
        email = payload["email"]
        result = wealthsimple._finalize_session(ws_session, email)
        _state["file_mtime"] = _session_file_mtime()
        return result["session_id"]
    except Exception as e:
        print(f"[MCP] cached session load failed: {type(e).__name__}: {e}", flush=True)
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
    """Login lazily on first tool call. Re-login on session expiry.

    Serialized via _session_lock so concurrent tool calls don't double-load
    the session file or fire two _finalize_session sweeps against WS at the
    same time (rotates the access token mid-flight, which used to surface
    as random 401s and "MFA required" errors).

    If ws_session.json has been rewritten since our last load (e.g.
    aifolimizer-launch.ps1 ran mfa_popup, or user re-ran mcp_login.py),
    discard the in-memory session and reload from disk so the running
    MCP process picks up the fresh token without a VSCode restart.
    """
    async with _session_lock:
        current_mtime = _session_file_mtime()
        if current_mtime and current_mtime > _state["file_mtime"]:
            _state["session_id"] = None

        if _state["session_id"] and wealthsimple.get_session(_state["session_id"]):
            return _state["session_id"]

        # Try cached session from mcp_login.py first. Retry once on transient
        # failure (network blip during _finalize_session) before declaring
        # the file dead and demanding an MFA re-login.
        sid = await _ws_call(_load_cached_session)
        if not sid:
            await asyncio.sleep(0.5)
            sid = await _ws_call(_load_cached_session)
        if sid:
            _state["session_id"] = sid
            return sid

        # Fall back to .env credentials (no-MFA accounts)
        email = os.getenv("WS_EMAIL", "")
        password = os.getenv("WS_PASSWORD", "")
        if not email or not password:
            raise RuntimeError(
                "No cached WS session and no WS_EMAIL/WS_PASSWORD in env. "
                "Run: cd backend && .venv/Scripts/python mcp_login.py"
            )

        try:
            result = await _ws_call(wealthsimple.login, email, password)
        except Exception as e:
            raise RuntimeError(
                f"WS login failed ({type(e).__name__}: {e}). "
                "If WS rejected the token (password change / revoked), run: "
                "cd backend && .venv/Scripts/python mcp_login.py"
            ) from e
        if result.get("needs_otp"):
            raise RuntimeError(
                "WS requires MFA. Run: cd backend && .venv/Scripts/python mcp_login.py "
                "and enter the 6-digit code from your email/authenticator."
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
    net_deposits_cad = float(session.get("net_deposits_cad") or 0.0)
    simple_return_pct = session.get("simple_return_pct")
    if account_id and account_id in per_account:
        acc = per_account[account_id]
        cash_balance = float(acc.get("cash_balance") or 0.0)
        usd_cash_balance = float(acc.get("usd_cash_balance") or 0.0)
        ws_account_total = float(acc.get("invested_value") or 0.0)
        unrealized_pnl_cad = float(acc.get("unrealized_pnl_cad") or 0.0)
    else:
        cash_balance = sum(a.cash_balance for a in profile.accounts)
        usd_cash_balance = sum(float(a.get("usd_cash_balance") or 0.0) for a in per_account.values())
        ws_account_total = sum(a.invested_value for a in profile.accounts)
        unrealized_pnl_cad = float(session.get("unrealized_pnl_cad") or 0.0)

    if account_id:
        raw_positions = await _ws_call(wealthsimple.get_positions, session_id, account_id)
    else:
        raw_positions = await _ws_call(wealthsimple.get_all_positions, session_id)
    return await asyncio.to_thread(
        market_data.enrich,
        raw_positions,
        cash_balance,
        ws_account_total,
        unrealized_pnl_cad,
        usd_cash_balance,
        net_deposits_cad,
        simple_return_pct,
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


# ────────────────────────────────────────────────────────────────────────────────
# Personal-finance context (life context: salary/age/room/expenses/etc.)
# Separate from get_profile (which returns WS-account data only).
# All fields optional — skills must degrade gracefully when present=false.
# ────────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_personal_context() -> dict:
    """Return the user's personal-finance context (life context, not WS data).

    Shape: {present: bool, context?: {...}, derived?: {...}, context_hash: str}.
    `derived` includes marginal_tax_rate_pct, emergency_fund_target_cad,
    fhsa_priority_first, account_waterfall — pre-computed so skills don't
    duplicate the math. Returns {present: false} when user has not set up.

    Personal-portfolio skills (cash-deployment, tax-loss-review, dividend-strategy,
    portfolio-health, risk-assessment, daily-briefing, pre-trade-check, etc.)
    should call this AFTER get_profile to personalize. Stock-research skills
    (stock-analysis, momentum-scanner, adversarial-research) should NOT call
    this — keeps research bias-free.
    """
    env = await asyncio.to_thread(personal_context_svc.envelope)
    return filter_personal_context_full(env)


@mcp.tool()
async def set_personal_context_field(field: str, value: Any) -> dict:
    """Set one personal-context field. Validates via Pydantic; idempotent.

    field: e.g. 'province', 'gross_salary_cad', 'room_tfsa_cad', 'fthb_yes'.
    See PersonalContext model for full field list.
    Pass null/empty value to clear the field.
    """
    if value == "":
        value = None
    new_ctx = await asyncio.to_thread(personal_context_svc.update_field, field, value)
    return {
        "set": True,
        "field": field,
        "value": getattr(new_ctx, field, None),
        "context_hash": personal_context_svc.context_hash(new_ctx),
    }


@mcp.tool()
async def set_personal_context_bulk(payload: dict) -> dict:
    """Replace personal context with a full payload. Validates entire object.

    Use this for the paste-template flow: user fills the JSON template and
    submits the whole thing at once. Atomic — partial-write impossible.
    Unknown fields are ignored.
    """
    valid_fields = set(PersonalContext.model_fields.keys())
    cleaned = {k: v for k, v in (payload or {}).items() if k in valid_fields and v not in ("", None, [])}
    ctx = PersonalContext.model_validate(cleaned)
    await asyncio.to_thread(personal_context_svc.save, ctx)
    return {
        "set": True,
        "fields_set": sorted(cleaned.keys()),
        "context_hash": personal_context_svc.context_hash(ctx),
    }


@mcp.tool()
async def clear_personal_context() -> dict:
    """Delete the local personal-context file. Returns whether a file was deleted."""
    deleted = await asyncio.to_thread(personal_context_svc.clear)
    return {"deleted": deleted}


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
async def get_concentration_warnings(
    account_id: str = "", single_max_pct: float = 10.0, sector_max_pct: float = 35.0
) -> list[dict]:
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


@mcp.tool()
async def optimize_portfolio(
    account_id: str = "",
    top_n: int = 20,
    use_analyst_views: bool = True,
    risk_free_rate: float = 0.045,
) -> dict:
    """
    Mean-variance portfolio optimization (max Sharpe) via PyPortfolioOpt.

    Returns the optimal weight per holding and the add/trim CHANGES vs your
    current allocation that maximise risk-adjusted return (Ledoit-Wolf
    shrinkage covariance, max 35% per name, longs only). When analyst price
    targets exist they are blended in as Black-Litterman forward-return views.

    top_n: optimise over the top N holdings by weight (default 20).
    use_analyst_views: blend analyst targets as BL views (default True).
    risk_free_rate: annual risk-free rate for the Sharpe calc (default ~4.5%).

    Answers "how much of each position to add/trim for best risk-adjusted
    return". Output is weights/changes in % only — no dollar balances.
    Cached 1h per symbol set.
    """
    portfolio = await _load_portfolio(account_id)
    if not portfolio.positions:
        return {"error": "No positions in portfolio"}

    top_positions = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:top_n]
    positions = [{"symbol": p.symbol, "weight": p.weight} for p in top_positions]

    analyst_targets: dict[str, float] | None = None
    if use_analyst_views:
        symbols = [p["symbol"] for p in positions]
        funda = await asyncio.to_thread(fundamentals_svc.get_fundamentals, symbols)
        analyst_targets = {
            s: d["analyst_target_price"]
            for s, d in funda.items()
            if isinstance(d, dict) and d.get("analyst_target_price")
        } or None

    return await asyncio.to_thread(optimizer_svc.optimize, positions, analyst_targets, risk_free_rate)


@mcp.tool()
async def get_dcf_valuation(symbol: str) -> dict:
    """
    Deterministic discounted-cash-flow intrinsic value for a US-listed ticker.

    5-year FCF projection + Gordon terminal value, anchored to the SEC EDGAR
    free-cash-flow series, discounted at a CAPM cost of equity. Returns a
    per-share fair value, upside vs current price, a discount-rate × terminal-
    growth sensitivity grid, and the FCF history. Gives price targets a
    quantitative spine instead of a free-hand estimate.

    Caveats: US tickers only (no .TO); net debt ignored (≈ enterprise value
    per share); unreliable when latest FCF is negative (returns a note). One
    valuation lens, not a verdict. No API key. Cached via SEC facts (24h).
    """
    return await asyncio.to_thread(dcf_svc.dcf_valuation, symbol)


@mcp.tool()
async def get_backtest_confidence(
    symbol: str,
    strategy: str = "sma_cross",
    period: str = "2y",
    tx_cost_bps: float = 5.0,
    n_boot: int = 1000,
    drawdown_threshold_pct: float = -25.0,
) -> dict:
    """
    Confidence intervals for a single-symbol backtest via resampling.

    Moving-block bootstrap of daily returns gives 5/25/50/75/95th-percentile
    bands on total return, CAGR, and max drawdown (preserves autocorrelation).
    An order-shuffle Monte-Carlo gives the probability of a drawdown worse than
    `drawdown_threshold_pct`. Turns "CAGR 14%" into "CAGR 14% [5-95th: 2-23%],
    20% chance of >25% drawdown" — material for real-money sizing.

    strategy: 'sma_cross' (default) or 'rsi_swing'. Reuses the live backtest
    engine; no new data sources.
    """
    return await asyncio.to_thread(
        backtest_stats_svc.confidence_intervals,
        symbol,
        strategy,
        period,
        tx_cost_bps,
        n_boot,
        drawdown_threshold_pct,
    )


@mcp.tool()
async def run_lookahead_sentinel(symbol: str, period: str = "2y", tx_cost_bps: float = 5.0) -> dict:
    """
    Lookahead-bias guard: inject a perfect-foresight signal into the backtest
    engine. A correctly-lagged engine cannot exploit it; if the foresight
    signal earns abnormal returns, lookahead has leaked. Returns passed bool +
    peek-vs-buy-hold CAGR. Cheapest insurance against a backtest that lies.
    """
    return await asyncio.to_thread(backtest_stats_svc.lookahead_sentinel, symbol, period, tx_cost_bps)


@mcp.tool()
async def log_hypothesis(
    thesis: str,
    ticker: str = "",
    acceptance_criteria: str = "",
    invalidation_criteria: str = "",
    horizon_days: int = 90,
    linked_run_card: str = "",
) -> dict:
    """
    Record a durable investment thesis ("believe X because Y; confirmed if Z,
    refuted if W") with open status. Complements per-trade decision history by
    tracking un-executed / in-flight ideas so research converts to action and
    theses are not re-litigated. Stored as JSONL. Returns the record + its id.
    """
    return await asyncio.to_thread(
        hypotheses_svc.log_hypothesis,
        thesis,
        ticker,
        acceptance_criteria,
        invalidation_criteria,
        horizon_days,
        linked_run_card,
    )


@mcp.tool()
async def list_hypotheses(status: str = "", ticker: str = "") -> list[dict]:
    """
    List recorded investment theses, newest first. Filter by status
    (open/confirmed/refuted/expired) and/or ticker. Call at the start of
    research on a name to surface prior open theses and avoid duplicate work.
    """
    return await asyncio.to_thread(hypotheses_svc.list_hypotheses, status, ticker)


@mcp.tool()
async def resolve_hypothesis(hypothesis_id: str, status: str, resolution_note: str = "") -> dict:
    """
    Mark a thesis confirmed, refuted, or expired with a resolution note.
    Closes the research-to-outcome loop. Returns the updated record.
    """
    return await asyncio.to_thread(hypotheses_svc.resolve_hypothesis, hypothesis_id, status, resolution_note)


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


@mcp.tool()
async def get_geopolitical_signals(lookback_hours: int = 24) -> dict:
    """
    Geopolitical tension index from GDELT 2.0 Doc API (free, no key).
    Scans last lookback_hours of global news for conflict, trade, sanctions,
    energy, and political-instability themes.

    Returns:
      global_tension_index (0-100): weighted mean across regions
      level: "high" | "moderate" | "low"
      regions: {Americas, Europe, Asia_Pacific, Middle_East, Emerging} each with
               tension_score (0-100), article_count, level
      hot_regions: regions where tension_score >= 60
      categories_detected: event types found (armed_conflict, trade_tensions,
                           sanctions, macro_stress, energy_events, ...)
      market_implications: list of ETF/sector impacts (e.g. "XLE (Energy +)")
      articles_analyzed: total article count processed

    lookback_hours: 6–168 (defaults 24h). Use 48-72h for broader signal.
    Cached 1h. Use alongside get_macro_snapshot in macro-impact skill.
    """
    return await asyncio.to_thread(geopolitical_svc.get_geopolitical_signals, lookback_hours)


@mcp.tool()
async def get_boc_snapshot() -> dict:
    """
    Bank of Canada macro via Valet API (free, no key). Official CAD source —
    complements FRED (US-centric). Returns BoC policy/target rate, USD/CAD,
    Government of Canada benchmark bond yields (2y/5y/10y), and the 10y-2y curve
    slope (curve_10y_2y_bps + curve_signal: inverted/normal). Cached 12h.
    Use in macro-impact / daily-briefing for Canadian rate + FX context.
    """
    return await asyncio.to_thread(boc_svc.boc_snapshot)


@mcp.tool()
async def get_statcan_snapshot() -> dict:
    """
    Statistics Canada real-economy data via WDS API (free, no key). Returns CPI
    all-items with computed cpi_yoy_pct (headline inflation) and the headline
    unemployment_rate_pct, each with ref_period. Pairs with get_boc_snapshot
    (rates/FX) for a full Canadian macro picture. Cached 12h.
    """
    return await asyncio.to_thread(statcan_svc.statcan_snapshot)


@mcp.tool()
async def get_crypto_fear_greed(limit: int = 30) -> dict:
    """
    Crypto Fear & Greed Index via alternative.me (free, no key). 0=extreme fear,
    100=extreme greed. Returns current_value, classification, avg_7d, avg_30d, and
    history. Extreme fear = historical accumulation zone; extreme greed = froth.
    Pairs with get_crypto_data for the crypto sleeve. limit: 1-365 days. Cached 1h.
    """
    return await asyncio.to_thread(crypto_sentiment_svc.crypto_fear_greed, limit)


@mcp.tool()
async def get_finnhub_news(ticker: str, days: int = 7) -> dict:
    """
    Company news + crude bull/bear headline tally via Finnhub (free tier; needs
    FINNHUB_KEY). Returns article_count, bull/bear_headlines, net_sentiment
    (-100..+100), signal (bullish/bearish/neutral), and sample_headlines.
    days: 1-30. Returns {"error": "no_api_key"} if key unset. Cached 30m.
    """
    return await asyncio.to_thread(finnhub_extras_svc.finnhub_news, ticker, days)


@mcp.tool()
async def get_insider_sentiment(ticker: str) -> dict:
    """
    Insider sentiment MSPR trend via Finnhub (free tier; needs FINNHUB_KEY).
    avg_mspr (-100..100; positive = net insider buying pressure), net_signal
    (bullish/bearish/neutral), and last 6 months of points. Complements
    get_insider_activity (transaction-level). Cached 30m.
    """
    return await asyncio.to_thread(finnhub_extras_svc.finnhub_insider_sentiment, ticker)


@mcp.tool()
async def get_economic_calendar() -> dict:
    """
    Upcoming macro releases via Finnhub. NOTE: economic calendar is PREMIUM on
    most Finnhub plans — returns {"error": "premium_endpoint"} on a free key.
    For free Canadian macro use get_boc_snapshot + get_statcan_snapshot instead.
    Cached 30m.
    """
    return await asyncio.to_thread(finnhub_extras_svc.finnhub_economic_calendar)


@mcp.tool()
async def get_search_interest(keywords: list[str], timeframe: str = "today 3-m") -> dict:
    """
    Google Trends search interest via pytrends (free, no key; unofficial). Per
    keyword: current_interest (0-100), change_4w, peak, trend (rising/falling/flat).
    Search-interest spikes are a retail-demand/crowding proxy — pair with
    get_positioning_signals. Rate-limited; degrades gracefully on 429. Up to 5
    keywords. timeframe: pytrends syntax (e.g. "today 3-m", "today 12-m"). Cached 6h.
    """
    return await asyncio.to_thread(google_trends_svc.trends_interest, keywords, timeframe)


@mcp.tool()
async def get_factor_snapshot() -> dict:
    """
    Fama-French factor returns via Ken French Data Library (free, no key). Latest
    daily + trailing 21d/252d returns for Mkt-RF (market), SMB (size), HML (value),
    RMW (profitability), CMA (investment), Mom (momentum), RF. factor_legend maps
    codes→names. Use to read which style factors are working before sizing tilts.
    Cached 24h.
    """
    return await asyncio.to_thread(fama_french_svc.factor_snapshot)


@mcp.tool()
async def get_factor_exposure(ticker: str, lookback_days: int = 252) -> dict:
    """
    OLS regression of a ticker's excess returns on Fama-French 5 + momentum (free,
    no key). Returns loadings (beta to market/size/value/profitability/investment/
    momentum), annualized alpha %, R², n_obs. Reveals hidden style tilts vs a plain
    market beta. US factors → non-US tickers directional only. lookback_days 60-1260.
    """
    return await asyncio.to_thread(fama_french_svc.factor_exposure, ticker, lookback_days)


@mcp.tool()
async def get_crypto_macro() -> dict:
    """
    DefiLlama crypto macro (free, no key). Total DeFi TVL + top chains by TVL, and
    aggregate stablecoin market cap + top issuers (all in $B). Rising stablecoin
    supply = dry powder entering crypto; falling TVL = risk-off. Macro-health
    context for the crypto sleeve alongside get_crypto_data + get_crypto_fear_greed.
    Cached 30m.
    """
    return await asyncio.to_thread(defillama_svc.crypto_macro)


@mcp.tool()
async def get_recent_filings(ticker: str, forms: list[str] = [], limit: int = 15) -> dict:
    """
    Recent material SEC filings via EDGAR (free, no key). Returns form, filed date,
    report_period, description, and a direct document URL per filing. Default filter
    = material forms (8-K, 10-K, 10-Q, 6-K, 20-F, S-1, proxy, 13D/G); pass `forms`
    to override (e.g. ["8-K"]). Event-detection feed for catalysts. US-listed only.
    limit 1-50. Cached 6h.
    """
    return await asyncio.to_thread(edgar_filings_svc.recent_filings, ticker, forms or None, limit)


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
        results[sym] = await asyncio.to_thread(fundamentals_svc.get_sec_financials, sym)
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
async def get_technicals_mtf(
    account_id: str = "",
    symbols: list[str] = [],
    timeframes: list[str] = ["1d", "1wk"],
) -> dict:
    """
    Multi-timeframe technical analysis. Returns per-symbol signals for each
    timeframe plus a mtf_confluence summary.

    timeframes: list of intervals to analyse — "1d" (daily), "1wk" (weekly),
    "1mo" (monthly). Defaults to ["1d", "1wk"].

    Per-TF fields: trend, rsi_14, rsi_signal, macd_hist, signal_agreement,
    signal_conviction, technical_score, stage, obv_trend, adx_signal,
    sma_200, current_price.

    mtf_confluence fields:
      trend_alignment: "aligned_uptrend" | "aligned_downtrend" | "mixed" | "no_data"
      signal_alignment: "aligned_bullish" | "aligned_bearish" | "mixed" | "no_data"
      overall: "strong_bullish" | "strong_bearish" | "mixed" | "neutral"

    Use when daily signal conflicts with weekly trend — MTF resolves ambiguity.
    Cached 1h per (symbol, timeframes). If symbols=[], uses top 15 by weight.
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
    valid = {"1d", "1wk", "1mo"}
    tfs = [t for t in timeframes if t in valid] or ["1d", "1wk"]
    return await asyncio.to_thread(technicals_svc.get_technicals_mtf, symbols, tfs)


@mcp.tool()
async def get_technicals_intraday(account_id: str = "", symbols: list[str] = []) -> dict:
    """
    Intraday indicators on 5-minute bars: VWAP, opening range (first 30 min) +
    break direction, RSI(2) Connors mean-reversion, RSI(14), ATR(14) for stop
    sizing, EMA(9/20) trend, volume spike vs 20-bar avg, session volume vs 5-day
    avg, overnight gap %, composite intraday_score (0-1).

    Use for: day-trade entries, pre-trade-check on intraday horizon,
    daily-briefing intraday addendum, catalyst-day momentum scans.

    NOT for swing/position — those use get_technicals (daily bars).

    Cached 60s — intraday bars stale fast. Yahoo 5m data is delayed ~15min.
    """
    session_id = await _ensure_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    _validate_account_id(account_id, profile)

    if not symbols:
        portfolio = await _load_portfolio(account_id)
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:10]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)
    return await asyncio.to_thread(technicals_intraday_svc.get_technicals_intraday, symbols)


@mcp.tool()
async def get_earnings_calendar(
    account_id: str = "",
    symbols: list[str] = [],
) -> list[dict]:
    """
    Next earnings dates for all portfolio holdings, sorted ascending.
    Flags entries in the next 14 days as is_upcoming=True.
    Pass `symbols` to also include non-held names (e.g. watchlist tickers);
    they are unioned with holdings. Each entry has held=True/False.
    Useful for pre-earnings analysis — call this before earnings_analyzer skill.
    """
    portfolio = await _load_portfolio(account_id)
    held = [p.symbol for p in portfolio.positions]
    held_set = set(held)
    extra = _validate_symbols([s.strip().upper() for s in symbols if s and s.strip()])
    all_syms = list(dict.fromkeys(held + extra))  # dedupe, preserve order
    fund_data = await asyncio.to_thread(fundamentals_svc.get_fundamentals, all_syms)

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
            results.append(
                {
                    "symbol": sym,
                    "earnings_date": ed[:10],
                    "days_until": days_until,
                    "is_upcoming": today <= ed_date <= cutoff,
                    "held": sym in held_set,
                }
            )
        except Exception:
            continue
    return sorted(results, key=lambda r: r["earnings_date"])


@mcp.tool()
async def get_dividend_calendar(
    account_id: str = "",
    symbols: list[str] = [],
) -> list[dict]:
    """
    Upcoming ex-dividend + pay dates for holdings (and any extra `symbols`),
    sorted by ex-dividend date ascending. Only names with a known forward
    ex-dividend date are returned.

    Per entry: symbol, ex_dividend_date, dividend_pay_date, days_until_ex,
    is_upcoming (ex-div within 14 days), dividend_yield, held (True/False).

    Use for: don't-sell-before-ex-div timing, dividend-capture windows, and
    placing income names in the right account for the tax year. Public market
    data (yfinance calendar) — no PII. Reuses the get_fundamentals fetch, so
    no extra HTTP when fundamentals are already warm.
    """
    portfolio = await _load_portfolio(account_id)
    held = [p.symbol for p in portfolio.positions]
    held_set = set(held)
    extra = _validate_symbols([s.strip().upper() for s in symbols if s and s.strip()])
    all_syms = list(dict.fromkeys(held + extra))
    fund_data = await asyncio.to_thread(fundamentals_svc.get_fundamentals, all_syms)

    from datetime import date, timedelta

    today = date.today()
    cutoff = today + timedelta(days=14)
    results = []
    for sym, data in fund_data.items():
        ex = data.get("ex_dividend_date")
        if not ex:
            continue
        try:
            ex_date = date.fromisoformat(ex[:10])
        except Exception:
            continue
        if ex_date < today:
            continue
        results.append(
            {
                "symbol": sym,
                "ex_dividend_date": ex[:10],
                "dividend_pay_date": (data.get("dividend_pay_date") or "")[:10] or None,
                "days_until_ex": (ex_date - today).days,
                "is_upcoming": today <= ex_date <= cutoff,
                "dividend_yield": data.get("dividend_yield"),
                "held": sym in held_set,
            }
        )
    return sorted(results, key=lambda r: r["ex_dividend_date"])


@mcp.tool()
async def get_watchlist() -> list[dict]:
    """
    User-defined watchlist — symbols being tracked but not held.
    Returns symbol, name, asset_class, notes, added_at. No PII (no positions).
    Pair with get_trade_ideas or get_earnings_calendar(symbols=...) to fold
    watchlist names into ranking / earnings checks.
    """
    items = await asyncio.to_thread(watchlist_svc.load_watchlist)
    return [
        {
            "symbol": i.get("symbol"),
            "name": i.get("name") or i.get("symbol"),
            "asset_class": i.get("asset_class") or "stock",
            "notes": i.get("notes", ""),
            "added_at": i.get("added_at"),
        }
        for i in items
        if i.get("symbol")
    ]


@mcp.tool()
async def get_trade_ideas(
    top_n: int = 3,
    include_watchlist: bool = True,
    min_risk_reward: float = 1.5,
) -> dict:
    """
    Top-N actionable trade ideas across holdings (+ watchlist), ranked by score.

    Reuses the backend recommendation engine (same scoring as the dashboard /
    nightly signals) — no duplicated logic. Filters out non-actionable names
    (HOLD/WATCH/PASS/NO_EDGE), names whose entry timing says wait for a pullback,
    and ideas below `min_risk_reward`. Each idea carries entry/stop/target so it
    is directly tradeable. Use for "top stocks to trade today" / morning brief.
    """
    portfolio = await _load_portfolio("")
    held = {p.symbol for p in portfolio.positions if p.symbol}
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
        if p.symbol
    ]
    if include_watchlist:
        wl = await asyncio.to_thread(watchlist_svc.load_watchlist)
        for i in wl:
            sym = i.get("symbol")
            if not sym or sym in held:
                continue
            positions.append(
                {
                    "symbol": sym,
                    "name": i.get("name") or sym,
                    "weight": 0.0,
                    "market_value_cad": 0.0,
                    "total_return_pct": 0.0,
                    "currency": "CAD" if sym.endswith((".TO", ".V")) else "USD",
                    "asset_class": i.get("asset_class") or "stock",
                    "sector": "",
                }
            )

    recs = await asyncio.to_thread(
        recommendations_svc.get_recommendations,
        positions,
        None,
    )

    ranked = trade_ideas_svc.rank_trade_ideas(
        recs,
        held,
        top_n=top_n,
        min_risk_reward=min_risk_reward,
    )
    ranked["universe"] = "holdings+watchlist" if include_watchlist else "holdings"

    # Risk-gate surfacing brake: the sync recommendation engine is gate-unaware
    # (risk_gate is async/DB-backed), so a portfolio-level halt/reduce must be
    # applied here at the surfacing layer — otherwise halted BUYs still show as
    # "top trades". Best-effort: never block idea generation if pool/gate is down.
    try:
        thash = _active_tenant_hash()
        if thash:
            from app.db import init_pool, close_pool
            from app.services import risk_gate

            await init_pool()
            try:
                state = await risk_gate.get_current(thash)
            finally:
                try:
                    await close_pool()
                except Exception:
                    pass
            if state:
                ranked["risk_gate"] = {
                    "status": state.status,
                    "size_multiplier": state.size_multiplier,
                    "reasons": state.reasons,
                }
                if state.status == "halt":
                    longs = [i for i in ranked["ideas"] if i.get("action") in ("BUY", "ADD")]
                    ranked["ideas"] = [i for i in ranked["ideas"] if i.get("action") not in ("BUY", "ADD")]
                    ranked["suppressed_by_risk_gate"] = len(longs)
                    ranked["risk_gate"]["note"] = (
                        "Portfolio risk-gate HALT — new BUY/ADD ideas suppressed; defensive (SELL/TRIM) ideas retained."
                    )
                elif state.status == "reduce_size":
                    ranked["risk_gate"]["note"] = (
                        f"Portfolio risk-gate REDUCE_SIZE ({state.size_multiplier}x) — "
                        "size fresh entries down or defer new BUYs."
                    )
    except Exception:
        ranked.setdefault("risk_gate", None)
    return ranked


@mcp.tool()
async def get_earnings_results(account_id: str = "", symbols: list[str] = [], quarters: int = 4) -> dict:
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
async def get_positioning_signals(account_id: str = "", symbols: list[str] = []) -> dict:
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
async def snapshot_positioning_history(account_id: str = "", symbols: list[str] = [], top_n: int = 15) -> dict:
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
        symbols,
        lookback_days,
        score_delta_threshold,
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
    return {sym: articles[: max(1, min(limit, 10))] for sym, articles in raw.items()}


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
async def get_crypto_data(account_id: str = "", symbols: list[str] = []) -> dict:
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
    items = await asyncio.to_thread(alerts_svc.read_recent_history, since_hours, limit)
    return {"since_hours": since_hours, "count": len(items), "alerts": items}


@mcp.tool()
async def run_alerts_now(
    account_id: str = "",
    price_drop_pct: float = 5.0,
    dry_run: bool = True,
) -> dict:
    """
    Evaluate alert rules against live portfolio and append triggers to history.
    dry_run=True (default): log + dedup but do NOT push to Telegram.
    dry_run=False: also push via Telegram bot if credentials are set.
    Returns counts: {triggered, pushed, deduped}.
    Use sparingly — this fetches live WS + yfinance data.
    """
    portfolio = await _load_portfolio(account_id)
    triggered = await asyncio.to_thread(alerts_svc.evaluate, portfolio, price_drop_pct=price_drop_pct)
    from app.core.config import settings as _cfg

    tg_token = None if dry_run else _cfg.telegram_bot_token
    tg_chat = None if dry_run else _cfg.telegram_chat_id
    counts = await asyncio.to_thread(
        alerts_svc.dispatch,
        triggered,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
    )
    return {"account": account_id or "all", "telegram": "off" if not tg_token else "on", **counts}


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
    exclude_weekdays: list[int] = [],
    max_hold_days: int = 0,
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
    exclude_weekdays: list of weekday ints to skip entries on (0=Mon, 1=Tue, ..., 4=Fri).
      Pass [0] to test the "no Monday entries" filter from backtesting research.
    max_hold_days: force-exit positions after N calendar days (0=disabled). Adds
      time-based exits independent of signal — reduces overnight/weekend exposure.
    profit_factor and insufficient_trades_warning (<150 trades) included in per-symbol output.
    Returns per-symbol metrics, weight-aggregated portfolio totals, delta-vs-buy-hold
    (positive = strategy beat passive). lookback_days clamped 30..730.
    If symbols=[], uses top_n holdings by weight. Cached 1h per (symbol, strategy,
    lookback, tx_cost, walk_forward, train_frac, exclude_weekdays, max_hold_days).
    """
    lookback_days = max(30, min(int(lookback_days), 730))
    top_n = max(1, min(int(top_n), 25))
    tx_cost_bps = max(0.0, min(float(tx_cost_bps), 100.0))
    train_frac = max(0.3, min(float(train_frac), 0.9))
    max_hold_days = max(0, int(max_hold_days))
    exclude_weekdays = [int(d) for d in exclude_weekdays if 0 <= int(d) <= 6]
    if not strategies:
        strategies = ["buy_hold", "rsi_swing", "sma_cross", "crowd_fade"]

    portfolio = await _load_portfolio(account_id)
    if not symbols:
        top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:top_n]
        symbols = [p.symbol for p in top]
    _validate_symbols(symbols)

    weights = {p.symbol: p.weight for p in portfolio.positions if p.symbol in symbols}
    return await asyncio.to_thread(
        backtest_svc.backtest_portfolio,
        symbols,
        weights,
        lookback_days,
        strategies,
        tx_cost_bps,
        walk_forward,
        train_frac,
        exclude_weekdays or None,
        max_hold_days,
    )


_SKILLS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills"
_SKILL_INDEX_CACHE: list[dict] | None = None


def _load_skill_index() -> list[dict]:
    """Filesystem-driven enumerate of every Claude Code skill folder.

    Reads `.claude/skills/*/SKILL.md`, extracts the YAML-frontmatter
    `description` (1-line), and harvests every `mcp__aifolimizer__<tool>`
    reference in the body. Cached for process lifetime — restart MCP to pick
    up new skills. Used to be a hard-coded 13-entry list that drifted as
    skills were added; making it filesystem-driven prevents that recurrence.
    """
    import re

    global _SKILL_INDEX_CACHE
    if _SKILL_INDEX_CACHE is not None:
        return _SKILL_INDEX_CACHE
    out: list[dict] = []
    if not _SKILLS_DIR.is_dir():
        _SKILL_INDEX_CACHE = out
        return out
    desc_re = re.compile(
        r"^description:\s*\|?\s*(.+?)(?=\n[a-z_]+:|\n---|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    tool_re = re.compile(r"mcp__aifolimizer__(\w+)")
    for child in sorted(_SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if not (child.is_dir() and skill_md.is_file()):
            continue
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        m = desc_re.search(text)
        # Trim multi-line descriptions to a one-line summary for the table.
        desc = (m.group(1).strip().splitlines()[0] if m else "").strip()[:200]
        tools = sorted(set(tool_re.findall(text)))
        out.append(
            {
                "name": child.name.replace("-", "_"),
                "style": desc,
                "tools_used": tools,
            }
        )
    _SKILL_INDEX_CACHE = out
    return out


@mcp.tool()
def list_analysis_modes() -> list[dict]:
    """Lists every Claude Code skill folder under .claude/skills/ — name,
    one-line description, and the MCP tools each one references. Built from
    the filesystem so it cannot drift as skills are added or removed."""
    return _load_skill_index()


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
        skill,
        ticker,
        action,
        conviction,
        rationale,
        target_pct,
        stop_pct,
        account,
    )


@mcp.tool()
async def score_recommendations(max_age_days: int = 90) -> dict:
    """Mark-to-market all open recommendations from the last N days.

    Fetches current price per ticker via data_router, computes unrealized P&L,
    flags stops/targets hit. Writes scored_recommendations.jsonl.
    Returns win-rate, avg return, and per-conviction breakdown.
    """
    return await asyncio.to_thread(paper_trade_svc.score_recommendations, max_age_days)


@mcp.tool()
async def get_live_track_record(windows_days: list[int] | None = None) -> dict:
    """Rolling forward-test stats from scored recommendations.

    Returns win-rate, avg return, by-conviction breakdown for 7/30/90d.
    Covers only live recommendations logged via log_recommendation —
    NOT the historical backtest (use get_skill_track_record for that).
    """
    return await asyncio.to_thread(paper_trade_svc.get_track_record, windows_days)


@mcp.tool()
async def get_ticker_reflection(symbol: str, n: int = 3) -> dict:
    """Prior recommendation history for a ticker — feeds reflection loop.

    Returns last N logged recommendations for symbol with return_pct, alpha vs
    XEQT, status (open/target_hit/stopped_out), and truncated rationale.
    Use before adversarial-research or pre-trade-check to surface track record
    on this name and avoid repeating losing patterns.
    """
    history = await asyncio.to_thread(paper_trade_svc.get_ticker_history, symbol, n)
    if not history:
        return {"symbol": symbol.upper(), "history": [], "summary": "No prior calls logged for this ticker."}
    wins = sum(1 for r in history if r.get("return_pct") is not None and r["return_pct"] > 0)
    scored = [r for r in history if r.get("return_pct") is not None]
    avg_ret = round(sum(r["return_pct"] for r in scored) / len(scored), 2) if scored else None
    return {
        "symbol": symbol.upper(),
        "history": history,
        "summary": (
            f"{len(history)} prior call(s): {wins}/{len(scored)} wins, avg return {avg_ret}% vs XEQT"
            if scored
            else f"{len(history)} open call(s), no closed P&L yet"
        ),
    }


@mcp.tool()
async def score_signal_horizons(horizons: list[int] | None = None) -> dict:
    """Fill realized H-day forward returns on every logged signal.

    For every directional signal (BUY/ADD/SELL/TRIM) whose H-day window has
    elapsed, fetches historical bars and computes realized return at each
    horizon. SELL/TRIM returns are sign-flipped so positive == correct call.

    Default horizons: full set (1, 3, 5, 10, 21, 42, 63) — must cover the
    decay-curve queries; restricting to (5, 21) silently emptied 5 of 7
    decay buckets.
    Writes back to .claude/context/signal_history.jsonl in place.
    Returns counts: scored_new, skipped_window, skipped_data, total_rows.
    """
    h = tuple(horizons) if horizons else signal_history_svc._DEFAULT_HORIZONS
    return await asyncio.to_thread(signal_history_svc.score_horizons, h)


@mcp.tool()
async def get_signal_accuracy(horizon: int = 21, min_count: int = 5) -> dict:
    """Buy/sell accuracy report at one horizon.

    Returns precision/recall/F1, win rate, expectancy %, and breakdowns by
    action class (BUY/SELL/ADD/TRIM), score bucket, and confidence level.
    Run score_signal_horizons first to populate realized returns.
    """
    return await asyncio.to_thread(signal_history_svc.accuracy_report, horizon, min_count=min_count)


@mcp.tool()
async def calibrate_signal_thresholds(horizon: int = 21, min_count: int = 10) -> dict:
    """Grid-search BUY/SELL score thresholds that maximize expectancy on history.

    Returns current thresholds (buy=7.5, sell_below=3.5) and best-found
    thresholds with n_traded + expectancy. Apply manually after sanity check.
    Small-sample caveat: only trust once n_scored >= a few hundred.
    """
    return await asyncio.to_thread(signal_history_svc.calibrate_thresholds, horizon, min_count=min_count)


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
    return await asyncio.to_thread(alpha_svc.get_alpha_attribution, None, lookback_days)


@mcp.tool()
async def get_quote_with_source(symbol: str, max_age_s: int = 300) -> dict:
    """Live quote with explicit data-source attribution.

    Source order is asset-class specific — see `data_router._chain_for` in
    `backend/app/services/data_router.py` (US/CA equities differ from crypto,
    FX, and indices). Returns price, prev_close, day_change_pct, source, and
    as_of timestamp. Use to verify provenance or when freshness matters more
    than cache speed.
    """
    return await asyncio.to_thread(data_router.get_quote, symbol, float(max_age_s))


@mcp.tool()
async def get_skill_track_record(
    universe: list[str] | None = None,
    lookback_days: int = 1825,
    tx_cost_bps: float = 5.0,
    fresh: bool = False,
    label: str | None = None,
) -> dict:
    """Backtest the codified-rule skill set over historical bars.

    Covers the 13 skills with deterministic Python rules in
    `app.services.skill_backtest.SKILL_RULES` (the LLM-driven skills like
    pre-trade-check, weekly-mirror, momentum-scanner, etc. are not
    rule-replayable and are not part of this backtest). Returns per-skill:
    total_return_pct, cagr_pct, sharpe, sortino, max_drawdown_pct,
    hit_rate_pct, num_trades, alpha_vs_spy_pct, alpha_vs_xeqt_pct. Honest
    caveat: even within the 13, LLM thesis nuance is not replayed.

    If universe is None: uses current portfolio top-10 holdings (label
    "holdings") when a session exists, else the unbiased 40-name basket
    (label "broad"). The trust report shows both labeled runs side by side.
    fresh=True forces re-run; otherwise the latest cached run for that label.
    """
    if not universe:
        session_id = _state.get("session_id")
        if session_id:
            try:
                ws = WSAPISession.from_token(session_id)
                portfolio = await _ws_call(wealthsimple.get_portfolio_response, ws)
                top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:10]
                universe = [p.symbol for p in top]
                label = label or "holdings"
            except Exception:
                universe = skill_bt_svc.DEFAULT_UNIVERSE
                label = label or "broad"
        else:
            universe = skill_bt_svc.DEFAULT_UNIVERSE
            label = label or "broad"
    label = label or "holdings"

    if not fresh:
        cached = await asyncio.to_thread(skill_bt_svc.latest_results, label)
        if cached:
            return cached

    return await asyncio.to_thread(
        skill_bt_svc.backtest_all_skills,
        universe,
        int(lookback_days),
        float(tx_cost_bps),
        True,
        label,
    )


@mcp.tool()
async def get_data_source_reliability(window_days: int = 7) -> dict:
    """Per-source success rate and latency over the trailing window.

    Used by the trust-signal report (TRACK_RECORD.md) so users can audit
    which providers actually served their data and how reliably.
    """
    stats = await asyncio.to_thread(data_router.get_source_reliability, float(window_days) * 86400)
    return {
        "window_days": window_days,
        "configured": data_router.configured_sources(),
        "stats": stats,
    }


@mcp.tool()
async def get_quotes_batch(symbols: list[str], max_age_s: int = 300) -> dict:
    """Fetch live quotes for multiple symbols in one batched HTTP call.

    ~13x faster than calling get_quote per symbol. Uses yfinance.download
    batching. Returns {symbol: {price, prev_close, day_change_pct, source}}.
    Missing symbols silently absent (fetch failed all sources).
    """
    return await asyncio.to_thread(data_router.get_quotes_batch, symbols, float(max_age_s))


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

    For BUY/ADD also returns:
      entry_zone   — buy band {timing: buy_now|wait_pullback, low, high,
                     reference, support_level, support_basis, note}
      exit_ladder  — tiered profit-taking [{label T1/T2/T3, price, sell_pct,
                     shares, gain_pct, rationale, gain_from_cost_pct?}]
    When the ticker is already held, a `position` block adds avg_cost,
    return_pct, and stop_below_cost.

    action: BUY | ADD | HOLD | TRIM | SELL | EXIT
      HOLD returns a management plan for a held name (stop + exit_ladder
      from current price + position block) — no entry zone, no sizing.
    conviction: HIGH | MED | LOW
      HIGH → 7% size, 8% stop, exit ladder 2R/4R/6R
      MED  → 5% size, 6% stop, exit ladder 1.5R/3R/4.5R
      LOW  → 3% size, 4% stop, exit ladder 1R/2R/3R

    Stop is placed at SMA20 - 1% when price > SMA20 (natural support),
    otherwise uses conviction-based % distance. The entry zone is anchored
    to the nearest support (SMA20/Bollinger); when price is stretched
    (>2x ATR above support or RSI ≥ 70) timing flips to wait_pullback.

    Call get_profile first — portfolio_value, available_cash, and the
    held-position cost basis are loaded automatically from the live session.
    """
    portfolio = await _load_portfolio(account_id)
    portfolio_value = portfolio.summary.total_value
    available_cash = portfolio.summary.cash_available

    position = next(
        (p for p in portfolio.positions if p.symbol == ticker.upper()),
        None,
    )
    position_value = float(position.market_value_cad) if position else 0.0
    avg_cost = float(position.book_cost) / float(position.quantity) if position and position.quantity else 0.0
    holding_return_pct = float(position.total_return_pct) if position else 0.0
    position_quantity = float(position.quantity) if position else 0.0

    return await asyncio.to_thread(
        trade_ticket_svc.generate_trade_ticket,
        ticker.upper(),
        action.upper(),
        float(portfolio_value),
        float(position_value),
        float(available_cash),
        conviction.upper(),
        account_id,
        avg_cost=avg_cost,
        holding_return_pct=holding_return_pct,
        position_quantity=position_quantity,
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
    return await asyncio.to_thread(memory_svc.remember, memory_type, content, tags)


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
    return await asyncio.to_thread(memory_svc.list_memories, memory_type or None)


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
        ticker,
        action,
        conviction,
        entry_price,
        target_price,
        stop_price,
        thesis_summary,
        skill_used,
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

    return await asyncio.to_thread(decision_mem_svc.resolve_outcomes, price_map, days_expiry)


@mcp.tool()
async def get_ticker_decision_history(ticker: str, max_decisions: int = 5) -> list[dict]:
    """Phase C — retrieve past trade decisions for a ticker (newest first).

    Call at the START of adversarial-research or cash-deployment for the same
    ticker to inject prior decisions, outcomes, and reflections as context.
    Prevents repeating failed theses; reinforces strategies that worked.

    Returns up to max_decisions records with: date, action, conviction,
    entry/target/stop prices, outcome, outcome_price, and reflection note.
    """
    return await asyncio.to_thread(decision_mem_svc.get_ticker_history, ticker, max_decisions)


@mcp.tool()
async def get_cross_ticker_lessons(max_lessons: int = 3) -> list[dict]:
    """Phase C — top resolved wins and losses across all tickers for cross-portfolio lessons.

    Returns newest-first wins (target_hit) and losses (stop_hit), capped at
    max_lessons each. Each record includes ticker, action, outcome, P&L %, and
    a generated reflection. Inject into skill prompts to surface portfolio-level
    patterns (e.g. 'last 3 TSX banks stopped out at SMA50 — avoid that entry').
    """
    return await asyncio.to_thread(decision_mem_svc.get_cross_ticker_lessons, max_lessons)


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
    - behavioral_biases: disposition effect, gain/loss asymmetry, overtrading,
      anchoring — each with evidence + a flagged bool (biases_flagged lists hits)
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
        horizon,
        min_count=min_count,
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
        return {"error": f"no snapshot for skill={skill}", "available": skill_runner_svc.codified_skills()}
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
    """Stable per-tenant hash for DB lookups.

    Single-user system: derived from session_id when available, else falls back
    to a stable "legacy" hash so DB-backed read tools work even before WS
    auth refreshes. Same fallback used by migrate_jsonl_to_postgres + paper_trade
    dual-write so all writes/reads share one tenant.
    """
    import hashlib

    sid = _state.get("session_id")
    if sid:
        return hashlib.sha1(sid.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return hashlib.sha1(b"legacy", usedforsecurity=False).hexdigest()[:16]


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
                    "score": float(r["score"]) if r.get("score") is not None else None,
                    "tech": float(r["tech_score"]) if r.get("tech_score") is not None else None,
                    "fund": float(r["fund_score"]) if r.get("fund_score") is not None else None,
                    "macro": float(r["macro_score"]) if r.get("macro_score") is not None else None,
                    "sentiment": float(r["sentiment_score"]) if r.get("sentiment_score") is not None else None,
                    "skill_consensus": r.get("skill_consensus"),
                    "skill_confidence": float(r["skill_confidence"]) if r.get("skill_confidence") is not None else None,
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
            thash,
            symbol.upper(),
            days=days,
        )
        return {
            "symbol": symbol.upper(),
            "days": days,
            "points": [
                {
                    "ts": r["ts"].isoformat() if r.get("ts") else None,
                    "score": float(r["score"]) if r.get("score") is not None else None,
                    "action": r.get("action"),
                    "tech": float(r["tech_score"]) if r.get("tech_score") is not None else None,
                    "fund": float(r["fund_score"]) if r.get("fund_score") is not None else None,
                    "macro": float(r["macro_score"]) if r.get("macro_score") is not None else None,
                    "sentiment": float(r["sentiment_score"]) if r.get("sentiment_score") is not None else None,
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
        return {"report": r} if r else {"report": None, "reason": "no report yet"}
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


# ── Meta-tools: batch related calls into one tool invocation ──────────────
# Additive — individual tools remain. Use meta-tools when you need a full
# picture; use individual tools for surgical single-signal queries.


@mcp.tool()
async def get_market_data(symbol: str) -> dict:
    """Combined fundamentals + technicals + news for one ticker in one call.

    Equivalent to calling get_fundamentals + get_technicals + get_news_headlines
    separately. Use for stock-analysis, adversarial-research, pre-trade-check
    when you need all three signal layers at once.
    """
    sym = symbol.upper()
    fund, tech, news = await asyncio.gather(
        asyncio.to_thread(fundamentals_svc.get_fundamentals, [sym]),
        asyncio.to_thread(technicals_svc.get_technicals, [sym]),
        asyncio.to_thread(news_svc.get_news, [sym]),
    )
    return {
        "symbol": sym,
        "fundamentals": fund.get(sym, {}),
        "technicals": tech.get(sym, {}),
        "news": news.get(sym, []),
    }


@mcp.tool()
async def get_portfolio_analysis(account_id: str = "") -> dict:
    """Combined portfolio + concentration warnings + x-ray in one call.

    Equivalent to get_portfolio + get_concentration_warnings + get_xray.
    Use for portfolio-health, sector-rotation, cash-deployment skills.
    """
    portfolio = await _load_portfolio(account_id)
    return {
        "portfolio": filter_portfolio(portfolio.model_dump()),
        "concentration_warnings": portfolio_analytics.concentration_warnings(portfolio),
        "xray": {
            "xray_exposures": portfolio_analytics.xray_exposures(portfolio),
            "sector_breakdown": portfolio_analytics.sector_concentration(portfolio),
            "asset_class_breakdown": portfolio_analytics.asset_class_breakdown(portfolio),
        },
    }


@mcp.tool()
async def get_risk_suite(account_id: str = "", period: str = "1y", top_n: int = 15) -> dict:
    """Combined risk metrics + correlation matrix in one call.

    Equivalent to get_risk_metrics + get_correlation_matrix.
    Use for risk-assessment skill or when gauging portfolio-level exposure.
    """
    portfolio = await _load_portfolio(account_id)
    if not portfolio.positions:
        return {"error": "No positions in portfolio"}
    top_positions = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:top_n]
    symbols = [p.symbol for p in top_positions]
    weights = {p.symbol: p.weight for p in top_positions}
    returns = await asyncio.to_thread(market_data.fetch_returns, symbols, period)
    corr_symbols = symbols[:10]
    corr_returns = {s: returns[s] for s in corr_symbols if s in returns}
    return {
        "risk_metrics": {
            "period": period,
            "symbols_analyzed": symbols,
            "metrics": quant.portfolio_risk_metrics(returns, weights),
        },
        "correlation_matrix": quant.correlation_matrix(corr_returns),
    }


@mcp.tool()
async def get_alert_suite(account_id: str = "", since_hours: int = 24) -> dict:
    """Combined triggered alerts + upcoming earnings + positioning signals in one call.

    Equivalent to get_triggered_alerts + get_earnings_calendar + get_positioning_signals.
    Use for daily-briefing skill or to get a full situational-awareness snapshot.
    """
    portfolio = await _load_portfolio(account_id)
    symbols = [p.symbol for p in portfolio.positions]

    alerts_data = await asyncio.to_thread(alerts_svc.read_recent_history, since_hours=since_hours)
    fund_data, pos_data = await asyncio.gather(
        asyncio.to_thread(fundamentals_svc.get_fundamentals, symbols),
        asyncio.to_thread(positioning_svc.get_positioning, symbols),
    )

    from datetime import date as _date, timedelta

    today = _date.today()
    cutoff = today + timedelta(days=14)
    earnings = []
    for sym in symbols:
        f = fund_data.get(sym, {})
        ed = f.get("earnings_date")
        if ed:
            try:
                from datetime import date as _date2

                d = _date2.fromisoformat(str(ed)[:10])
                earnings.append(
                    {
                        "symbol": sym,
                        "earnings_date": str(d),
                        "is_upcoming": today <= d <= cutoff,
                    }
                )
            except (ValueError, TypeError):
                logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    earnings.sort(key=lambda x: x["earnings_date"])

    return {
        "triggered_alerts": alerts_data,
        "earnings_calendar": earnings,
        "positioning": pos_data,
    }


@mcp.tool()
async def get_track_record_suite() -> dict:
    """Combined live track record + scored recommendations + alpha attribution in one call.

    Equivalent to get_live_track_record + score_recommendations + get_alpha_attribution.
    Use for weekly-mirror skill or full performance accountability review.
    """
    track, scored, alpha = await asyncio.gather(
        asyncio.to_thread(paper_trade_svc.get_track_record, None),
        asyncio.to_thread(paper_trade_svc.score_recommendations),
        asyncio.to_thread(alpha_svc.get_alpha_attribution),
    )
    return {
        "live_track_record": track,
        "scored_recommendations": scored,
        "alpha_attribution": alpha,
    }


_INSTANCE_LOCK = None  # held for process lifetime; module global prevents GC-release


def _warn_if_second_instance() -> None:
    """Tripwire for the duplicate-registration footgun.

    aifolimizer over stdio is one process per MCP client. Two clients (a stray
    .claude.json entry alongside project .mcp.json) spawn two servers that fight
    over one rotating WS token — surfacing as stalls + spurious MFA prompts that
    look like a backend bug. The first instance takes this advisory lock
    silently; any later instance fails the non-blocking acquire and logs loudly
    so the real cause (a second registration) is visible immediately. Non-fatal:
    we still serve, since the filelock around token rotation keeps both safe.
    """
    global _INSTANCE_LOCK
    from filelock import FileLock, Timeout as FileLockTimeout

    lock_path = Path.home() / ".aifolimizer" / "mcp_server.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=0)
        _INSTANCE_LOCK = lock
    except FileLockTimeout:
        print(
            "[MCP] WARNING: another aifolimizer MCP server is already running. "
            "Duplicate registration (check ~/.claude.json vs project .mcp.json) "
            "causes WS token contention -> stalls + spurious MFA. Keep one source.",
            flush=True,
        )


if __name__ == "__main__":
    _warn_if_second_instance()
    mcp.run()
