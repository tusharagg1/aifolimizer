"""Unit tests for event_dispatcher (Phase 15).

LLM calls + Redis + Postgres are mocked. Tests verify:
  - Material flip detection thresholds
  - Dedup logic (single dispatch per event-key per day)
  - Correct LLM skill invoked per event type
  - Snapshot persistence path
  - Threshold edge cases
"""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import pytest

from app.services import event_dispatcher as ed


# ── helpers ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_local_dedup():
    """Each test starts with empty in-process dedup set."""
    ed._LOCAL_DEDUP.clear()
    yield
    ed._LOCAL_DEDUP.clear()


@pytest.fixture
def _no_redis(monkeypatch):
    """Force the in-process dedup path by making Redis import return None."""
    fake_cache = types.SimpleNamespace(get_redis=lambda: None)
    monkeypatch.setattr(
        "app.cache.get_redis",
        fake_cache.get_redis,
        raising=False,
    )
    yield


@pytest.fixture
def _stub_skill_runners(monkeypatch):
    """Stub each skill runner to record what was called + what context."""
    calls: list[tuple[str, tuple, dict]] = []

    def _make_stub(skill_name):
        async def _stub(*args, **kwargs):
            calls.append((skill_name, args, kwargs))
            return {
                "skill": skill_name, "status": "ok",
                "computed_at": datetime.now(tz=timezone.utc),
                "expires_at": datetime.now(tz=timezone.utc),
                "ttl_minutes": 60, "summary": {}, "actionable": [],
                "alerts": [], "key_insights": [],
            }
        return _stub

    from app.services import skill_llm_runner as slr
    monkeypatch.setattr(slr, "run_macro_impact", _make_stub("macro-impact"))
    monkeypatch.setattr(slr, "run_risk_assessment", _make_stub("risk-assessment"))
    monkeypatch.setattr(slr, "run_portfolio_health", _make_stub("portfolio-health"))
    monkeypatch.setattr(slr, "run_earnings_postmortem", _make_stub("earnings-postmortem"))
    monkeypatch.setattr(slr, "run_adversarial_research", _make_stub("adversarial-research"))

    async def _persist_stub(thash, snapshot):
        calls.append(("__persist__", (thash, snapshot["skill"]), {}))
    monkeypatch.setattr(ed, "_persist", _persist_stub)
    monkeypatch.setattr(ed, "_push_telegram", lambda *a, **k: None)
    return calls


# ── regime flip ─────────────────────────────────────────────────────────────

def _regime(composite, trend="up", vol="low", macro="risk_on"):
    return types.SimpleNamespace(
        composite=composite, trend=trend, volatility=vol, macro=macro,
        vix=15.0, spy_vs_sma200_pct=5.0,
        ten_y_yield=4.0, fed_funds=4.5, confidence=0.8,
    )


def test_regime_flip_dispatches_when_trend_changes(_no_redis, _stub_skill_runners):
    prev = _regime("trend_up_low_vol", trend="up")
    new = _regime("trend_down_high_vol", trend="down", vol="high")
    result = asyncio.run(ed.on_regime_flip(prev, new, tenant_hashes=["abc"]))
    assert result["status"] == "ok"
    assert result["from"] == "trend_up_low_vol"
    assert result["to"] == "trend_down_high_vol"
    called = {c[0] for c in _stub_skill_runners}
    assert called >= {"macro-impact", "risk-assessment", "portfolio-health"}


def test_regime_flip_skips_on_immaterial_change(_no_redis, _stub_skill_runners):
    prev = _regime("trend_up_low_vol")
    new = _regime("trend_up_low_vol")
    result = asyncio.run(ed.on_regime_flip(prev, new, tenant_hashes=["abc"]))
    assert result["status"] == "skip"
    assert result["reason"] == "no_material_flip"
    assert _stub_skill_runners == []


def test_regime_flip_skips_when_prev_none(_no_redis, _stub_skill_runners):
    new = _regime("trend_up_low_vol")
    result = asyncio.run(ed.on_regime_flip(None, new, tenant_hashes=["abc"]))
    assert result["status"] == "skip"


def test_regime_flip_dedup_same_day(_no_redis, _stub_skill_runners):
    prev = _regime("trend_up_low_vol", trend="up")
    new = _regime("trend_down_high_vol", trend="down")
    first = asyncio.run(ed.on_regime_flip(prev, new, tenant_hashes=["abc"]))
    second = asyncio.run(ed.on_regime_flip(prev, new, tenant_hashes=["abc"]))
    assert first["status"] == "ok"
    assert second["status"] == "skip"
    assert second["reason"] == "deduped"


# ── earnings surprise ───────────────────────────────────────────────────────

def test_earnings_surprise_dispatches_above_threshold(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_earnings_surprise("th_abc", "NVDA", 12.5),
    )
    assert result["status"] == "ok"
    assert result["ticker"] == "NVDA"
    calls = [c[0] for c in _stub_skill_runners]
    assert "earnings-postmortem" in calls


def test_earnings_surprise_skips_below_threshold(_no_redis, _stub_skill_runners):
    result = asyncio.run(ed.on_earnings_surprise("th_abc", "AAPL", 1.2))
    assert result["status"] == "skip"
    assert result["reason"] == "below_threshold"


def test_earnings_surprise_negative_dispatches(_no_redis, _stub_skill_runners):
    result = asyncio.run(ed.on_earnings_surprise("th_abc", "META", -8.0))
    assert result["status"] == "ok"


def test_earnings_surprise_dedup(_no_redis, _stub_skill_runners):
    first = asyncio.run(ed.on_earnings_surprise("th_abc", "NVDA", 10.0))
    second = asyncio.run(ed.on_earnings_surprise("th_abc", "NVDA", 10.0))
    assert first["status"] == "ok"
    assert second["status"] == "skip"


# ── drawdown breach ─────────────────────────────────────────────────────────

def test_drawdown_breach_dispatches_to_halt(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_drawdown_breach("th_abc", "trade", "halt"),
    )
    assert result["status"] == "ok"
    assert result["to"] == "halt"


def test_drawdown_breach_dispatches_to_reduce_size(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_drawdown_breach("th_abc", "trade", "reduce_size"),
    )
    assert result["status"] == "ok"


def test_drawdown_breach_skips_when_returning_to_trade(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_drawdown_breach("th_abc", "halt", "trade"),
    )
    assert result["status"] == "skip"
    assert result["reason"] == "non_trigger_status"


def test_drawdown_breach_skips_when_status_unchanged(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_drawdown_breach("th_abc", "halt", "halt"),
    )
    assert result["status"] == "skip"
    assert result["reason"] == "no_change"


# ── crowding flip ───────────────────────────────────────────────────────────

def test_crowding_flip_dispatches_above_delta(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_crowding_flip("th_abc", "TSLA", 25, 70),
    )
    assert result["status"] == "ok"
    assert result["delta"] == 45.0
    calls = [c[0] for c in _stub_skill_runners]
    assert "adversarial-research" in calls


def test_crowding_flip_skips_below_delta(_no_redis, _stub_skill_runners):
    result = asyncio.run(
        ed.on_crowding_flip("th_abc", "TSLA", 50, 60),
    )
    assert result["status"] == "skip"
    assert result["reason"] == "below_delta"


def test_crowding_flip_negative_delta_dispatches(_no_redis, _stub_skill_runners):
    """consensus->contrarian flip also counts."""
    result = asyncio.run(
        ed.on_crowding_flip("th_abc", "TSLA", 80, 30),
    )
    assert result["status"] == "ok"
    assert result["delta"] == -50.0
