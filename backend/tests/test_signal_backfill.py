"""Unit tests for signal_backfill (PG realized-return port). DB-free -
repo + data_router calls are monkeypatched."""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.services import signal_backfill as sb
from app.db.repositories import signals_repo
from app.services import data_router


# ── pure compute ──────────────────────────────────────────────────────────────


def test_compute_return_long_win():
    assert sb.compute_return(100.0, 110.0, "BUY") == 10.0


def test_compute_return_long_loss():
    assert sb.compute_return(100.0, 90.0, "ADD") == -10.0


def test_compute_return_short_is_sign_flipped():
    # a SELL that fell 10% is a +10% correct call
    assert sb.compute_return(100.0, 90.0, "SELL") == 10.0
    assert sb.compute_return(100.0, 110.0, "TRIM") == -10.0


def test_compute_return_guards_bad_entry():
    assert sb.compute_return(0.0, 110.0, "BUY") is None
    assert sb.compute_return(None, 110.0, "BUY") is None
    assert sb.compute_return(100.0, None, "BUY") is None


# ── orchestrator ────────────────────────────────────────────────────────────────


def _bars_from(start: datetime, n: int, start_close: float) -> list[dict]:
    return [{"date": f"2026-01-{str(start.day + i).zfill(2)}", "close": start_close + i} for i in range(n)]


def test_run_writes_realized_return(monkeypatch):
    sig_ts = datetime(2026, 1, 1)
    candidate = {
        "tenant_hash": "t1",
        "symbol": "AAA",
        "ts": sig_ts,
        "entry_price": 100.0,
        "action": "BUY",
    }

    async def fake_rows(horizon_days, *, batch_limit=500):
        return [candidate] if horizon_days == 5 else []

    written: list[tuple] = []

    async def fake_set(tenant_hash, symbol, ts, horizon_days, ret_pct):
        written.append((tenant_hash, symbol, ts, horizon_days, ret_pct))

    # 10 ascending bars from 2026-01-01 close=100; exit at +5 -> close=105
    bars = _bars_from(sig_ts, 10, 100.0)

    monkeypatch.setattr(signals_repo, "rows_needing_backfill", fake_rows)
    monkeypatch.setattr(signals_repo, "set_realized_return", fake_set)
    monkeypatch.setattr(data_router, "get_history", lambda *a, **k: bars)

    result = asyncio.run(sb.run(horizons=(5,)))

    assert result["written"] == 1
    assert result["per_horizon"]["h5"] == 1
    assert len(written) == 1
    t, sym, ts, h, ret = written[0]
    assert (t, sym, ts, h) == ("t1", "AAA", sig_ts, 5)
    # entry 100 -> exit 105 long = +5%
    assert ret == 5.0


def test_run_derives_entry_when_null(monkeypatch):
    sig_ts = datetime(2026, 1, 1)
    candidate = {
        "tenant_hash": "t1",
        "symbol": "CCC",
        "ts": sig_ts,
        "entry_price": None,  # legacy row, no recorded entry
        "action": "BUY",
    }

    async def fake_rows(horizon_days, *, batch_limit=500):
        return [candidate] if horizon_days == 5 else []

    set_realized: list[tuple] = []
    set_entry: list[tuple] = []

    async def fake_set_realized(tenant_hash, symbol, ts, horizon_days, ret_pct):
        set_realized.append((symbol, ret_pct))

    async def fake_set_entry(tenant_hash, symbol, ts, entry_price):
        set_entry.append((symbol, entry_price))

    bars = _bars_from(sig_ts, 10, 100.0)  # close@signal-date=100, +5d=105

    monkeypatch.setattr(signals_repo, "rows_needing_backfill", fake_rows)
    monkeypatch.setattr(signals_repo, "set_realized_return", fake_set_realized)
    monkeypatch.setattr(signals_repo, "set_entry_price", fake_set_entry)
    monkeypatch.setattr(data_router, "get_history", lambda *a, **k: bars)

    result = asyncio.run(sb.run(horizons=(5,)))

    assert result["written"] == 1
    assert set_entry == [("CCC", 100.0)]  # derived entry persisted
    assert set_realized == [("CCC", 5.0)]  # 100 -> 105 long = +5%


def test_run_skips_when_no_bars(monkeypatch):
    candidate = {
        "tenant_hash": "t1",
        "symbol": "BBB",
        "ts": datetime(2026, 1, 1),
        "entry_price": 100.0,
        "action": "BUY",
    }

    async def fake_rows(horizon_days, *, batch_limit=500):
        return [candidate] if horizon_days == 5 else []

    async def fake_set(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("set_realized_return called despite no bars")

    monkeypatch.setattr(signals_repo, "rows_needing_backfill", fake_rows)
    monkeypatch.setattr(signals_repo, "set_realized_return", fake_set)
    monkeypatch.setattr(data_router, "get_history", lambda *a, **k: [])

    result = asyncio.run(sb.run(horizons=(5,)))
    assert result["written"] == 0
    assert result["skipped_data"] == 1
