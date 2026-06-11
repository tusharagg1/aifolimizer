"""Tests for the consolidated Telegram notifier (notifications/telegram.py)."""

from app.core.config import settings
from app.services.notifications import telegram


def test_send_formats_and_posts(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return None

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    telegram.send("TOKEN", "CHAT", "Title here", "Body here", severity="high", rule="rsi_oversold")

    assert "/botTOKEN/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == "CHAT"
    assert captured["json"]["parse_mode"] == "HTML"
    text = captured["json"]["text"]
    assert "🔴" in text          # high severity tag
    assert "⬇️" in text          # rsi_oversold emoji
    assert "<b>" in text and "Title here" in text
    assert "Body here" in text


def test_send_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram.httpx, "post", boom)
    # Must not raise — one failed push can't block the caller.
    telegram.send("T", "C", "t", "b")


def test_push_noop_when_creds_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    telegram.push("t", "b")
    assert calls == []  # no send attempted


def test_push_sends_when_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: calls.append(k.get("json")))
    monkeypatch.setattr(settings, "telegram_bot_token", "TOK")
    monkeypatch.setattr(settings, "telegram_chat_id", "CID")
    telegram.push("hello", "world", severity="low")
    assert len(calls) == 1
    assert calls[0]["chat_id"] == "CID"
    assert "🟢" in calls[0]["text"]  # low severity tag
