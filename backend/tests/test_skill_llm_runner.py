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
    _set_llm_response(monkeypatch, {
        "verdict": "buy",
        "bull_thesis": "secular AI tailwind",
        "bear_thesis": "rich multiple",
        "key_risk": "regulatory",
    })
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
    _set_llm_response(monkeypatch, {
        "verdict": "miss",
        "thesis_change": "broken",
        "action": "exit",
        "reason": "guidance cut 20%",
    })
    snap = asyncio.run(
        r.run_earnings_postmortem("SHOP.TO", {
            "earnings_date": "2026-05-15",
            "surprise_pct": -12.5,
        }),
    )
    assert snap["status"] == "ok"
    assert snap["actionable"][0]["symbol"] == "SHOP.TO"
    assert snap["actionable"][0]["recommendation"] == "exit"
    assert snap["actionable"][0]["thesis_change"] == "broken"


# ── stock-compare ───────────────────────────────────────────────────────────

def test_stock_compare_picks_winner(monkeypatch):
    _set_llm_response(monkeypatch, {
        "winner": "MSFT",
        "reason": "broader AI moat",
        "loser_action": "trim",
    })
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
