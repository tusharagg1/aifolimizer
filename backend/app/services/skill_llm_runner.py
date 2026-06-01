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
import time
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


_THINK_TAG_RE = __import__("re").compile(
    r"<think>.*?</think>", __import__("re").DOTALL | __import__("re").IGNORECASE,
)


def _parse_json_safe(text: str | None) -> dict | None:
    if not text:
        return None
    # DeepSeek-R1 + similar reasoning models wrap chain-of-thought in
    # <think>...</think>. Strip before JSON detection.
    text = _THINK_TAG_RE.sub("", text).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # Reasoning models sometimes return prose then JSON. Find first { ... }.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


_PROVIDERS_CACHE: dict[str, Any] = {"ts": 0.0, "available": False}
_PROVIDERS_TTL_S = 30.0


def _providers_available() -> bool:
    now = time.time()
    if now - _PROVIDERS_CACHE["ts"] < _PROVIDERS_TTL_S:
        return _PROVIDERS_CACHE["available"]
    try:
        available = len(llm_router.active_provider_names()) > 0
    except Exception:
        available = False
    _PROVIDERS_CACHE["ts"] = now
    _PROVIDERS_CACHE["available"] = available
    return available


async def _call_llm_json(
    prompt: str, system: str, *, task: str | None = None,
) -> dict | None:
    """Best-effort: call first available provider, parse JSON, return dict.

    `task` routes GitHub Models to a task-appropriate model (reasoning model
    for adversarial/sell-verify, cheap mini for narrative/briefing). Other
    providers ignore.
    """
    # Reasoning models (deepseek-r1, o-series when scoped) need extra budget
    # for chain-of-thought tokens before the final answer.
    reasoning_tasks = {"adversarial", "sell_verify"}
    max_tok = 1500 if task in reasoning_tasks else 400
    for provider in llm_router._available_providers():
        try:
            text = await llm_router._call_provider(
                provider, prompt, system=system,
                max_tokens=max_tok, temperature=0.3, task=task,
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
    "produce JSON with these exact keys: "
    "{\"verdict\":\"buy|hold|sell\","
    "\"conviction\":\"low|medium|high\","
    "\"bull_thesis\":\"<one sentence>\","
    "\"bear_thesis\":\"<one sentence>\","
    "\"key_risk\":\"<one sentence>\","
    "\"price_target\":<float or null>,"
    "\"stop\":<float or null>,"
    "\"bull_invalidation\":\"<exact condition that kills bull thesis>\"}. "
    "price_target and stop must be numeric when price is provided. "
    "No prose outside the JSON object."
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

_PRE_TRADE_SYSTEM = (
    "You are a brutally disciplined trading-desk risk officer. Given a trade "
    "intent, run the pre-trade-check gates: FOMO-rip, crowding, sizing, stop "
    "discipline, concentration, stage, re-entry. Produce JSON "
    "{\"verdict\":\"PASS|REJECT\",\"reason\":\"<one line>\",\"failed_gates\":"
    "[...],\"warnings\":[...],\"suggested_entry\":<float>,\"suggested_stop\":"
    "<float>,\"position_pct_of_nav\":<float>,"
    "\"max_loss_pct_of_nav\":<float>}. "
    "Size is given in % of NAV only — never request or quote dollar amounts. "
    "Never recommend size > 5% of NAV. "
    "Never recommend re-entry on a stop-hit ticker without explicit new "
    "bullish catalyst. No prose outside the JSON object."
)

_WEEKLY_MIRROR_SYSTEM = (
    "You are a cold honest performance auditor. Given a week of trading + "
    "portfolio state, produce JSON {\"verdict\":\"CONTINUE|COOL_OFF|SUSPEND\","
    "\"reason\":\"<one line>\",\"win_rate_30d\":<float>,\"r_multiple_30d\":"
    "<float>,\"trading_vs_core_diff_pct\":<float>,\"top_pattern\":\"...\","
    "\"next_actions\":[\"<bullet>\",...]}. Verdict thresholds: CONTINUE if "
    "win_rate_30d>=50 AND r_multiple>=1.5; SUSPEND if win_rate<40 OR "
    "r_multiple<1.0; else COOL_OFF. All P&L figures are in % of NAV; never "
    "request or quote dollar amounts. No hedging. No prose outside JSON."
)

_REBALANCE_SYSTEM = (
    "You are a wealth-management rebalancer. Given target allocation, current "
    "weights, and per-account cash %, produce JSON {\"deployment\":[{\"ticker\":"
    "\"...\",\"account_label\":\"...\",\"pct_of_nav\":<float>,\"action\":"
    "\"BUY|TRIM\"},...],\"drift_summary\":\"...\",\"cash_remaining_pct\":<float>,"
    "\"next_review_days\":<int>}. All sizing in % of NAV — never request or "
    "quote dollar amounts. Never recommend selling a core holding "
    "unless drift > 15pp above target. Rebalance via new cash by default. "
    "No prose outside the JSON object."
)


def _adv_prompt(ticker: str, context: dict) -> str:
    lines = [
        f"Ticker: {ticker}",
        f"Sector: {context.get('sector', 'unknown')}",
        f"Portfolio weight: {context.get('weight', 0):.1f}%",
        f"Signal score (0-10): {context.get('score', 'n/a')}",
    ]
    if context.get("current_price"):
        lines.append(f"Current price: ${context['current_price']}")
    if context.get("rsi_14"):
        lines.append(f"RSI 14: {context['rsi_14']}")
    if context.get("stage"):
        lines.append(f"Stage: {context['stage']}")
    if context.get("analyst_upside_pct"):
        lines.append(
            f"Analyst upside %: {context['analyst_upside_pct']}",
        )
    if context.get("crowding_score"):
        lines.append(f"Crowding score: {context['crowding_score']}")
    if context.get("reflection"):
        lines.append(f"\n## Prior Calls on {ticker}\n{context['reflection']}\nLearn from these. Don't repeat losing patterns.")
    lines.append("Produce the JSON verdict object now.")
    return "\n".join(lines)


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
    # SPI guard: prompt sent to external LLMs. NAV / balances must stay on-machine.
    # Send relative allocation (% of NAV) only.
    return (
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
    # SPI guard: NAV in dollars must not leave the machine. Day change % is fine.
    return (
        f"Date: {ctx.get('date', 'today')}\n"
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
    enriched = dict(context or {})
    # Inject prior decisions on this ticker + portfolio-wide lessons so the
    # LLM doesn't repeat thesis patterns that already failed. Reflection
    # text is built once and cached on the context dict so callers passing
    # a pre-built reflection win that path.
    if not enriched.get("reflection"):
        try:
            from app.services import decision_memory
            history = decision_memory.get_ticker_history(ticker, 5)
            cross = decision_memory.get_cross_ticker_lessons(3)
            chunks: list[str] = []
            for h in history:
                outcome = h.get("outcome", "open")
                ref = h.get("reflection") or ""
                date = (h.get("created_utc") or "")[:10]
                action = h.get("action", "")
                chunks.append(
                    f"- {date} {action} → {outcome}: {ref}".strip()
                )
            for c in cross:
                outcome = c.get("outcome", "?")
                ticker_c = c.get("ticker", "?")
                ref = c.get("reflection") or ""
                chunks.append(
                    f"- portfolio-lesson {ticker_c} → {outcome}: {ref}"
                )
            if chunks:
                enriched["reflection"] = "\n".join(chunks)
        except Exception as e:
            log.debug("decision_memory injection skipped: %s", e)
    data = await _call_llm_json(
        _adv_prompt(ticker, enriched), _ADV_SYSTEM,
        task="adversarial",
    )
    if data is None:
        return _snapshot(
            "adversarial-research",
            status="error", error="llm_no_response",
        )
    verdict = (data.get("verdict") or "hold").lower()
    conviction = (data.get("conviction") or "medium").lower()
    action = {"buy": "BUY", "sell": "SELL"}.get(verdict, "HOLD")
    actionable = [{
        "symbol": ticker,
        "action": action,
        "conviction": conviction,
        "reason": data.get("bull_thesis") or data.get("reason", ""),
        "price_target": data.get("price_target"),
        "stop": data.get("stop"),
        "bull_invalidation": data.get("bull_invalidation"),
    }]
    insights = [
        f"{ticker} verdict: {verdict} ({conviction} conviction)",
        f"Risk: {data.get('key_risk', '—')}",
    ]
    if data.get("price_target"):
        insights.append(f"Target: ${data['price_target']}")
    if data.get("stop"):
        insights.append(f"Stop: ${data['stop']}")
    return _snapshot(
        "adversarial-research",
        summary={
            "verdicts": {ticker: verdict},
            "conviction": conviction,
            "price_target": data.get("price_target"),
            "stop": data.get("stop"),
            "bull_invalidation": data.get("bull_invalidation"),
            "bear_thesis": data.get("bear_thesis"),
        },
        actionable=actionable,
        alerts=([{"level": "warn", "symbol": ticker,
                  "message": data.get("key_risk") or ""}]
                if data.get("key_risk") else []),
        key_insights=insights,
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


def _pre_trade_prompt(ctx: dict) -> str:
    # SPI guard: Total NAV is private — never send. Public market data
    # (current_price, ATR, SMA50) is fine. Sizing is requested as % of NAV.
    # ATR is converted to % of price so the model has volatility scale without
    # an absolute dollar reference.
    # numpy scalars (np.float64 from yfinance/pandas) fail isinstance(_, float)
    # silently — try-cast covers Python and numpy numeric types alike.
    try:
        _p = float(ctx.get("current_price"))
        _a = float(ctx.get("atr_14"))
        atr_pct = f"{round(_a / _p * 100, 2)}" if _p > 0 else "n/a"
    except (TypeError, ValueError):
        atr_pct = "n/a"
    return (
        f"Ticker: {ctx.get('ticker', 'n/a')}\n"
        f"Direction: {ctx.get('direction', 'BUY')}\n"
        f"Horizon: {ctx.get('horizon', 'swing')}\n"
        f"Current price (public market): ${ctx.get('current_price', 'n/a')}\n"
        f"Day change %: {ctx.get('day_change_pct', 'n/a')}\n"
        f"3-day change %: {ctx.get('change_3d_pct', 'n/a')}\n"
        f"ATR 14 (% of price): {atr_pct}%\n"
        f"RSI 14: {ctx.get('rsi_14', 'n/a')}\n"
        f"Stage: {ctx.get('stage', 'n/a')}\n"
        f"SMA50 (public market): ${ctx.get('sma_50', 'n/a')}\n"
        f"Crowding score: {ctx.get('crowding_score', 'n/a')}\n"
        f"Current weight in portfolio: {ctx.get('current_weight_pct', 0)}%\n"
        f"User intent reason: {ctx.get('user_reason', 'unstated')}\n"
        f"Last 30d user win rate: {ctx.get('user_win_rate_30d', 'n/a')}\n"
        f"Recent stop-out on this ticker (30d): "
        f"{ctx.get('recent_stop_out', False)}\n"
        + (
            f"\n## Prior Calls on {ctx.get('ticker', 'this ticker')}\n"
            f"{ctx['reflection']}\n"
            f"Factor this track record into your discipline gates.\n"
            if ctx.get("reflection") else ""
        )
        + "Run the pre-trade-check gates. Produce the JSON verdict now."
    )


def _weekly_mirror_prompt(ctx: dict) -> str:
    # SPI guard: NAV, deposits, P&L absolute dollars are private — never send.
    # All P&L figures are relative (% of NAV). avg_win/avg_loss in dollars are
    # dropped; R-multiple already conveys their ratio.
    return (
        f"Date: {ctx.get('date', 'today')}\n"
        f"Week NAV change %: {ctx.get('week_change_pct', 'n/a')}\n"
        f"Return vs deposits %: {ctx.get('return_vs_deposits_pct', 'n/a')}\n"
        f"Boring-core 7d P&L (% of NAV): {ctx.get('core_pnl_pct_7d', 'n/a')}%\n"
        f"Discretionary 7d P&L (% of NAV): {ctx.get('disc_pnl_pct_7d', 'n/a')}%\n"
        f"vs SPY 7d %: {ctx.get('vs_spy_7d_pct', 'n/a')}\n"
        f"30d closed trades: {ctx.get('closed_trades_30d', 0)}\n"
        f"30d win rate %: {ctx.get('win_rate_30d', 'n/a')}\n"
        f"30d R-multiple: {ctx.get('r_multiple_30d', 'n/a')}\n"
        f"90d alpha vs XEQT %: {ctx.get('alpha_vs_xeqt_90d', 'n/a')}\n"
        f"Top recurring loss pattern: {ctx.get('top_pattern', 'n/a')}\n"
        "Produce the JSON weekly verdict now."
    )


def _rebalance_prompt(ctx: dict) -> str:
    # SPI guard: NAV and per-account cash dollar amounts are private. Send
    # per-account cash as % of NAV instead, keyed by account label (TFSA/RRSP/
    # Non-Reg/...) — caller is expected to redact account IDs upstream.
    return (
        f"Strategy: {ctx.get('strategy', 'growth-aggressive')}\n"
        f"Target weights (% of NAV): {ctx.get('target_weights', {})}\n"
        f"Current weights (% of NAV): {ctx.get('current_weights', {})}\n"
        f"Per-account cash (% of NAV, keyed by account label): "
        f"{ctx.get('cash_per_account_pct', {})}\n"
        f"Last rebalance date: {ctx.get('last_rebalance_date', 'never')}\n"
        f"Tax-loss superficial blocks (next 30d): "
        f"{ctx.get('superficial_blocked_tickers', [])}\n"
        "Produce the JSON deployment plan now."
    )


async def run_pre_trade_check(context: dict | None = None) -> dict[str, Any]:
    """Discipline gate before any discretionary entry. Returns PASS/REJECT."""
    if not _providers_available():
        return _snapshot(
            "pre-trade-check", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _pre_trade_prompt(context or {}), _PRE_TRADE_SYSTEM,
        task="pre_trade_check",
    )
    if data is None:
        return _snapshot(
            "pre-trade-check", status="error", error="llm_no_response",
        )
    verdict = (data.get("verdict") or "REJECT").upper()
    return _snapshot(
        "pre-trade-check",
        summary={
            "verdict": verdict,
            "ticker": (context or {}).get("ticker"),
            "reason": data.get("reason"),
            "suggested_entry": data.get("suggested_entry"),
            "suggested_stop": data.get("suggested_stop"),
            "position_pct_of_nav": data.get("position_pct_of_nav"),
            "max_loss_pct_of_nav": data.get("max_loss_pct_of_nav"),
        },
        actionable=[{
            "recommendation": "PASS" if verdict == "PASS" else "BLOCK",
            "ticker": (context or {}).get("ticker"),
            "reason": data.get("reason", ""),
        }],
        alerts=(
            [{"level": "warn", "message": f"REJECT: {data.get('reason')}"}]
            if verdict == "REJECT" else []
        ),
        key_insights=[
            f"{verdict}: {data.get('reason', '—')}",
            f"Failed gates: {data.get('failed_gates') or 'none'}",
            f"Warnings: {data.get('warnings') or 'none'}",
        ],
    )


async def run_weekly_mirror(context: dict | None = None) -> dict[str, Any]:
    """Cold honest weekly performance audit. Verdict: continue/cool-off/suspend."""
    if not _providers_available():
        return _snapshot(
            "weekly-mirror", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _weekly_mirror_prompt(context or {}), _WEEKLY_MIRROR_SYSTEM,
        task="weekly_mirror",
    )
    if data is None:
        return _snapshot(
            "weekly-mirror", status="error", error="llm_no_response",
        )
    verdict = (data.get("verdict") or "COOL_OFF").upper()
    alert_level = {
        "SUSPEND": "warn", "COOL_OFF": "info", "CONTINUE": "info",
    }.get(verdict, "info")
    return _snapshot(
        "weekly-mirror",
        summary={
            "verdict": verdict,
            "win_rate_30d": data.get("win_rate_30d"),
            "r_multiple_30d": data.get("r_multiple_30d"),
            "trading_vs_core_diff_pct": data.get("trading_vs_core_diff_pct"),
            "top_pattern": data.get("top_pattern"),
        },
        actionable=[
            {"recommendation": a, "reason": ""}
            for a in (data.get("next_actions") or [])
        ],
        alerts=[{"level": alert_level, "message": f"Verdict: {verdict}"}],
        key_insights=[
            f"Verdict: {verdict} — {data.get('reason', '—')}",
            f"30d win rate: {data.get('win_rate_30d', '—')}%",
            f"30d R-multiple: {data.get('r_multiple_30d', '—')}",
            f"Top pattern: {data.get('top_pattern', '—')}",
        ],
    )


_STOCK_ANALYSIS_SYSTEM = (
    "You are a Goldman + Citadel equity analyst. Given a ticker w/ tech + "
    "fund + crowding context, produce JSON {\"verdict\":\"BUY|HOLD|SELL|"
    "TRIM|ADD\",\"conviction\":\"low|med|high\",\"target_price\":<float>,"
    "\"stop\":<float>,\"thesis\":\"...\",\"top_risk\":\"...\"}. No prose."
)

_EARN_ANALYZER_SYSTEM = (
    "You are a JPMorgan pre-earnings analyst. Given ticker + earnings date + "
    "fund context, produce JSON {\"hold_through\":\"yes|no|trim\","
    "\"expected_move_pct\":<float>,\"key_metric\":\"...\",\"action\":"
    "\"hold|trim|hedge|exit\",\"reason\":\"...\"}. No prose."
)

_CASH_DEPLOY_SYSTEM = (
    "You are a cash-deployment analyst. Given cash % of NAV + portfolio + "
    "candidate setups, produce JSON {\"deployment\":[{\"ticker\":\"...\","
    "\"pct_of_nav\":<float>,\"entry\":<float>,\"stop\":<float>,"
    "\"setup_score\":<int>},...],\"cash_remaining_pct\":<float>,\"reason\":"
    "\"...\"}. All sizing in % of NAV — never quote dollar amounts. Never "
    "recommend single position > 5% NAV. Never add to concentration-flagged "
    "or crowding>=70 names. No prose."
)

_DIV_SYSTEM = (
    "You are a Harvard-endowment dividend strategist. Given holdings + "
    "yield + payout context, produce JSON {\"income_health\":\"strong|ok|"
    "weak\",\"projected_yield_pct\":<float>,\"top_risked_payer\":\"...\","
    "\"recommendation\":\"hold|rotate|add_dividend|trim_low_yield\","
    "\"reason\":\"...\"}. Yield expressed as % only — never quote dollar "
    "income amounts. No prose."
)

_SECTOR_SYSTEM = (
    "You are a Renaissance sector-rotation analyst. Given sector exposures "
    "+ market regime, produce JSON {\"overweight\":[\"...\"],\"underweight\":"
    "[\"...\"],\"top_rotation\":\"<from>->\\u003cto\\u003e\",\"reason\":"
    "\"...\"}. No prose."
)

_TAX_LOSS_SYSTEM = (
    "You are a Canadian tax-loss-harvesting analyst. Given underwater "
    "positions (loss as % of NAV) + account labels, produce JSON "
    "{\"candidates\":[{\"ticker\":\"...\",\"loss_pct_of_nav\":<float>,"
    "\"account_label\":\"...\",\"replacement\":\"...\","
    "\"superficial_block_until\":\"YYYY-MM-DD\"},...],"
    "\"total_loss_pct_of_nav\":<float>,\"reason\":\"...\"}. Loss figures in "
    "% of NAV only — never quote dollar amounts. Never recommend harvesting "
    "in TFSA or RRSP (no tax benefit). Always flag superficial-loss rule. "
    "No prose."
)


def _stock_analysis_prompt(ctx: dict) -> str:
    return (
        f"Ticker: {ctx.get('ticker', 'n/a')}\n"
        f"Price: ${ctx.get('current_price', 'n/a')}\n"
        f"Stage: {ctx.get('stage', 'n/a')}\n"
        f"RSI 14: {ctx.get('rsi_14', 'n/a')}\n"
        f"SMA50: ${ctx.get('sma_50', 'n/a')}  SMA200: ${ctx.get('sma_200', 'n/a')}\n"
        f"P/E: {ctx.get('pe_ratio', 'n/a')}  Analyst upside %: "
        f"{ctx.get('analyst_upside_pct', 'n/a')}\n"
        f"Crowding score: {ctx.get('crowding_score', 'n/a')}\n"
        f"Sector: {ctx.get('sector', 'n/a')}\n"
        "Produce the JSON verdict now."
    )


def _earn_analyzer_prompt(ctx: dict) -> str:
    return (
        f"Ticker: {ctx.get('ticker', 'n/a')}\n"
        f"Earnings date: {ctx.get('earnings_date', 'unknown')}\n"
        f"Current price: ${ctx.get('current_price', 'n/a')}\n"
        f"EPS estimate: {ctx.get('eps_estimate', 'n/a')}\n"
        f"Avg past surprise %: {ctx.get('avg_surprise_pct', 'n/a')}\n"
        f"IV / historical vol: {ctx.get('iv_hv_ratio', 'n/a')}\n"
        "Produce the JSON pre-earnings call now."
    )


def _cash_deploy_prompt(ctx: dict) -> str:
    # SPI guard: settled cash CAD and Total NAV are private balances. Send
    # cash as % of NAV so the model recommends pct_of_nav allocations; the
    # caller resolves $ + share counts locally after the LLM returns.
    return (
        f"Settled cash (% of NAV): {ctx.get('cash_pct_of_nav', 'n/a')}%\n"
        f"Account label: {ctx.get('account_label', 'n/a')}\n"
        f"Strategy lens: {ctx.get('strategy', 'aggressive growth')}\n"
        f"Eligible candidates: {ctx.get('candidates', [])}\n"
        f"Concentration-flagged tickers: {ctx.get('blocked', [])}\n"
        "Produce the JSON deployment plan now."
    )


def _div_prompt(ctx: dict) -> str:
    # SPI guard: Total NAV and absolute annual income $ are private. Yield
    # already expressed as % — sufficient signal without dollar amounts.
    return (
        f"Dividend payers: {ctx.get('payers', [])}\n"
        f"Portfolio yield %: {ctx.get('portfolio_yield_pct', 'n/a')}\n"
        "Produce the JSON dividend health now."
    )


def _sector_prompt(ctx: dict) -> str:
    return (
        f"Sector exposures %: {ctx.get('sector_weights', {})}\n"
        f"Market regime: {ctx.get('regime_composite', 'n/a')}\n"
        f"Top performing sectors (3mo): {ctx.get('top_3mo', [])}\n"
        f"Bottom performing sectors (3mo): {ctx.get('bottom_3mo', [])}\n"
        "Produce the JSON sector rotation tilt now."
    )


def _tax_loss_prompt(ctx: dict) -> str:
    # SPI guard: absolute CAD loss + YTD realized gain $ are private. Send
    # loss as % of NAV. Account breakdown should arrive pre-redacted (labels
    # only, no IDs) — the caller is responsible upstream.
    return (
        f"Underwater positions: {ctx.get('losers', [])}\n"
        f"Total unrealized loss (% of NAV): "
        f"{ctx.get('total_loss_pct_of_nav', 'n/a')}%\n"
        f"YTD realized gains (% of NAV): "
        f"{ctx.get('realized_gains_pct_of_nav', 'n/a')}%\n"
        f"Account labels in scope: {ctx.get('account_labels', [])}\n"
        "Produce the JSON tax-loss harvest plan now."
    )


def _generic_runner_factory(skill_name: str, prompt_fn, system_prompt, task):
    """Returns an async runner that calls LLM + wraps in _snapshot()."""

    async def _runner(context: dict | None = None) -> dict[str, Any]:
        if not _providers_available():
            return _snapshot(
                skill_name, status="error", error="no_llm_provider",
            )
        data = await _call_llm_json(
            prompt_fn(context or {}), system_prompt, task=task,
        )
        if data is None:
            return _snapshot(
                skill_name, status="error", error="llm_no_response",
            )
        return _snapshot(
            skill_name,
            summary={k: v for k, v in data.items()
                     if not isinstance(v, (list, dict))},
            actionable=(
                data.get("deployment")
                or data.get("candidates")
                or [{
                    "recommendation": data.get("action")
                    or data.get("verdict")
                    or data.get("recommendation", "hold"),
                    "reason": data.get("reason")
                    or data.get("thesis", ""),
                }]
            ),
            key_insights=[
                f"{skill_name}: {data.get('verdict') or data.get('reason') or '—'}",
            ],
        )

    return _runner


run_stock_analysis_for_ticker = _generic_runner_factory(
    "stock-analysis", _stock_analysis_prompt,
    _STOCK_ANALYSIS_SYSTEM, "stock_analysis",
)
run_earnings_analyzer = _generic_runner_factory(
    "earnings-analyzer", _earn_analyzer_prompt,
    _EARN_ANALYZER_SYSTEM, "earnings_analyzer",
)
run_cash_deployment = _generic_runner_factory(
    "cash-deployment", _cash_deploy_prompt,
    _CASH_DEPLOY_SYSTEM, "cash_deployment",
)
run_dividend_strategy = _generic_runner_factory(
    "dividend-strategy", _div_prompt,
    _DIV_SYSTEM, "dividend_strategy",
)
run_sector_rotation = _generic_runner_factory(
    "sector-rotation", _sector_prompt,
    _SECTOR_SYSTEM, "sector_rotation",
)
run_tax_loss_review = _generic_runner_factory(
    "tax-loss-review", _tax_loss_prompt,
    _TAX_LOSS_SYSTEM, "tax_loss_review",
)


async def run_auto_rebalance(context: dict | None = None) -> dict[str, Any]:
    """Monthly core-ETF rebalance + DCA plan. Auto-execute=False by default."""
    if not _providers_available():
        return _snapshot(
            "auto-rebalance", status="error", error="no_llm_provider",
        )
    data = await _call_llm_json(
        _rebalance_prompt(context or {}), _REBALANCE_SYSTEM,
        task="auto_rebalance",
    )
    if data is None:
        return _snapshot(
            "auto-rebalance", status="error", error="llm_no_response",
        )
    deployments = data.get("deployment") or []
    return _snapshot(
        "auto-rebalance",
        summary={
            "drift_summary": data.get("drift_summary"),
            "cash_remaining_pct": data.get("cash_remaining_pct"),
            "deployment_count": len(deployments),
        },
        actionable=[
            {
                "recommendation": d.get("action", "BUY"),
                "ticker": d.get("ticker"),
                "account": d.get("account"),
                "shares": d.get("shares"),
                "cost": d.get("cost"),
            }
            for d in deployments
        ],
        key_insights=[
            f"Drift: {data.get('drift_summary', '—')}",
            f"Deployments planned: {len(deployments)}",
            f"Next review in {data.get('next_review_days', 30)}d",
        ],
    )


# ── Context-only wrappers (for agent_registry uniform calling) ─────────────
# Registry calls runner(context_dict). These wrappers extract symbol/ticker
# from context, then call underlying ticker-positional runners.


async def run_adversarial_research_ctx(
    context: dict | None = None,
) -> dict[str, Any]:
    ctx = context or {}
    ticker = (ctx.get("ticker") or ctx.get("symbol") or "").upper()
    if not ticker:
        return _snapshot(
            "adversarial-research",
            status="error",
            error="missing_ticker_in_context",
        )
    return await run_adversarial_research(ticker, ctx)


async def run_earnings_postmortem_ctx(
    context: dict | None = None,
) -> dict[str, Any]:
    ctx = context or {}
    ticker = (ctx.get("ticker") or ctx.get("symbol") or "").upper()
    if not ticker:
        return _snapshot(
            "earnings-postmortem",
            status="error",
            error="missing_ticker_in_context",
        )
    return await run_earnings_postmortem(ticker, ctx)


async def run_stock_compare_ctx(
    context: dict | None = None,
) -> dict[str, Any]:
    ctx = context or {}
    a = (ctx.get("a") or ctx.get("ticker_a") or "").upper()
    b = (ctx.get("b") or ctx.get("ticker_b") or "").upper()
    if not a or not b:
        return _snapshot(
            "stock-compare",
            status="error",
            error="missing_tickers_in_context",
        )
    return await run_stock_compare(a, b)


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

    # Bounded concurrency — free-tier rate-limits cap us at 4 parallel calls.
    sem = asyncio.Semaphore(4)

    async def _run_adv(h: dict) -> tuple[str, bool]:
        async with sem:
            try:
                snap = await run_adversarial_research(h["symbol"], h)
                await snapshots_repo.upsert(tenant_hash, snap["skill"], snap)
                return "adv", snap.get("status") == "ok"
            except Exception as e:
                log.warning("adv failed for %s: %s", h.get("symbol"), e)
                return "adv", False

    async def _run_earn(h: dict) -> tuple[str, bool]:
        async with sem:
            try:
                snap = await run_earnings_postmortem(h["symbol"], h)
                await snapshots_repo.upsert(tenant_hash, snap["skill"], snap)
                return "earn", snap.get("status") == "ok"
            except Exception as e:
                log.warning("earn failed for %s: %s", h.get("symbol"), e)
                return "earn", False

    async def _run_compare(anchor: str, h: dict) -> tuple[str, bool]:
        async with sem:
            try:
                snap = await run_stock_compare(anchor, h["symbol"])
                await snapshots_repo.upsert(tenant_hash, snap["skill"], snap)
                return "compare", snap.get("status") == "ok"
            except Exception as e:
                log.warning(
                    "compare failed for %s vs %s: %s",
                    anchor, h.get("symbol"), e,
                )
                return "compare", False

    tasks: list = [_run_adv(h) for h in top_holdings[:max_adv]]
    earn_candidates = [
        h for h in top_holdings if h.get("earnings_date")
    ][:max_earn]
    tasks += [_run_earn(h) for h in earn_candidates]
    if len(top_holdings) >= 2:
        anchor = top_holdings[0]["symbol"]
        tasks += [
            _run_compare(anchor, h)
            for h in top_holdings[1:max_compare_pairs + 1]
        ]

    outcomes = await asyncio.gather(*tasks, return_exceptions=False)
    for bucket, ok in outcomes:
        if ok:
            results[bucket] += 1
        else:
            results["errors"] += 1

    return {"status": "ok", **results}
