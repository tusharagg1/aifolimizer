"""Multi-source news headlines - asset-aware fallback chain.

Mirrors data_router's design for the news layer, which used to be a single
flaky yfinance scrape:

  - Per-asset source priority (see `_news_chain`). First source returning
    a NON-EMPTY result wins; sources are tried in quality/quota order.
  - Cross-process L2 cache via data_cache (SQLite). The old in-process dict
    meant each MCP/cron process re-hit Yahoo independently - 3-4x the load
    and rate-limiting. Now they share one 30-min cache.
  - Per-source circuit breaker (shared with data_router) so a rate-limited
    provider is skipped instead of hammered.
  - Every call logged to source_stats so source_drift can demote a
    chronically-failing news provider, same as price sources.

Provider notes:
  - finnhub  : free 60/min, reliable, real-time US company-news. Primary US.
  - yfinance : free but flaky/laggy Yahoo scrape. Best TSX + crypto coverage.
  - eodhd    : strong intl/TSX, but free tier is 20 calls/day SHARED with
               price/fundamentals quota - so it is always LAST resort.

Normalized article shape: {title, publisher, published, url, source}.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import yfinance as yf

from app.security import get_logger
from app.services import data_cache as cache
from app.services.data_sources.base import fetch_json, redact_secrets
from app.services.data_sources.circuit_breaker import default_breaker
from app.services.data_sources.symbol_classifier import classify_asset

_LOG = get_logger("aifolimizer.services.news")

_NEWS_TTL = 1800  # 30 minutes
_HTTP_TIMEOUT = 10.0
# Store up to 50 so headline-velocity (positioning) has a real 30-day window;
# display callers slice to their own small limit at read time.
_MAX_ARTICLES = 50

_breaker = default_breaker()


def _fmt_epoch(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts) if ts else ""


def _to_ts(value) -> float | None:
    """Best-effort epoch seconds from an ISO string or numeric timestamp."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# ── per-source fetchers (raise on transport failure; [] == no news) ──────────


def _fetch_yfinance(symbol: str) -> list[dict]:
    articles = yf.Ticker(symbol).news or []
    out: list[dict] = []
    for a in articles:
        content = a.get("content", {})
        is_new = isinstance(content, dict)
        title = content.get("title") if is_new else a.get("title", "")
        publisher = content.get("provider", {}).get("displayName", "") if is_new else a.get("publisher", "")
        published = content.get("pubDate", "") if is_new else _fmt_epoch(a.get("providerPublishTime", ""))
        url = content.get("canonicalUrl", {}).get("url", "") if is_new else a.get("link", "")
        if title:
            ts = a.get("providerPublishTime")
            out.append(
                {
                    "title": title,
                    "publisher": publisher,
                    "published": published,
                    "published_ts": _to_ts(ts) if ts else _to_ts(published),
                    "url": url,
                    "source": "yfinance",
                }
            )
    return out


def _fetch_finnhub(symbol: str) -> list[dict]:
    key = os.environ.get("FINNHUB_KEY", "").strip()
    if not key:
        return []
    sym = symbol.strip().upper()
    today = datetime.utcnow().date()
    frm = (today - timedelta(days=30)).isoformat()
    rows = fetch_json(
        "https://finnhub.io/api/v1/company-news",
        name="news:finnhub",
        symbol=sym,
        params={"symbol": sym, "from": frm, "to": today.isoformat(), "token": key},
        timeout=_HTTP_TIMEOUT,
        default=[],
    )
    out: list[dict] = []
    for r in rows:
        title = r.get("headline", "")
        if title:
            dt = r.get("datetime", "")
            out.append(
                {
                    "title": title,
                    "publisher": r.get("source", ""),
                    "published": _fmt_epoch(dt),
                    "published_ts": _to_ts(dt),
                    "url": r.get("url", ""),
                    "source": "finnhub",
                }
            )
    return out


def _eod_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.US"


def _fetch_eodhd(symbol: str) -> list[dict]:
    key = os.environ.get("EODHD_KEY", "").strip()
    if not key:
        return []
    rows = fetch_json(
        "https://eodhd.com/api/news",
        name="news:eodhd",
        symbol=_eod_symbol(symbol),
        params={"s": _eod_symbol(symbol), "api_token": key, "limit": _MAX_ARTICLES, "fmt": "json"},
        timeout=_HTTP_TIMEOUT,
        default=[],
    )
    out: list[dict] = []
    for r in rows:
        title = r.get("title", "")
        if title:
            date_raw = r.get("date") or ""
            out.append(
                {
                    "title": title,
                    "publisher": (r.get("source") or "EODHD"),
                    "published": str(date_raw)[:16].replace("T", " "),
                    "published_ts": _to_ts(date_raw),
                    "url": r.get("link", ""),
                    "source": "eodhd",
                }
            )
    return out


_FETCHERS = {
    "yfinance": _fetch_yfinance,
    "finnhub": _fetch_finnhub,
    "eodhd": _fetch_eodhd,
}


def _news_chain(symbol: str) -> list[str]:
    """Asset-aware source priority. First NON-EMPTY result wins."""
    ac = classify_asset(symbol).asset_class
    if ac == "us_equity":
        return ["finnhub", "yfinance", "eodhd"]
    if ac == "ca_equity":
        return ["yfinance", "finnhub", "eodhd"]
    if ac == "crypto":
        return ["yfinance", "finnhub"]
    if ac in ("uk_equity", "eu_equity"):
        return ["yfinance", "eodhd"]
    # index / fx / unknown - only yfinance carries general headlines
    return ["yfinance"]


def _fetch_chain(symbol: str) -> list[dict]:
    """L2-cache-first, then live fetch down the chain. First non-empty wins."""
    chain = _news_chain(symbol)

    # Cache-first: any fresh per-source result short-circuits (no network).
    for source in chain:
        cached = cache.get_news(symbol, source, _NEWS_TTL)
        if cached:
            return cached

    errors: list[str] = []
    for source in chain:
        if _breaker.is_open(source):
            continue
        fn = _FETCHERS[source]
        start = time.perf_counter()
        try:
            articles = fn(symbol)
            latency = (time.perf_counter() - start) * 1000
            cache.log_source_call(f"news:{source}", True, latency, None)
            _breaker.record(source, ok=True)
            if articles:
                trimmed = articles[:_MAX_ARTICLES]
                cache.put_news(symbol, source, trimmed)
                return trimmed
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            safe_err = redact_secrets(e)
            cache.log_source_call(f"news:{source}", False, latency, safe_err[:200])
            _breaker.record(source, ok=False)
            errors.append(f"{source}: {safe_err}")

    if errors:
        _LOG.warning("[news] all sources failed for %s: %s", symbol, "; ".join(errors))
    return []


def get_news(symbols: list[str]) -> dict[str, list]:
    return {sym: _fetch_chain(sym) for sym in symbols}


def recent_headlines(symbol: str) -> list[dict]:
    """Resilient multi-source headlines for one symbol (up to ~50, 30-day
    window). Each article carries `published_ts` (epoch). Used by positioning
    for headline-velocity instead of the old single-source yfinance scrape.
    """
    return _fetch_chain(symbol)
