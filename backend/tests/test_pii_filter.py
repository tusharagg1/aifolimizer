"""Tests for pii_filter — security guardrail per CLAUDE.md (NON-NEGOTIABLE).

Run from backend/:
  .venv\\Scripts\\python -m pytest tests/test_pii_filter.py -v
"""

from __future__ import annotations

from app.services.pii_filter import filter_portfolio, filter_user_context


# ── Fields that MUST never appear in filtered output ─────────────────────────
_PII_KEYS = {
    "account_id",
    "account_number",
    "email",
    "full_name",
    "first_name",
    "last_name",
    "phone",
    "ws_account_id",
    "ws_internal_id",
    "user_id",
    "address",
}


def _dump_keys(obj) -> set[str]:
    """Walk dict/list recursively, return every key seen."""
    seen: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            seen.add(k)
            seen.update(_dump_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            seen.update(_dump_keys(item))
    return seen


# ── filter_portfolio ─────────────────────────────────────────────────────────


def test_filter_portfolio_strips_pii_keys():
    raw = {
        "positions": [
            {
                "symbol": "AAPL",
                "name": "Apple Inc",
                "quantity": 10,
                "book_cost": 1500,
                "market_value": 1800,
                "day_change_pct": 0.5,
                "total_return_pct": 20.0,
                "weight": 5.0,
                "asset_class": "equity",
                "sector": "tech",
                # PII fields that MUST be stripped
                "account_id": "ws-internal-xyz-9999",
                "account_number": "1234567890",
                "ws_internal_id": "secret",
                "user_id": "test-user-prod",
                "email": "test@example.invalid",
                "full_name": "Test User",
            }
        ],
        "summary": {
            "total_value": 100000,
            "total_cost": 90000,
            "total_return_pct": 11.1,
            "cash_available": 1000,
            # PII at summary level
            "account_id": "must-go",
            "email": "must-go@example.com",
        },
    }

    safe = filter_portfolio(raw)
    keys = _dump_keys(safe)

    leaked = _PII_KEYS & keys
    assert not leaked, f"PII keys leaked: {leaked}"

    # Non-PII financial fields preserved
    assert safe["positions"][0]["symbol"] == "AAPL"
    assert safe["positions"][0]["market_value"] == 1800
    assert safe["summary"]["total_value"] == 100000


def test_filter_portfolio_empty_input():
    """Empty / partial dicts must not crash."""
    assert filter_portfolio({}) == {
        "positions": [],
        "summary": {
            "total_value": 0,
            "total_cost": 0,
            "total_return_pct": 0,
            "cash_available": 0,
        },
    }


def test_filter_portfolio_position_defaults():
    """Missing optional fields default to safe zeros, no key leakage."""
    raw = {"positions": [{"symbol": "XEQT.TO", "account_id": "leak-me"}], "summary": {}}
    safe = filter_portfolio(raw)
    keys = _dump_keys(safe)
    assert not (_PII_KEYS & keys)
    pos = safe["positions"][0]
    assert pos["symbol"] == "XEQT.TO"
    assert pos["quantity"] == 0
    assert pos["market_value"] == 0


# ── filter_user_context ──────────────────────────────────────────────────────


def test_filter_user_context_pseudonymizes_account_types():
    raw = {
        "accounts": [
            {
                "type": "TFSA",
                "currency": "CAD",
                "cash_balance": 5000,
                "invested_value": 50000,
                # PII that must vanish
                "account_id": "tfsa-xyz",
                "account_number": "999",
                "full_name": "Test User",
            },
            {
                "type": "Non-Reg",
                "currency": "USD",
                "cash_balance": 1000,
                "invested_value": 12000,
            },
        ],
        "total_cash": 6000,
        "total_invested": 62000,
        "account_types": ["TFSA", "Non-Reg"],
        # Top-level PII
        "email": "test@example.invalid",
        "user_id": "prod-123",
    }
    safe = filter_user_context(raw)
    keys = _dump_keys(safe)

    leaked = _PII_KEYS & keys
    assert not leaked, f"PII leaked from user context: {leaked}"

    # Account types pseudonymized to friendly labels
    labels = {a["label"] for a in safe["accounts"]}
    assert "Tax-Free Savings Account" in labels
    assert "Non-Registered Investment Account" in labels

    # Financial figures preserved, NLV relabeled and self-consistent
    assert safe["total_cash"] == 6000
    assert safe["total_nlv"] == 62000
    assert safe["total_securities"] == 56000
    assert safe["total_nlv"] == safe["total_cash"] + safe["total_securities"]
    tfsa = next(a for a in safe["accounts"] if a["label"] == "Tax-Free Savings Account")
    assert tfsa["net_liquidation_value"] == 50000
    assert tfsa["securities_value"] == 45000
    assert tfsa["net_liquidation_value"] == tfsa["cash_balance"] + tfsa["securities_value"]


def test_filter_user_context_unknown_account_type_falls_back():
    raw = {
        "accounts": [
            {
                "type": "MARGIN-PRO-PLUS",
                "currency": "CAD",
                "cash_balance": 0,
                "invested_value": 0,
            }
        ],
        "total_cash": 0,
        "total_invested": 0,
        "account_types": ["MARGIN-PRO-PLUS"],
    }
    safe = filter_user_context(raw)
    assert safe["accounts"][0]["label"] == "Investment Account"
    assert safe["account_types"] == ["Investment Account"]
