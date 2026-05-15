import time
from datetime import datetime
import yfinance as yf

_cache: dict[str, tuple[list, float]] = {}
_CACHE_TTL = 1800  # 30 minutes


def _cached(symbol: str, fetch_fn) -> list:
    entry = _cache.get(symbol)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    result = fetch_fn(symbol)
    _cache[symbol] = (result, time.time())
    return result


def _fmt_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _fetch_one(symbol: str) -> list:
    try:
        articles = yf.Ticker(symbol).news or []
        out = []
        for a in articles[:5]:
            content = a.get("content", {})
            title = (
                content.get("title")
                if isinstance(content, dict)
                else a.get("title", "")
            )
            publisher = (
                content.get("provider", {}).get("displayName", "")
                if isinstance(content, dict)
                else a.get("publisher", "")
            )
            pub_date = (
                content.get("pubDate", "")
                if isinstance(content, dict)
                else _fmt_date(a.get("providerPublishTime", ""))
            )
            url = (
                content.get("canonicalUrl", {}).get("url", "")
                if isinstance(content, dict)
                else a.get("link", "")
            )
            if title:
                out.append({
                    "title": title,
                    "publisher": publisher,
                    "published": pub_date,
                    "url": url,
                })
        return out
    except Exception as e:
        print(f"[news] {symbol}: {e}")
        return []


def get_news(symbols: list[str]) -> dict[str, list]:
    return {sym: _cached(sym, _fetch_one) for sym in symbols}
