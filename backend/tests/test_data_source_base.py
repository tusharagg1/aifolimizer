"""Characterization tests for shared DataSource helpers (base.py).

Locks the behavior the per-adapter copies had before extraction, and proves
fetch_json keeps the redact_secrets security fix (commit 4f3f0d8).
"""

import httpx
import pytest

from app.services.data_sources import base
from app.services.data_sources.base import (
    SourceUnavailable,
    fetch_json,
    pct_normalize,
    to_float,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("None", None),
        ("-", None),
        ("4.5", 4.5),
        (4, 4.0),
        (0.0, 0.0),
        ("garbage", None),
        ([], None),
    ],
)
def test_to_float(raw, expected):
    assert to_float(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.04, 4.0),  # ratio -> percent
        (4.0, 4.0),  # already percent stays
        (1.0, 1.0),  # boundary: >=1 stays
        (None, None),
        ("-", None),
    ],
)
def test_pct_normalize(raw, expected):
    assert pct_normalize(raw) == expected


class _FakeResp:
    def __init__(self, payload, raise_exc=None):
        self._payload = payload
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._payload


def test_fetch_json_success(monkeypatch):
    monkeypatch.setattr(base.httpx, "get", lambda *a, **k: _FakeResp({"close": 1.23}))
    assert fetch_json("http://x", name="t", symbol="AAPL") == {"close": 1.23}


def test_fetch_json_falsy_returns_default(monkeypatch):
    monkeypatch.setattr(base.httpx, "get", lambda *a, **k: _FakeResp(None))
    assert fetch_json("http://x", name="t", symbol="AAPL", default=[]) == []
    assert fetch_json("http://x", name="t", symbol="AAPL") == {}


def test_fetch_json_http_error_wraps_in_source_unavailable(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(base.httpx, "get", boom)
    with pytest.raises(SourceUnavailable) as ei:
        fetch_json("http://x", name="eodhd", symbol="RY.TO")
    assert "eodhd http RY.TO" in str(ei.value)


def test_fetch_json_redacts_apikey_in_error(monkeypatch):
    # Simulate the real leak: httpx error string carries the full URL + apikey.
    def boom(*a, **k):
        raise RuntimeError(
            "Client error '404' for url 'https://api.twelvedata.com/quote?symbol=SHOP&apikey=SECRET123KEY&exchange=TSX'"
        )

    monkeypatch.setattr(base.httpx, "get", boom)
    with pytest.raises(SourceUnavailable) as ei:
        fetch_json("http://x", name="twelve_data", symbol="SHOP.TO")
    msg = str(ei.value)
    assert "SECRET123KEY" not in msg
    assert "apikey=<redacted>" in msg
