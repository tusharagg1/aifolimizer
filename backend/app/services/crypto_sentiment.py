"""Crypto Fear & Greed Index via alternative.me (free, no key).

Aggregate market sentiment 0-100 (0 = extreme fear, 100 = extreme greed)
derived from volatility, momentum, social, dominance and trends. Pairs with
CoinGecko price data for the crypto sleeve: extreme fear historically marks
accumulation zones, extreme greed marks froth.

Docs: https://alternative.me/crypto/fear-and-greed-index/
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.crypto_sentiment")

_URL = "https://api.alternative.me/fng/"
_TIMEOUT = 10.0
_TTL = 3600  # 1h — index updates once daily
_cache: dict[int, tuple[dict, float]] = {}


def _avg(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def crypto_fear_greed(limit: int = 30) -> dict[str, Any]:
    """Crypto Fear & Greed Index: current value + 7d/30d averages. Cached 1h."""
    limit = max(1, min(int(limit), 365))
    cached = _cache.get(limit)
    now = time.time()
    if cached and now - cached[1] < _TTL:
        return cached[0]

    try:
        resp = httpx.get(
            _URL,
            params={"limit": limit, "format": "json"},
            timeout=_TIMEOUT,
            headers={"User-Agent": "aifolimizer/1.0"},
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception as e:
        _LOG.warning(f"[crypto_sentiment] fetch failed: {e}")
        return {"error": "fetch_failed", "data_source": "alternative.me Fear & Greed"}

    if not data:
        return {"error": "no_data", "data_source": "alternative.me Fear & Greed"}

    series = []
    for d in data:
        try:
            series.append(
                {
                    "value": int(d["value"]),
                    "classification": d.get("value_classification"),
                    "timestamp": int(d["timestamp"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue

    if not series:
        return {"error": "parse_failed", "data_source": "alternative.me Fear & Greed"}

    current = series[0]
    vals = [s["value"] for s in series]
    out = {
        "current_value": current["value"],
        "classification": current["classification"],
        "avg_7d": _avg(vals[:7]),
        "avg_30d": _avg(vals[:30]),
        "history": series,
        "data_source": "alternative.me Crypto Fear & Greed (free, no key)",
    }
    _cache[limit] = (out, now)
    return out
