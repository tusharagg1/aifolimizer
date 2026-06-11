"""News provider fetchers route through base.fetch_json -> credential redaction.

Closes the key-leak gap where news._fetch_finnhub/_fetch_eodhd hand-rolled httpx
and leaked the apikey URL into source_stats / logs (same class as commit 4f3f0d8,
in a file the adapter fix did not cover).
"""

import pytest

from app.services import news
from app.services.data_sources import base
from app.services.data_sources.base import SourceUnavailable


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_finnhub_news_redacts_key_on_error(monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", "SECRETNEWSKEY")

    def boom(*a, **k):
        raise RuntimeError(
            "Client error '401' for url 'https://finnhub.io/api/v1/company-news?symbol=AAPL&token=SECRETNEWSKEY'"
        )

    monkeypatch.setattr(base.httpx, "get", boom)
    with pytest.raises(SourceUnavailable) as ei:
        news._fetch_finnhub("AAPL")
    msg = str(ei.value)
    assert "SECRETNEWSKEY" not in msg
    assert "token=<redacted>" in msg
    assert "news:finnhub" in msg


def test_finnhub_news_parses_rows(monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", "k")
    rows = [{"headline": "Apple up", "source": "Reuters", "datetime": 1700000000, "url": "http://x"}]
    monkeypatch.setattr(base.httpx, "get", lambda *a, **k: _FakeResp(rows))
    out = news._fetch_finnhub("AAPL")
    assert len(out) == 1
    assert out[0]["title"] == "Apple up"
    assert out[0]["source"] == "finnhub"


def test_finnhub_news_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    assert news._fetch_finnhub("AAPL") == []


def test_eodhd_news_redacts_key_on_error(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "EODSECRET")

    def boom(*a, **k):
        raise RuntimeError("Server error for url 'https://eodhd.com/api/news?s=AAPL.US&api_token=EODSECRET&fmt=json'")

    monkeypatch.setattr(base.httpx, "get", boom)
    with pytest.raises(SourceUnavailable) as ei:
        news._fetch_eodhd("AAPL")
    msg = str(ei.value)
    assert "EODSECRET" not in msg
    assert "api_token=<redacted>" in msg
