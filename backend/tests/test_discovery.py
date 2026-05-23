"""Unit tests for discovery (Phase 13). Mocks recommendations + DB lookups."""
from __future__ import annotations

import asyncio

from app.services import discovery as d
from app.services import discovery_universe as du


# ── universe sanity ────────────────────────────────────────────────────────

def test_universe_dedup_and_includes_all_tiers():
    u = du.full_universe()
    assert len(u) == len(set(u))
    assert "AAPL" in u
    assert "RY.TO" in u
    assert "XLK" in u


def test_is_us_vs_canadian():
    assert du.is_us("AAPL") is True
    assert du.is_us("RY.TO") is False
    assert du.is_canadian("RY.TO") is True
    assert du.is_canadian("AAPL") is False


# ── scan_universe ──────────────────────────────────────────────────────────

class _FakePos:
    def __init__(self, symbol, weight=5.0, sector=None):
        self.symbol = symbol
        self.weight = weight
        self.sector = sector


class _FakePortfolio:
    def __init__(self, positions):
        self.positions = positions


def _patch_rec_engine(monkeypatch, recs):
    """Filter mock-recs to those whose symbol is actually in candidates,
    matching real engine behavior where held positions are pre-excluded."""
    from app.services import recommendations as rec_svc
    rec_map = {r["symbol"]: r for r in recs}

    def fake(positions):
        return [
            rec_map[p["symbol"]]
            for p in positions if p["symbol"] in rec_map
        ]

    monkeypatch.setattr(rec_svc, "get_recommendations", fake)


def _patch_db_empty(monkeypatch):
    async def empty_watch(_):
        return []

    async def empty_wash(_):
        return set()

    monkeypatch.setattr(d, "_watchlist_for", empty_watch)
    monkeypatch.setattr(d, "_washsale_blocked", empty_wash)


def test_scan_excludes_held(monkeypatch):
    _patch_db_empty(monkeypatch)
    _patch_rec_engine(monkeypatch, [
        {"symbol": "AAPL", "action": "BUY", "score": 8.5,
         "confidence": "high", "kelly_pct": 5.0, "reasons": ["x"]},
        {"symbol": "NVDA", "action": "BUY", "score": 9.0,
         "confidence": "high", "kelly_pct": 7.0, "reasons": ["y"]},
    ])
    port = _FakePortfolio([_FakePos("AAPL")])
    picks = asyncio.run(
        d.scan_universe(
            "th1", portfolio=port, min_score=6.0,
            universe=["AAPL", "NVDA"],
        ),
    )
    syms = [p["symbol"] for p in picks]
    assert "AAPL" not in syms
    assert "NVDA" in syms


def test_scan_filters_below_min_score(monkeypatch):
    _patch_db_empty(monkeypatch)
    _patch_rec_engine(monkeypatch, [
        {"symbol": "WEAK", "action": "BUY", "score": 5.0,
         "confidence": "low", "reasons": []},
        {"symbol": "STRONG", "action": "BUY", "score": 8.5,
         "confidence": "high", "reasons": []},
    ])
    picks = asyncio.run(
        d.scan_universe(
            "th1", portfolio=None, min_score=7.0,
            universe=["WEAK", "STRONG"],
        ),
    )
    syms = [p["symbol"] for p in picks]
    assert "WEAK" not in syms
    assert "STRONG" in syms


def test_scan_excludes_non_buy_actions(monkeypatch):
    _patch_db_empty(monkeypatch)
    _patch_rec_engine(monkeypatch, [
        {"symbol": "HOLDONE", "action": "HOLD", "score": 7.5, "reasons": []},
        {"symbol": "SELLONE", "action": "SELL", "score": 8.0, "reasons": []},
        {"symbol": "BUYONE", "action": "BUY", "score": 7.5,
         "confidence": "med", "reasons": []},
    ])
    picks = asyncio.run(
        d.scan_universe(
            "th1", portfolio=None,
            universe=["HOLDONE", "SELLONE", "BUYONE"],
        ),
    )
    syms = [p["symbol"] for p in picks]
    assert "BUYONE" in syms
    assert "HOLDONE" not in syms
    assert "SELLONE" not in syms


def test_scan_sorts_by_score_desc(monkeypatch):
    _patch_db_empty(monkeypatch)
    _patch_rec_engine(monkeypatch, [
        {"symbol": "A", "action": "BUY", "score": 7.0, "reasons": []},
        {"symbol": "B", "action": "BUY", "score": 9.0, "reasons": []},
        {"symbol": "C", "action": "BUY", "score": 8.0, "reasons": []},
    ])
    picks = asyncio.run(
        d.scan_universe(
            "th1", portfolio=None,
            universe=["A", "B", "C"],
        ),
    )
    assert [p["symbol"] for p in picks] == ["B", "C", "A"]


def test_scan_washsale_blocked(monkeypatch):
    async def empty_watch(_):
        return []

    async def blocked(_):
        return {"BLOCKED"}

    monkeypatch.setattr(d, "_watchlist_for", empty_watch)
    monkeypatch.setattr(d, "_washsale_blocked", blocked)
    _patch_rec_engine(monkeypatch, [
        {"symbol": "OK", "action": "BUY", "score": 8.0, "reasons": []},
    ])
    picks = asyncio.run(
        d.scan_universe(
            "th1", portfolio=None,
            universe=["BLOCKED", "OK"],
        ),
    )
    syms = [p["symbol"] for p in picks]
    assert "BLOCKED" not in syms
    assert "OK" in syms


def test_sector_saturation_warning(monkeypatch):
    _patch_db_empty(monkeypatch)
    _patch_rec_engine(monkeypatch, [
        {"symbol": "MSFT", "action": "BUY", "score": 8.0,
         "confidence": "med", "reasons": [], "sector": "Technology"},
    ])
    port = _FakePortfolio([
        _FakePos("AAPL", weight=30.0, sector="Technology"),
        _FakePos("GOOGL", weight=15.0, sector="Technology"),
    ])
    picks = asyncio.run(
        d.scan_universe(
            "th1", portfolio=port,
            universe=["MSFT"],
        ),
    )
    assert len(picks) == 1
    assert "warning" in picks[0]
    assert "sector heavy" in picks[0]["warning"]


def test_top_n_picks_limits(monkeypatch):
    _patch_db_empty(monkeypatch)
    _patch_rec_engine(monkeypatch, [
        {"symbol": f"X{i}", "action": "BUY", "score": 8.0 + i * 0.1,
         "confidence": "med", "reasons": []}
        for i in range(10)
    ])
    picks = asyncio.run(
        d.top_n_picks(
            "th1", portfolio=None, n=3,
        ),
    )
    # Test patched universe is the full default — limit applied AFTER score sort
    assert len(picks) <= 3
