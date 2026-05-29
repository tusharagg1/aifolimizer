"""Offline tests for symbol_resolver + watchlist add validation.

resolve() itself is network-bound (yfinance) so it is monkeypatched here;
the live resolution is checked separately. These tests pin the pure mapping
and the add-time validation contract (reject unknown, fail-open on errors).
"""

import pytest

from app.services import symbol_resolver as sr
from app.services import watchlist as wl


def test_asset_class_mapping():
    assert sr._asset_class("ETF") == "etf"
    assert sr._asset_class("MUTUALFUND") == "etf"
    assert sr._asset_class("CRYPTOCURRENCY") == "crypto"
    assert sr._asset_class("INDEX") == "index"
    assert sr._asset_class("EQUITY") == "stock"
    assert sr._asset_class(None) == "stock"
    assert sr._asset_class("equity") == "stock"  # case-insensitive


def test_resolve_empty_symbol():
    out = sr.resolve("  ")
    assert out["valid"] is False
    assert out["reason"] == "empty"


def _patch_wl(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "_WATCHLIST_PATH", tmp_path / "wl.json")
    monkeypatch.setattr(wl, "_LEGACY_PATH", tmp_path / "legacy.json")


def test_add_symbol_rejects_unknown(tmp_path, monkeypatch):
    _patch_wl(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sr, "resolve", lambda s: {"valid": False, "reason": "not_found"}
    )
    with pytest.raises(ValueError):
        wl.add_symbol("ZZZZ")


def test_add_symbol_failopen_on_network_error(tmp_path, monkeypatch):
    _patch_wl(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sr, "resolve",
        lambda s: {
            "valid": False, "reason": "network_error",
            "name": None, "asset_class": "stock",
        },
    )
    items = wl.add_symbol("AAPL")
    assert any(i["symbol"] == "AAPL" for i in items)


def test_add_symbol_stores_identity(tmp_path, monkeypatch):
    _patch_wl(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sr, "resolve",
        lambda s: {
            "valid": True, "reason": "ok",
            "name": "SPDR Gold Shares", "asset_class": "etf",
        },
    )
    items = wl.add_symbol("GLD")
    row = next(i for i in items if i["symbol"] == "GLD")
    assert row["name"] == "SPDR Gold Shares"
    assert row["asset_class"] == "etf"


def test_add_symbol_blank_raises(tmp_path, monkeypatch):
    _patch_wl(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        wl.add_symbol("   ")
