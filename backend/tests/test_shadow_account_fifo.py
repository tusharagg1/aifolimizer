"""Quantity-aware FIFO pairing — regression test for the audit P1 fix.

Earlier impl popped a whole buy lot on the first matching sell, which
mispaired scale-in / scale-out flows. The fix tracks remaining qty per
buy lot and consumes `min(buy_rem, sell_rem)` per slice. These tests
lock that behaviour in.
"""
from __future__ import annotations

from app.services.shadow_account import _fifo_pair


def _trades(*rows: tuple[str, str, str, float, float]) -> list[dict]:
    return [
        {
            "symbol": sym,
            "side": side,
            "date": date + "T10:00:00",
            "price": price,
            "quantity": qty,
        }
        for sym, side, date, price, qty in rows
    ]


def test_one_to_one_pair_uses_full_qty() -> None:
    rt = _fifo_pair(
        _trades(
            ("AAA", "buy",  "2024-01-01", 100.0, 5.0),
            ("AAA", "sell", "2024-01-10", 110.0, 5.0),
        )
    )
    assert len(rt) == 1
    assert rt[0]["quantity"] == 5.0
    assert rt[0]["return_pct"] == 10.0


def test_partial_sell_leaves_remainder_for_next_sell() -> None:
    """Buy 10 → sell 4 → sell 6. Should produce two roundtrips, qty 4 and 6."""
    rt = _fifo_pair(
        _trades(
            ("AAA", "buy",  "2024-01-01", 100.0, 10.0),
            ("AAA", "sell", "2024-01-05", 110.0, 4.0),
            ("AAA", "sell", "2024-01-10", 120.0, 6.0),
        )
    )
    assert len(rt) == 2
    qtys = sorted(r["quantity"] for r in rt)
    assert qtys == [4.0, 6.0]
    # First sell at 110 = +10%; second at 120 = +20%.
    rets = sorted(r["return_pct"] for r in rt)
    assert rets == [10.0, 20.0]


def test_scale_in_then_single_sell_consumes_lots_in_order() -> None:
    """Buy 3, buy 4 → sell 5. First three from lot 1, next two from lot 2."""
    rt = _fifo_pair(
        _trades(
            ("AAA", "buy",  "2024-01-01", 100.0, 3.0),
            ("AAA", "buy",  "2024-01-02", 105.0, 4.0),
            ("AAA", "sell", "2024-01-10", 120.0, 5.0),
        )
    )
    assert len(rt) == 2
    # Roundtrips ordered by sell event; both from same sell at 120.
    qtys = [r["quantity"] for r in rt]
    assert sorted(qtys) == [2.0, 3.0]
    # 100→120 = +20%, 105→120 = +14.29%
    rets = sorted(r["return_pct"] for r in rt)
    assert rets[0] == 14.29
    assert rets[1] == 20.0


def test_orphan_sell_without_buy_inventory_is_dropped() -> None:
    rt = _fifo_pair(_trades(("AAA", "sell", "2024-01-10", 100.0, 1.0)))
    assert rt == []


def test_zero_quantity_falls_back_to_one() -> None:
    """Defensive: malformed inputs with qty=0 still produce a roundtrip."""
    rt = _fifo_pair(
        [
            {"symbol": "AAA", "side": "buy",
             "date": "2024-01-01T10:00:00", "price": 100.0, "quantity": 0},
            {"symbol": "AAA", "side": "sell",
             "date": "2024-01-10T10:00:00", "price": 110.0, "quantity": 0},
        ]
    )
    assert len(rt) == 1
    assert rt[0]["quantity"] == 1.0
