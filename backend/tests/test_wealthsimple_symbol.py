"""_qualify_symbol: maps WS exchange-prefixed symbols to Yahoo form.
Pure, no network/session."""

from __future__ import annotations

from app.services.wealthsimple import _qualify_symbol


def test_canadian_exchanges_get_suffix():
    assert _qualify_symbol("TSX:XEQT") == "XEQT.TO"
    assert _qualify_symbol("TSXV:ABC") == "ABC.V"
    assert _qualify_symbol("NEO:HBND") == "HBND.NE"
    assert _qualify_symbol("CSE:XYZ") == "XYZ.CN"


def test_us_exchanges_strip_to_bare():
    assert _qualify_symbol("NYSE:T") == "T"
    assert _qualify_symbol("NASDAQ:MSFT") == "MSFT"


def test_dual_listed_disambiguation():
    # the prefix is the only thing distinguishing AT&T from Telus
    assert _qualify_symbol("NYSE:T") == "T"  # AT&T
    assert _qualify_symbol("TSX:T") == "T.TO"  # Telus


def test_bare_symbol_unchanged():
    assert _qualify_symbol("XEQT") == "XEQT"
    assert _qualify_symbol("MSFT") == "MSFT"


def test_already_suffixed_not_double_suffixed():
    assert _qualify_symbol("TSX:XEQT.TO") == "XEQT.TO"


def test_unknown_exchange_falls_back_to_bare_ticker():
    assert _qualify_symbol("LSE:VOD") == "VOD"
