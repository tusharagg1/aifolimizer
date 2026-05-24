"""LLM-skill runner (Phase 7).

Three skills require LLM synthesis and live in .claude/skills/ as Claude Code
prompts. This module re-implements them as backend-callable nightly jobs that
go through llm_router (free providers only). Outputs feed skill_evidence as
soft contributors — None on LLM failure does NOT block the integrated signal.

Rules:
  - Cap LLM calls at top_n holdings per night (default 10).
  - Bail early if llm_router reports no providers available.
  - Cache result per (skill, symbol, date) in Postgres skill_snapshots.
  - Output schema matches codified skills (summary / actionable / alerts /
    key_insights) so skill_evidence._MAPPERS picks them up identically.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import llm_router

log = logging.getLogger(__name__)

_DEFAULT_TOP_N = 10
_DEFAULT_TTL_MIN = 24 * 60  # 24h cache


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _snapshot(
    skill: str,
    *,
    summary: dict | None = None,
    actionable: list | None = None,
    alerts: list | None = None,
    key_insights: list | None = None,
    status: str = "ok",
    ttl_min: int = _DEFAULT_TTL_MIN,
    error: str | None = None,
) -> dict[str, Any]:
    now = _now()
    return {
        "skill": skill,
        "status": status,
        "computed_at": now,
        "expires_at": now + timedelta(minutes=ttl_min),
        "ttl_minutes": ttl_min,
        "summary": summary or {},
        "actionable": actionable or [],
        "alerts": alerts or [],
        "key_insights": key_insights or [],
        "error": error,
    }


def _parse_json_safe(text: str | None) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _providers_available() -> bool:
    try:
        return len(llm_router.active_provider_names()) > 0
    except Exception:
        return False


async def _call_llm_json(
    prompt: str, system: str, *, task: str | None = None,
) -> dict | None:
    """Best-effort: call first available provider, parse JSON, return dict.

    `task` routes GitHub Models to a task-appropriate model (reasoning model
    for adversarial/sell-verify, cheap mini for narrative/briefing). Other
    providers ignore.
    """
    for provider in llm_router._available_providers():
        try:
            text = await llm_router._call_provider(
                provider, prompt, system=system,
                max_tokens=400, temperature=0.3, task=task,
            )
            data = _parse_json_safe(text)
            if data is not None:
                llm_router._record_success(provider["name"])
                return data
        except Exception as e:
            log.warning(
                "llm provider %s failed: %s", provider["name"], e,
            )
            llm_router._record_error(provider["name"])
    return None


# ── Prompts ────────────────────────────────────────────────────────────────

_ADV_SYSTEM = (
    "You are a balanced equity research analyst. For the given ticker, "
    "produce JSON {\"verdict\":\"buy|hold|sell\",\"bull_thesis\":\"...\","
    "\"bear_thesis\":\"...\",\"key_risk\":\"...\"}. Concise. No prose "
    "outside the JSON object."
)

_EARN_SYSTEM = (
    "You are an earnings post-mortem analyst. Given a ticker that just "
    "reported, produce JSON {\"verdict\":\"beat|miss|in_line\","
    "\"thesis_change\":\"confirmed|weakened|broken\","
    "\"action\":\"hold|add|trim|exit\",\"reason\":\"...\"}. No prose "
    "outside the JSON object."
)

_COMP_SYSTEM = (
    "You are a head-to-head equity analyst. Given two tickers, produce "
    "JSON {\"winner\":\"<ticker>\",\"reason\":\"...\","
    "\"loser_action\":\"hold|trim|exit\"}. No prose outside the JSON object."
)

_RISK_SYSTEM = (
    "You are a Bridgewater-style portfolio risk analyst. Given a portfolio "
    "snapshot, produce JSON {\"risk_level\":\"low|moderate|elevated|high\","
    "\"top_risk\":\"concentration|drawdown|correlation|macro|liquidity\","
    "\"action\":\"hold|reduce|hedge|rebalance\",\"reason\":\"...\"}. "
    "No prose outside the JSON object."
)

_HEALTH_SYSTEM = (
    "You are a BlackRock portfolio health analyst. Given allocation + "
    "concentration data, produce JSON {\"health\":\"healthy|attention|"
    "unhealthy\",\"top_issue\":\"concentration|sector_skew|cash_drag|"
    "duplication|none\",\"action\":\"hold|rebalance|trim_top|add_diversifier\","
    "\"reason\":\"...\"}. No prose outside the JSON object."
)

_MACRO_SYSTEM = (
    "You are a McKinsey macro strategist. Given macro indicators "
    "(rates, inflation, FX, regime), produce JSON {\"regime\":\"risk_on|"
    "risk_off|stagflation|recovery|late_cycle\",\"posture\":\"offense|"
    "neutral|defense\",\"action\":\"hold|rotate_defensive|rotate_growth|"
    "raise_cash\",\"reason\":\"...\"}. No prose outside the JSON object."
)

_BRIEFING_SYSTEM = (
    "You are a CIO writing a 1-paragraph morning briefing. Given portfolio + "
    "regime + alerts, produce JSON {\"headline\":\"...\",\"top_concern\":"
    "\"...\",\"top_opportunity\":\"...\",\"action_today\":\"...\"}. "
    "Be specific, cite numbers. No prose outside the JSON object."
)


def _adv_prompt(ticker: str, context: dict) -> str:
    return (
        f"Ticker: {ticker}\n"
        f"Sector: {context.get('sector', 'unknown')}\n"
        f"Current weight in portfolio: {context.get('weight', 0):.1f}%\n"
        f"Recent score (0-10): {context.get('score', 'n/a')}\n"
        "Produce the JSON verdict object now."
    )


def _earn_prompt(ticker: str, context: dict) -> str:
    return (
        f"Ticker: {ticker}\n"
        f"Last earnings date: {context.get('earnings_date', 'unknown')}\n"
        f"Surprise %: {context.get('surprise_pct', 'unknown')}\n"
        "Produce the JSON earnings post-mortem object now."
    )


def _comp_prompt(a: str, b: str) -> str:
    return (
        f"Ticker A: {a}\nTicker B: {b}\n"
        "Pick the winner for a 6-12 month hold. Produce the JSON object now."
    )


def _risk_prompt(ctx: dict) -> str:
    return (
        f"Portfolio max drawdown: {ctx.get('max_drawdown_pct', 'n/a')}%\n"
        f"VIX: {ctx.get('vix', 'n/a')}\n"
        f"Top position weight: {ctx.get('top_weight_pct', 'n/a')}%\n"
        f"Sector concentration (top sector %): "
        f"{ctx.get('top_sector_pct', 'n/a')}%\n"
        f"Loss streak (7d consecutive losers): "
        f"{ctx.get('loss_streak', 0)}\n"
        f"Calibration ECE: {ctx.get('calibration_ece', 'n/a')}\n"
        "Produce the JSON risk assessment now."
    )


def _health_prompt(ctx: dict) -> str:
    return (
        f"Total NAV: ${ctx.get('total_nav', 'n/a')}\n"
        f"Cash %: {ctx.get('cash_pct', 'n/a')}%\n"
        f"Equity %: {ctx.get('equity_pct', 'n/a')}%\n"
        f"Crypto %: {ctx.get('crypto_pct', 'n/a')}%\n"
        f"Top position: {ctx.get('top_symbol', 'n/a')} "
        f"({ctx.get('top_weight_pct', 'n/a')}%)\n"
        f"N positions: {ctx.get('n_positions', 'n/a')}\n"
        f"Top sector: {ctx.get('top_sector', 'n/a')} "
        f"({ctx.get('top_sector_pct', 'n/a')}%)\n"
        "Produce the JSON health assessment now."
    )


def _macro_prompt(ctx: dict) -> str:
    return (
        f"Fed funds rate: {ctx.get('fed_funds', 'n/a')}%\n"
        f"10y yield: {ctx.get('ten_y_yield', 'n/a')}%\n"
        f"CPI YoY: {ctx.get('cpi_yoy', 'n/a')}%\n"
        f"CAD/USD: {ctx.get('cad_usd', 'n/a')}\n"
        f"VIX: {ctx.get('vix', 'n/a')}\n"
        f"SPY vs SMA200: {ctx.get('spy_vs_sma200_pct', 'n/a')}%\n"
        f"Regime composite: {ctx.get('regime_composite', 'n/a')}\n"
        "Produce the JSON macro posture now."
    )


def _briefing_prompt(ctx: dict) -> str:
    return (
        f"Date: {ctx.get('date', 'today')}\n"
        f"Total NAV: ${ctx.get('total_nav', 'n/a')}\n"
        f"Day change: {ctx.get('day_change_pct', 'n/a')}%\n"
        f"Regime: {ctx.get('regime_composite', 'n/a')}\n"
        f"Risk gate: {ctx.get('risk_gate_status', 'trade')}\n"
        f"Open alerts: {ctx.get('n_alerts', 0)}\n"
        f"Top mover: {ctx.get('top_mover', 'n/a')}\n"
        f"Earnings today: {ctx.get('earnings_today', 0)}\n"
        "Produce the JSON briefing now."
    )


# ── Public skill runners ───────────────────────────────────────────────────

async def run_adversarial_research(
    ticker: str, context: dict | None = None,
) -> dict[str, Any]:
    """Bull/bear thesis + verdict for one ticker."""
    if not _providers_available():
        return _snapshot(
            "adversarial-research",
            status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _adv_prompt(ticker, context or {}), _ADV_SYSTEM,
        task="adversarial",
    )
    if data is None:
        return _snapshot(
            "adversarial-research",
            status="error", error="llm_no_response",
        )
    verdict = (data.get("verdict") or "hold").lower()
    action = {
        "buy": "BUY", "sell": "SELL",
    }.get(verdict, "HOLD")
    actionable = [{"symbol": ticker, "action": action,
                   "reason": data.get("bull_thesis") or data.get("reason", "")}]
    return _snapshot(
        "adversarial-research",
        summary={"verdicts": {ticker: verdict}},
        actionable=actionable,
        alerts=([{"level": "warn", "symbol": ticker,
                 "message": data.get("key_risk") or ""}]
                if data.get("key_risk") else []),
        key_insights=[
            f"{ticker} verdict: {verdict}",
            f"Risk: {data.get('key_risk', '—')}",
        ],
    )


async def run_earnings_postmortem(
    ticker: str, context: dict | None = None,
) -> dict[str, Any]:
    if not _providers_available():
        return _snapshot(
            "earnings-postmortem",
            status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _earn_prompt(ticker, context or {}), _EARN_SYSTEM,
        task="earnings_pm",
    )
    if data is None:
        return _snapshot(
            "earnings-postmortem",
            status="error", error="llm_no_response",
        )
    action_raw = (data.get("action") or "hold").lower()
    return _snapshot(
        "earnings-postmortem",
        summary={"verdicts": {ticker: data.get("verdict", "in_line")}},
        actionable=[{
            "symbol": ticker,
            "recommendation": action_raw,
            "thesis_change": data.get("thesis_change"),
            "reason": data.get("reason", ""),
        }],
        key_insights=[
            f"{ticker}: {data.get('verdict')} → thesis "
            f"{data.get('thesis_change')} → {action_raw}",
        ],
    )


async def run_stock_compare(a: str, b: str) -> dict[str, Any]:
    if not _providers_available():
        return _snapshot(
            "stock-compare",
            status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _comp_prompt(a, b), _COMP_SYSTEM, task="stock_compare",
    )
    if data is None:
        return _snapshot(
            "stock-compare",
            status="error", error="llm_no_response",
        )
    winner = data.get("winner") or a
    loser = b if winner == a else a
    loser_act = (data.get("loser_action") or "hold").lower()
    return _snapshot(
        "stock-compare",
        summary={"winner": winner, "loser": loser},
        actionable=[
            {"symbol": winner, "action": "BUY",
             "reason": data.get("reason", "")},
            {"symbol": loser,
             "recommendation": loser_act,
             "reason": "loses comparison"},
        ],
        key_insights=[f"{winner} > {loser}: {data.get('reason', '—')}"],
    )


async def run_risk_assessment(context: dict | None = None) -> dict[str, Any]:
    if not _providers_available():
        return _snapshot(
            "risk-assessment", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _risk_prompt(context or {}), _RISK_SYSTEM, task="risk_assess",
    )
    if data is None:
        return _snapshot(
            "risk-assessment", status="error", error="llm_no_response",
        )
    level = (data.get("risk_level") or "moderate").lower()
    action = (data.get("action") or "hold").lower()
    return _snapshot(
        "risk-assessment",
        summary={"risk_level": level, "top_risk": data.get("top_risk")},
        actionable=[{
            "recommendation": action, "reason": data.get("reason", ""),
        }],
        alerts=(
            [{"level": "warn" if level in ("elevated", "high") else "info",
              "message": f"Risk level: {level}"}]
            if level != "low" else []
        ),
        key_insights=[
            f"Risk: {level} — top concern: {data.get('top_risk', '—')}",
            f"Action: {action} — {data.get('reason', '—')}",
        ],
    )


async def run_portfolio_health(context: dict | None = None) -> dict[str, Any]:
    if not _providers_available():
        return _snapshot(
            "portfolio-health", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _health_prompt(context or {}), _HEALTH_SYSTEM,
        task="portfolio_health",
    )
    if data is None:
        return _snapshot(
            "portfolio-health", status="error", error="llm_no_response",
        )
    health = (data.get("health") or "attention").lower()
    action = (data.get("action") or "hold").lower()
    return _snapshot(
        "portfolio-health",
        summary={"health": health, "top_issue": data.get("top_issue")},
        actionable=[{
            "recommendation": action, "reason": data.get("reason", ""),
        }],
        alerts=(
            [{"level": "warn", "message": f"Health: {health}"}]
            if health == "unhealthy" else []
        ),
        key_insights=[
            f"Health: {health} — issue: {data.get('top_issue', '—')}",
            f"Action: {action} — {data.get('reason', '—')}",
        ],
    )


async def run_macro_impact(context: dict | None = None) -> dict[str, Any]:
    if not _providers_available():
        return _snapshot(
            "macro-impact", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _macro_prompt(context or {}), _MACRO_SYSTEM, task="macro_impact",
    )
    if data is None:
        return _snapshot(
            "macro-impact", status="error", error="llm_no_response",
        )
    regime = (data.get("regime") or "neutral").lower()
    posture = (data.get("posture") or "neutral").lower()
    action = (data.get("action") or "hold").lower()
    return _snapshot(
        "macro-impact",
        summary={"regime": regime, "posture": posture},
        actionable=[{
            "recommendation": action, "reason": data.get("reason", ""),
        }],
        key_insights=[
            f"Regime: {regime} — posture: {posture}",
            f"Action: {action} — {data.get('reason', '—')}",
        ],
    )


async def run_daily_briefing(context: dict | None = None) -> dict[str, Any]:
    if not _providers_available():
        return _snapshot(
            "daily-briefing", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _briefing_prompt(context or {}), _BRIEFING_SYSTEM,
        task="daily_briefing",
    )
    if data is None:
        return _snapshot(
            "daily-briefing", status="error", error="llm_no_response",
        )
    return _snapshot(
        "daily-briefing",
        summary={
            "headline": data.get("headline", ""),
            "top_concern": data.get("top_concern", ""),
            "top_opportunity": data.get("top_opportunity", ""),
        },
        actionable=[{
            "recommendation": data.get("action_today", "hold"),
            "reason": data.get("headline", ""),
        }],
        key_insights=[
            data.get("headline", ""),
            f"Concern: {data.get('top_concern', '—')}",
            f"Opportunity: {data.get('top_opportunity', '—')}",
            f"Action: {data.get('action_today', '—')}",
        ],
    )


# ── Nightly orchestrator ───────────────────────────────────────────────────

async def run_nightly_llm_skills(
    tenant_hash: str,
    top_holdings: list[dict],
    *,
    max_adv: int = _DEFAULT_TOP_N,
    max_earn: int = _DEFAULT_TOP_N,
    max_compare_pairs: int = 3,
) -> dict[str, Any]:
    """Iterate top-N holdings → run LLM skills → persist snapshots.

    `top_holdings` = list of {"symbol": str, "weight": float, "sector": str,
    "score": float, "earnings_date": str | None, "surprise_pct": float | None}
    pre-sorted descending by weight.
    Cap total LLM calls at ~30/night (max_adv + max_earn + 2*max_compare).
    """
    if not _providers_available():
        return {
            "status": "skip",
            "reason": "no LLM provider available",
        }

    from app.db.repositories import snapshots_repo

    results: dict[str, int] = {"adv": 0, "earn": 0, "compare": 0, "errors": 0}

    # Adversarial-research for top N by weight
    for h in top_holdings[:max_adv]:
        try:
            snap = await run_adversarial_research(h["symbol"], h)
            await snapshots_repo.upsert(tenant_hash, snap["skill"], snap)
            if snap.get("status") == "ok":
                results["adv"] += 1
            else:
                results["errors"] += 1
        except Exception as e:
            log.warning("adv failed for %s: %s", h.get("symbol"), e)
            results["errors"] += 1

    # Earnings-postmortem for holdings with a recent earnings_date
    earn_candidates = [
        h for h in top_holdings
        if h.get("earnings_date")
    ][:max_earn]
    for h in earn_candidates:
        try:
            snap = await run_earnings_postmortem(h["symbol"], h)
            await snapshots_repo.upsert(tenant_hash, snap["skill"], snap)
            if snap.get("status") == "ok":
                results["earn"] += 1
            else:
                results["errors"] += 1
        except Exception as e:
            log.warning("earn failed for %s: %s", h.get("symbol"), e)
            results["errors"] += 1

    # Stock-compare: top weight vs each next 3 (rotating)
    if len(top_holdings) >= 2:
        anchor = top_holdings[0]["symbol"]
        for h in top_holdings[1:max_compare_pairs + 1]:
            try:
                snap = await run_stock_compare(anchor, h["symbol"])
                await snapshots_repo.upsert(
                    tenant_hash, snap["skill"], snap,
                )
                if snap.get("status") == "ok":
                    results["compare"] += 1
                else:
                    results["errors"] += 1
            except Exception as e:
                log.warning(
                    "compare failed for %s vs %s: %s",
                    anchor, h.get("symbol"), e,
                )
                results["errors"] += 1

    # Inter-call sleep keeps free-tier rate-limits happy
    await asyncio.sleep(0.0)

    return {"status": "ok", **results}
