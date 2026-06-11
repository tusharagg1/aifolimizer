"""_resolve_fetch_symbol: exchange-qualifies bare CA names for provider lookup.
Pure, no network."""

from __future__ import annotations

from app.services import data_router as dr


def test_bare_known_canadian_gets_to_suffix():
    assert dr._resolve_fetch_symbol("XEQT") == "XEQT.TO"
    assert dr._resolve_fetch_symbol("VFV") == "VFV.TO"


def test_us_equity_untouched():
    assert dr._resolve_fetch_symbol("MSFT") == "MSFT"
    assert dr._resolve_fetch_symbol("AAPL") == "AAPL"


def test_already_suffixed_untouched():
    assert dr._resolve_fetch_symbol("XEQT.TO") == "XEQT.TO"
    assert dr._resolve_fetch_symbol("SHOP.V") == "SHOP.V"


def test_non_equity_untouched():
    assert dr._resolve_fetch_symbol("BTC") == "BTC"
    assert dr._resolve_fetch_symbol("^GSPC") == "^GSPC"
