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


async def _call_llm_json(prompt: str, system: str) -> dict | None:
    """Best-effort: call first available provider, parse JSON, return dict."""
    for provider in llm_router._available_providers():
        try:
            text = await llm_router._call_provider(
                provider, prompt, system=system,
                max_tokens=400, temperature=0.3,
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
    data = await _call_llm_json(_comp_prompt(a, b), _COMP_SYSTEM)
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
