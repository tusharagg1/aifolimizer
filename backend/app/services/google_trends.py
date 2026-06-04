"""Google Trends search-interest via pytrends (free, no key, unofficial).

Search interest is a retail-demand proxy: a spike in queries for a ticker or
theme often precedes or confirms crowding. Use alongside positioning signals.

pytrends scrapes Google's unofficial endpoint — it is rate-limited (HTTP 429)
and may break without notice. The dependency is imported lazily so the rest of
the server runs even if pytrends is absent; all failures degrade gracefully.
"""

from __future__ import annotations

import time
from typing import Any

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.google_trends")

_TTL = 6 * 3600  # 6h — search interest is slow-moving
_cache: dict[str, tuple[dict, float]] = {}


def trends_interest(keywords: list[str], timeframe: str = "today 3-m") -> dict[str, Any]:
    """Relative search interest (0-100) + 4-week change per keyword. Cached 6h."""
    kws = [k.strip() for k in keywords if k.strip()][:5]
    if not kws:
        return {"error": "no_keywords", "data_source": "google_trends"}

    ck = f"{','.join(sorted(kws))}|{timeframe}"
    hit = _cache.get(ck)
    now = time.time()
    if hit and now - hit[1] < _TTL:
        return hit[0]

    try:
        from pytrends.request import TrendReq
    except ImportError:
        return {"error": "pytrends_not_installed",
                "note": "pip install pytrends", "data_source": "google_trends"}

    try:
        pt = TrendReq(hl="en-US", tz=300)
        pt.build_payload(kws, timeframe=timeframe)
        df = pt.interest_over_time()
    except Exception as e:
        _LOG.warning(f"[google_trends] fetch failed: {e}")
        return {"error": "fetch_failed", "detail": str(e)[:120], "data_source": "google_trends"}

    if df is None or df.empty:
        return {"error": "no_data", "data_source": "google_trends"}

    out: dict[str, Any] = {"data_source": "Google Trends via pytrends (free, no key)",
                           "timeframe": timeframe, "keywords": {}}
    for kw in kws:
        if kw not in df.columns:
            continue
        col = df[kw].dropna()
        if col.empty:
            continue
        latest = int(col.iloc[-1])
        prior = int(col.iloc[-5]) if len(col) >= 5 else int(col.iloc[0])
        out["keywords"][kw] = {
            "current_interest": latest,
            "change_4w": latest - prior,
            "peak": int(col.max()),
            "trend": "rising" if latest > prior else ("falling" if latest < prior else "flat"),
        }

    _cache[ck] = (out, now)
    return out
