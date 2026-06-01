"""Retry-After honoring on a single 429 / 503."""

from __future__ import annotations

import httpx

from app.services import http_helpers


def test_retry_after_header_triggers_one_retry(monkeypatch) -> None:
    calls: list[float] = []

    def fake_sleep(s: float) -> None:
        calls.append(s)

    monkeypatch.setattr(http_helpers.time, "sleep", fake_sleep)

    state = {"n": 0}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(transport_handler)
    client = httpx.Client(transport=transport)

    resp = http_helpers.request_with_retry_after(
        "GET",
        "https://example.invalid/x",
        client=client,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert state["n"] == 2
    assert calls == [2.0]


def test_retry_after_caps_at_30s(monkeypatch) -> None:
    calls: list[float] = []
    monkeypatch.setattr(http_helpers.time, "sleep", lambda s: calls.append(s))
    state = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "600"})
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    http_helpers.request_with_retry_after(
        "GET",
        "https://example.invalid/y",
        client=client,
    )
    assert calls == [30.0]


def test_no_retry_when_status_ok(monkeypatch) -> None:
    calls: list[float] = []
    monkeypatch.setattr(http_helpers.time, "sleep", lambda s: calls.append(s))
    handler = lambda _req: httpx.Response(200, json={"ok": True})  # noqa: E731
    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = http_helpers.request_with_retry_after(
        "GET",
        "https://example.invalid/z",
        client=client,
    )
    assert resp.status_code == 200
    assert calls == []
