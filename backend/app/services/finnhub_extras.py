"""Finnhub alternative-data tools (free tier, reuses FINNHUB_KEY).

Extends the existing Finnhub price/fundamentals adapter with the free-tier
endpoints not surfaced elsewhere:
  - company news + crude bull/bear headline tally (/company-news)
  - insider sentiment MSPR trend (/stock/insider-sentiment)
  - economic calendar (/calendar/economic) — PREMIUM on most plans; the tool
    degrades gracefully to {"error": "premium_endpoint"} on 401/403.

All public market data — no PII.
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from typing import Any

import httpx

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.finnhub_extras")

_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 12.0
_TTL = 1800  # 30m
_cache: dict[str, tuple[dict, float]] = {}

_BULL = (
    "beat",
    "surge",
    "soar",
    "upgrade",
    "record",
    "rally",
    "jump",
    "raise",
    "outperform",
    "buy",
    "strong",
    "growth",
    "profit",
    "gain",
    "top",
)
_BEAR = (
    "miss",
    "plunge",
    "downgrade",
    "cut",
    "lawsuit",
    "probe",
    "fall",
    "drop",
    "warn",
    "slump",
    "loss",
    "sell",
    "weak",
    "decline",
    "fraud",
    "recall",
)


def _key() -> str:
    return os.environ.get("FINNHUB_KEY", "").strip()


def _cached(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < _TTL:
        return entry[0]
    return None


def _store(key: str, value: dict) -> dict:
    _cache[key] = (value, time.time())
    return value


def _get(path: str, params: dict) -> Any:
    """GET finnhub endpoint. Raises on premium gate (401/403) via marker dict."""
    params = {**params, "token": _key()}
    resp = httpx.get(f"{_BASE}/{path}", params=params, timeout=_TIMEOUT)
    if resp.status_code in (401, 403):
        raise _Premium()
    resp.raise_for_status()
    return resp.json()


class _Premium(Exception):
    pass


def _classify(text: str) -> int:
    t = text.lower()
    return sum(w in t for w in _BULL) - sum(w in t for w in _BEAR)


def finnhub_news(ticker: str, days: int = 7) -> dict[str, Any]:
    """Company news + crude bull/bear headline tally over the window. Cached 30m."""
    if not _key():
        return {"error": "no_api_key", "data_source": "finnhub"}
    days = max(1, min(int(days), 30))
    ck = f"news_{ticker}_{days}"
    if (hit := _cached(ck)) is not None:
        return hit

    today = _dt.date.today()
    frm = (today - _dt.timedelta(days=days)).isoformat()
    sym = ticker.strip().upper()
    try:
        rows = _get("company-news", {"symbol": sym, "from": frm, "to": today.isoformat()}) or []
    except _Premium:
        return {"error": "premium_endpoint", "data_source": "finnhub"}
    except Exception as e:
        _LOG.warning(f"[finnhub_extras] news {sym}: {e}")
        return {"error": "fetch_failed", "data_source": "finnhub"}

    bull = bear = 0
    headlines = []
    for r in rows[:40]:
        head = r.get("headline", "")
        score = _classify(head + " " + (r.get("summary") or ""))
        if score > 0:
            bull += 1
        elif score < 0:
            bear += 1
        if len(headlines) < 10:
            headlines.append(
                {
                    "headline": head,
                    "source": r.get("source"),
                    "url": r.get("url"),
                    "datetime": r.get("datetime"),
                }
            )

    total = bull + bear
    net = round((bull - bear) / total * 100, 1) if total else 0.0
    return _store(
        ck,
        {
            "ticker": sym,
            "article_count": len(rows),
            "bull_headlines": bull,
            "bear_headlines": bear,
            "net_sentiment": net,  # -100 (all bear) .. +100 (all bull)
            "signal": "bullish" if net >= 25 else ("bearish" if net <= -25 else "neutral"),
            "sample_headlines": headlines,
            "data_source": "Finnhub company-news (free)",
        },
    )


def finnhub_insider_sentiment(ticker: str) -> dict[str, Any]:
    """Insider sentiment MSPR (monthly share-purchase ratio) trend. Cached 30m."""
    if not _key():
        return {"error": "no_api_key", "data_source": "finnhub"}
    sym = ticker.strip().upper()
    ck = f"insider_{sym}"
    if (hit := _cached(ck)) is not None:
        return hit

    today = _dt.date.today()
    frm = (today - _dt.timedelta(days=180)).isoformat()
    try:
        data = _get("stock/insider-sentiment", {"symbol": sym, "from": frm, "to": today.isoformat()})
    except _Premium:
        return {"error": "premium_endpoint", "data_source": "finnhub"}
    except Exception as e:
        _LOG.warning(f"[finnhub_extras] insider {sym}: {e}")
        return {"error": "fetch_failed", "data_source": "finnhub"}

    points = (data or {}).get("data") or []
    if not points:
        return _store(
            ck,
            {"ticker": sym, "months": [], "net_signal": "no_data", "data_source": "Finnhub insider-sentiment (free)"},
        )

    msprs = [p.get("mspr") for p in points if p.get("mspr") is not None]
    avg = round(sum(msprs) / len(msprs), 1) if msprs else None
    net = "no_data"
    if avg is not None:
        net = "bullish" if avg > 20 else ("bearish" if avg < -20 else "neutral")
    return _store(
        ck,
        {
            "ticker": sym,
            "avg_mspr": avg,  # -100..100; positive = net insider buying pressure
            "net_signal": net,
            "months": points[-6:],
            "data_source": "Finnhub insider-sentiment (free)",
        },
    )


def finnhub_economic_calendar() -> dict[str, Any]:
    """Upcoming macro releases. PREMIUM on most plans → degrades gracefully."""
    if not _key():
        return {"error": "no_api_key", "data_source": "finnhub"}
    ck = "econ_cal"
    if (hit := _cached(ck)) is not None:
        return hit
    try:
        data = _get("calendar/economic", {})
    except _Premium:
        return {
            "error": "premium_endpoint",
            "note": "Finnhub economic calendar requires a paid plan.",
            "data_source": "finnhub",
        }
    except Exception as e:
        _LOG.warning(f"[finnhub_extras] econ calendar: {e}")
        return {"error": "fetch_failed", "data_source": "finnhub"}

    events = (data or {}).get("economicCalendar") or []
    return _store(
        ck,
        {
            "event_count": len(events),
            "events": events[:50],
            "data_source": "Finnhub economic-calendar",
        },
    )
