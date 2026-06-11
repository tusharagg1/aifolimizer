"""get_recommendations drops non-equity asset classes (commodity/bond/cash)
before scoring. Commodity-only input short-circuits to [] with no network."""

from __future__ import annotations

from app.services import recommendations as rec


def test_commodity_only_returns_empty_no_network():
    # GOLD (WS PRECIOUS_METAL -> asset_class "commodity") must not be equity-scored.
    out = rec.get_recommendations([{"symbol": "GOLD", "asset_class": "commodity", "weight": 5.0}])
    assert out == []


def test_bond_and_cash_excluded():
    out = rec.get_recommendations(
        [
            {"symbol": "XBB", "asset_class": "bond", "weight": 3.0},
            {"symbol": "CASH1", "asset_class": "cash", "weight": 1.0},
        ]
    )
    assert out == []


def test_asset_class_check_is_case_insensitive():
    out = rec.get_recommendations([{"symbol": "GOLD", "asset_class": "COMMODITY", "weight": 5.0}])
    assert out == []
