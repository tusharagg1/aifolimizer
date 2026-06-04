"""Central agent registry — single source of truth for skill→trigger→runner.

Each skill in .claude/skills/ has a backend agent counterpart that:
  - Runs headless on a trigger (cron or event)
  - Persists output to skill_snapshots table
  - Exposes status (last_run, next_run, win_rate, enabled) via /api/agents/*

This module does NOT execute agents directly. Execution lives in the existing
scheduler (jobs/scheduler.py) and event_dispatcher.py. This module is the
*declaration* layer those readers consult to learn what to run when.

Adding a new skill agent:
  1. Author SKILL.md in .claude/skills/<name>/
  2. Add backend runner function (skill_llm_runner.py or skill_runner.py)
  3. Register entry below with trigger + runner reference
  4. Scheduler picks it up on next tick — no scheduler.py edit needed
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

TriggerType = Literal["cron", "event", "manual"]
ModelPref = Literal["fast", "reasoning", "auto"]


@dataclass
class AgentSpec:
    name: str  # matches .claude/skills/<name>/SKILL.md
    description: str  # short for dashboard card
    trigger: TriggerType
    runner_ref: str  # "module.path:function" lazy-resolved
    schedule: str | None = None  # cron expr (UTC) when trigger=cron
    event_types: list[str] = field(default_factory=list)  # when trigger=event
    model_pref: ModelPref = "fast"
    auto_execute: bool = True  # False = propose only, user confirms
    output_sinks: list[str] = field(default_factory=lambda: ["snapshot"])
    horizon_days: int | None = None  # forward-scoring horizon for win-rate
    category: str = "general"  # ui grouping: trading|portfolio|research|behavioral


# ── In-memory operational state (per skill) ───────────────────────────────────
# Set/cleared by REST endpoints + scheduler ticks. Persistent state (snoozed
# until, manual overrides) intentionally NOT here — would need DB. Keep simple
# for P1.
_RUNTIME_STATE: dict[str, dict[str, Any]] = {}


def _state(name: str) -> dict[str, Any]:
    return _RUNTIME_STATE.setdefault(
        name,
        {
            "enabled": True,
            "snoozed_until_ts": None,
            "last_run_ts": None,
            "last_run_status": None,
            "manual_runs_count": 0,
        },
    )


def mark_run(name: str, status: str) -> None:
    s = _state(name)
    s["last_run_ts"] = time.time()
    s["last_run_status"] = status


def mark_manual_run(name: str) -> None:
    _state(name)["manual_runs_count"] += 1


def set_enabled(name: str, enabled: bool) -> None:
    _state(name)["enabled"] = bool(enabled)


def set_snoozed_until(name: str, ts: float | None) -> None:
    _state(name)["snoozed_until_ts"] = ts


def is_active(name: str) -> bool:
    """Agent can fire now? (enabled AND not snoozed)."""
    s = _state(name)
    if not s["enabled"]:
        return False
    snz = s["snoozed_until_ts"]
    return not (snz and snz > time.time())


def runtime_state(name: str) -> dict[str, Any]:
    return dict(_state(name))


# ── Registry (declarations only — runtime state above) ───────────────────────

REGISTRY: dict[str, AgentSpec] = {
    # ─ Behavioral discipline ────────────────────────────────────────────────
    "pre-trade-check": AgentSpec(
        name="pre-trade-check",
        description="FOMO filter + stop discipline gate before every entry",
        trigger="event",
        event_types=["watchlist_add", "frontend_trade_intent"],
        runner_ref="app.services.skill_llm_runner:run_pre_trade_check",
        model_pref="fast",
        auto_execute=True,
        category="behavioral",
        horizon_days=21,
    ),
    "weekly-mirror": AgentSpec(
        name="weekly-mirror",
        description="Sunday cold honest performance review w/ verdict",
        trigger="cron",
        schedule="0 23 * * 0",  # Sunday 7pm ET ≈ 11pm UTC standard time
        runner_ref="app.services.skill_llm_runner:run_weekly_mirror",
        model_pref="reasoning",
        auto_execute=True,
        category="behavioral",
        horizon_days=7,
    ),
    # ─ Trading skills (event/intra-day) ─────────────────────────────────────
    "stock-analysis": AgentSpec(
        name="stock-analysis",
        description="Goldman+Citadel deep-dive — fires on alert or crowding shift",
        trigger="event",
        event_types=["alert_triggered", "crowding_flip"],
        runner_ref="app.services.skill_llm_runner:run_stock_analysis_for_ticker",
        model_pref="fast",
        category="trading",
        horizon_days=21,
    ),
    "earnings-analyzer": AgentSpec(
        name="earnings-analyzer",
        description="Pre-earnings JPMorgan check — fires 3d before earnings on >2% holdings",
        trigger="event",
        event_types=["earnings_imminent_3d"],
        runner_ref="app.services.skill_llm_runner:run_earnings_analyzer",
        model_pref="fast",
        category="trading",
        horizon_days=14,
    ),
    "earnings-postmortem": AgentSpec(
        name="earnings-postmortem",
        description="Post-report beat/miss breakdown — fires on >5% surprise",
        trigger="event",
        event_types=["earnings_surprise"],
        runner_ref="app.services.skill_llm_runner:run_earnings_postmortem_ctx",
        model_pref="reasoning",
        category="trading",
        horizon_days=21,
    ),
    "adversarial-research": AgentSpec(
        name="adversarial-research",
        description="Bull/bear/consensus pipeline — nightly on top-N holdings",
        trigger="cron",
        schedule="0 4 * * *",  # nightly 4am UTC
        runner_ref="app.services.skill_llm_runner:run_adversarial_research_ctx",
        model_pref="reasoning",
        category="research",
        horizon_days=30,
    ),
    "cash-deployment": AgentSpec(
        name="cash-deployment",
        description="Cash deployment plan — fires when settled cash > $500",
        trigger="event",
        event_types=["settled_cash_above_threshold"],
        runner_ref="app.services.skill_llm_runner:run_cash_deployment",
        model_pref="fast",
        auto_execute=False,
        category="trading",
        horizon_days=14,
    ),
    "top-trades-today": AgentSpec(
        name="top-trades-today",
        description="Ranked decision-ready trade ideas — weekday pre-open push",
        trigger="cron",
        schedule="0 11 * * 1-5",  # 7am ET ≈ 11am UTC standard
        runner_ref="app.services.skill_runner:run_top_trades_today",
        model_pref="fast",
        category="trading",
        horizon_days=1,
    ),
    "position-review": AgentSpec(
        name="position-review",
        description="HOLD/TRIM/SELL verdict sweep over top holdings — nightly",
        trigger="cron",
        schedule="0 2 * * *",  # nightly 2am UTC
        runner_ref="app.services.skill_runner:run_position_review",
        model_pref="fast",
        category="trading",
        horizon_days=21,
    ),
    # ─ Portfolio health (cron) ───────────────────────────────────────────────
    "daily-briefing": AgentSpec(
        name="daily-briefing",
        description="Morning portfolio digest — weekdays 7am ET pre-open",
        trigger="cron",
        schedule="0 11 * * 1-5",  # 7am ET ≈ 11am UTC standard
        runner_ref="app.services.skill_llm_runner:run_daily_briefing",
        model_pref="fast",
        category="portfolio",
        horizon_days=1,
    ),
    "portfolio-health": AgentSpec(
        name="portfolio-health",
        description="BlackRock health score — daily nightly",
        trigger="cron",
        schedule="0 3 * * *",
        runner_ref="app.services.skill_llm_runner:run_portfolio_health",
        model_pref="fast",
        category="portfolio",
        horizon_days=30,
    ),
    "risk-assessment": AgentSpec(
        name="risk-assessment",
        description="Bridgewater risk profile — weekly Friday close",
        trigger="cron",
        schedule="0 22 * * 5",  # Friday 6pm ET ≈ 10pm UTC
        runner_ref="app.services.skill_llm_runner:run_risk_assessment",
        model_pref="fast",
        category="portfolio",
        horizon_days=30,
    ),
    "macro-impact": AgentSpec(
        name="macro-impact",
        description="McKinsey macro brief — fires on regime flip + weekly",
        trigger="event",
        event_types=["regime_flip", "fomc_decision", "cpi_release"],
        runner_ref="app.services.skill_llm_runner:run_macro_impact",
        model_pref="fast",
        category="portfolio",
        horizon_days=30,
    ),
    # ─ Wealth-building (monthly) ────────────────────────────────────────────
    "auto-rebalance": AgentSpec(
        name="auto-rebalance",
        description="Monthly core ETF rebalance + DCA prompt — 1st of month",
        trigger="cron",
        schedule="0 13 1 * *",  # 1st of month 9am ET ≈ 1pm UTC
        runner_ref="app.services.skill_llm_runner:run_auto_rebalance",
        model_pref="fast",
        auto_execute=False,
        category="portfolio",
        horizon_days=30,
    ),
    "dividend-strategy": AgentSpec(
        name="dividend-strategy",
        description="Harvard endowment income view — quarterly",
        trigger="cron",
        schedule="0 13 1 1,4,7,10 *",  # quarterly 1st
        runner_ref="app.services.skill_llm_runner:run_dividend_strategy",
        model_pref="fast",
        category="portfolio",
        horizon_days=90,
    ),
    "sector-rotation": AgentSpec(
        name="sector-rotation",
        description="Renaissance sector flow tilt — bi-weekly",
        trigger="cron",
        schedule="0 13 1,15 * *",
        runner_ref="app.services.skill_llm_runner:run_sector_rotation",
        model_pref="fast",
        category="research",
        horizon_days=30,
    ),
    "tax-loss-review": AgentSpec(
        name="tax-loss-review",
        description="Canadian tax-loss harvest review — Nov 15 + Dec 15",
        trigger="cron",
        schedule="0 13 15 11,12 *",
        runner_ref="app.services.skill_llm_runner:run_tax_loss_review",
        model_pref="fast",
        auto_execute=False,
        category="portfolio",
        horizon_days=30,
    ),
}


def list_agents() -> list[dict[str, Any]]:
    """All agents w/ static spec + runtime state. Used by /api/agents."""
    out = []
    for spec in REGISTRY.values():
        state = runtime_state(spec.name)
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                "trigger": spec.trigger,
                "schedule": spec.schedule,
                "event_types": spec.event_types,
                "model_pref": spec.model_pref,
                "auto_execute": spec.auto_execute,
                "category": spec.category,
                "horizon_days": spec.horizon_days,
                **state,
            }
        )
    return out


def get_agent(name: str) -> AgentSpec | None:
    return REGISTRY.get(name)


def resolve_runner(spec: AgentSpec) -> Callable | None:
    """Lazy-import runner_ref="module.path:function" → callable."""
    if ":" not in spec.runner_ref:
        return None
    mod_path, fn_name = spec.runner_ref.split(":", 1)
    try:
        mod = __import__(mod_path, fromlist=[fn_name])
        return getattr(mod, fn_name, None)
    except Exception:
        return None


def cron_agents() -> list[AgentSpec]:
    return [s for s in REGISTRY.values() if s.trigger == "cron"]


def event_agents_for(event_type: str) -> list[AgentSpec]:
    return [s for s in REGISTRY.values() if s.trigger == "event" and event_type in s.event_types]


# ── Cron-due check (minimal cron parser — supports 5-field standard) ─────────


def _cron_field_match(field: str, value: int) -> bool:
    """Match one cron field against an int. Supports: *, N, A-B, A,B,C, */N."""
    if field == "*":
        return True
    for part in field.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step_i = int(step)
            if base in ("", "*"):
                return value % step_i == 0
            start = int(base)
            return value >= start and (value - start) % step_i == 0
        if "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                return True
        else:
            if int(part) == value:
                return True
    return False


def is_cron_due(schedule: str, now: datetime | None = None) -> bool:
    """True if cron expr matches current minute. 5 fields: min hour dom mon dow.

    dow: 0=Sunday ... 6=Saturday (cron-standard).
    Match resolution = 1 minute — caller must dedupe per-day if running coarser.
    """
    if not schedule:
        return False
    now = now or datetime.now(timezone.utc)
    fields = schedule.split()
    if len(fields) != 5:
        return False
    minute, hour, dom, mon, dow = fields
    dow_now = (now.weekday() + 1) % 7  # python: Mon=0; cron: Sun=0
    return (
        _cron_field_match(minute, now.minute)
        and _cron_field_match(hour, now.hour)
        and _cron_field_match(dom, now.day)
        and _cron_field_match(mon, now.month)
        and _cron_field_match(dow, dow_now)
    )


def cron_due_agents(now: datetime | None = None) -> list[AgentSpec]:
    """Cron agents whose schedule matches current minute AND are active."""
    return [s for s in cron_agents() if is_cron_due(s.schedule or "", now) and is_active(s.name)]
