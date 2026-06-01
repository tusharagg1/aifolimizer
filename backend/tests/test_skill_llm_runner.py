"""Unit tests for skill_llm_runner (Phase 7).

LLM calls are mocked — tests verify wrapper semantics, JSON parsing,
graceful degrade, and snapshot shape.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import skill_llm_runner as r


# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    """Default: providers available, _call_llm_json returns a canned dict."""
    monkeypatch.setattr(r, "_providers_available", lambda: True)
    yield


def _set_llm_response(monkeypatch, payload):
    async def _fake(prompt, system, *, task=None):
        return payload

    monkeypatch.setattr(r, "_call_llm_json", _fake)


# ── adversarial-research ────────────────────────────────────────────────────


def test_adversarial_research_buy_verdict(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "verdict": "buy",
            "bull_thesis": "secular AI tailwind",
            "bear_thesis": "rich multiple",
            "key_risk": "regulatory",
        },
    )
    snap = asyncio.run(
        r.run_adversarial_research("NVDA", {"weight": 5.0, "sector": "Tech"}),
    )
    assert snap["skill"] == "adversarial-research"
    assert snap["status"] == "ok"
    assert snap["actionable"][0]["symbol"] == "NVDA"
    assert snap["actionable"][0]["action"] == "BUY"
    assert "regulatory" in (snap["alerts"][0]["message"])


def test_adversarial_research_no_providers(monkeypatch):
    monkeypatch.setattr(r, "_providers_available", lambda: False)
    snap = asyncio.run(r.run_adversarial_research("AAPL", {}))
    assert snap["status"] == "error"
    assert snap["error"] == "no_llm_provider"
    assert snap["actionable"] == []


def test_adversarial_research_llm_failure(monkeypatch):
    async def _fail(prompt, system, *, task=None):
        return None

    monkeypatch.setattr(r, "_call_llm_json", _fail)
    snap = asyncio.run(r.run_adversarial_research("AAPL", {}))
    assert snap["status"] == "error"
    assert snap["error"] == "llm_no_response"


# ── earnings-postmortem ─────────────────────────────────────────────────────


def test_earnings_postmortem_thesis_broken(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "verdict": "miss",
            "thesis_change": "broken",
            "action": "exit",
            "reason": "guidance cut 20%",
        },
    )
    snap = asyncio.run(
        r.run_earnings_postmortem(
            "SHOP.TO",
            {
                "earnings_date": "2026-05-15",
                "surprise_pct": -12.5,
            },
        ),
    )
    assert snap["status"] == "ok"
    assert snap["actionable"][0]["symbol"] == "SHOP.TO"
    assert snap["actionable"][0]["recommendation"] == "exit"
    assert snap["actionable"][0]["thesis_change"] == "broken"


# ── stock-compare ───────────────────────────────────────────────────────────


def test_stock_compare_picks_winner(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "winner": "MSFT",
            "reason": "broader AI moat",
            "loser_action": "trim",
        },
    )
    snap = asyncio.run(r.run_stock_compare("MSFT", "GOOG"))
    assert snap["status"] == "ok"
    actionable_syms = [a["symbol"] for a in snap["actionable"]]
    assert "MSFT" in actionable_syms
    assert "GOOG" in actionable_syms
    # winner is BUY
    winner_row = next(a for a in snap["actionable"] if a["symbol"] == "MSFT")
    assert winner_row["action"] == "BUY"
    loser_row = next(a for a in snap["actionable"] if a["symbol"] == "GOOG")
    assert loser_row["recommendation"] == "trim"


# ── JSON parsing ────────────────────────────────────────────────────────────


def test_parse_json_safe_fenced_code():
    text = '```json\n{"verdict": "buy"}\n```'
    assert r._parse_json_safe(text) == {"verdict": "buy"}


def test_parse_json_safe_malformed_returns_none():
    assert r._parse_json_safe("not json") is None


def test_parse_json_safe_empty_returns_none():
    assert r._parse_json_safe("") is None
    assert r._parse_json_safe(None) is None


def test_parse_json_safe_strips_think_block():
    text = '<think>\nLet me reason about this...\n</think>\n{"verdict": "buy"}'
    assert r._parse_json_safe(text) == {"verdict": "buy"}


def test_parse_json_safe_recovers_json_from_prose():
    text = 'Sure, here it is: {"verdict": "sell"} hope this helps.'
    assert r._parse_json_safe(text) == {"verdict": "sell"}


# ── risk-assessment ─────────────────────────────────────────────────────────


def test_risk_assessment_elevated(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "risk_level": "elevated",
            "top_risk": "concentration",
            "action": "reduce",
            "reason": "single position 35%",
        },
    )
    snap = asyncio.run(r.run_risk_assessment({"top_weight_pct": 35.0}))
    assert snap["skill"] == "risk-assessment"
    assert snap["status"] == "ok"
    assert snap["summary"]["risk_level"] == "elevated"
    assert snap["actionable"][0]["recommendation"] == "reduce"
    assert any("Risk level: elevated" in a["message"] for a in snap["alerts"])


def test_risk_assessment_low_emits_no_alert(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "risk_level": "low",
            "top_risk": "macro",
            "action": "hold",
            "reason": "diversified",
        },
    )
    snap = asyncio.run(r.run_risk_assessment({}))
    assert snap["alerts"] == []


# ── portfolio-health ────────────────────────────────────────────────────────


def test_portfolio_health_unhealthy(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "health": "unhealthy",
            "top_issue": "concentration",
            "action": "rebalance",
            "reason": "65% in tech",
        },
    )
    snap = asyncio.run(r.run_portfolio_health({"top_sector_pct": 65}))
    assert snap["summary"]["health"] == "unhealthy"
    assert snap["actionable"][0]["recommendation"] == "rebalance"
    assert len(snap["alerts"]) == 1


def test_portfolio_health_healthy_no_alert(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "health": "healthy",
            "top_issue": "none",
            "action": "hold",
            "reason": "well diversified",
        },
    )
    snap = asyncio.run(r.run_portfolio_health({}))
    assert snap["alerts"] == []


# ── macro-impact ────────────────────────────────────────────────────────────


def test_macro_impact_risk_off(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "regime": "risk_off",
            "posture": "defense",
            "action": "rotate_defensive",
            "reason": "Fed funds > 10y, yield inverted",
        },
    )
    snap = asyncio.run(r.run_macro_impact({"fed_funds": 5.5, "ten_y_yield": 4.2}))
    assert snap["summary"]["regime"] == "risk_off"
    assert snap["summary"]["posture"] == "defense"
    assert snap["actionable"][0]["recommendation"] == "rotate_defensive"


# ── daily-briefing ──────────────────────────────────────────────────────────


def test_daily_briefing(monkeypatch):
    _set_llm_response(
        monkeypatch,
        {
            "headline": "Tech rebound continues, watch NVDA earnings",
            "top_concern": "VIX still 22",
            "top_opportunity": "Energy oversold",
            "action_today": "hold and monitor NVDA pre-market",
        },
    )
    snap = asyncio.run(r.run_daily_briefing({"date": "2026-05-24"}))
    assert snap["summary"]["headline"].startswith("Tech rebound")
    assert "VIX" in snap["summary"]["top_concern"]
    assert snap["actionable"][0]["recommendation"] == "hold and monitor NVDA pre-market"


# ── no-providers fallback ───────────────────────────────────────────────────


def test_risk_no_providers_returns_error(monkeypatch):
    monkeypatch.setattr(r, "_providers_available", lambda: False)
    snap = asyncio.run(r.run_risk_assessment({}))
    assert snap["status"] == "error"
    assert snap["error"] == "no_llm_provider"


def test_health_no_providers_returns_error(monkeypatch):
    monkeypatch.setattr(r, "_providers_available", lambda: False)
    snap = asyncio.run(r.run_portfolio_health({}))
    assert snap["status"] == "error"


def test_macro_no_providers_returns_error(monkeypatch):
    monkeypatch.setattr(r, "_providers_available", lambda: False)
    snap = asyncio.run(r.run_macro_impact({}))
    assert snap["status"] == "error"


def test_briefing_no_providers_returns_error(monkeypatch):
    monkeypatch.setattr(r, "_providers_available", lambda: False)
    snap = asyncio.run(r.run_daily_briefing({}))
    assert snap["status"] == "error"
