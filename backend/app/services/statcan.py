"""Statistics Canada WDS API — official Canadian macro (free, no key).

Complements Bank of Canada (rates/FX) with the real-economy series: CPI
all-items (with computed YoY inflation) and the headline unemployment rate.
Public REST endpoint, no authentication.

StatCan sits behind Akamai bot-protection that resets plain-Python TLS
handshakes (JA3 fingerprint block). We fetch via curl_cffi with a browser
impersonation profile, which the WAF accepts. curl_cffi is imported lazily so
the rest of the server runs without it; absence degrades to a clean error.

Docs: https://www.statcan.gc.ca/en/developers/wds
"""

from __future__ import annotations

import time
from typing import Any

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.statcan")

_URL = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
_TIMEOUT = 15.0
_TTL = 12 * 3600  # 12h — StatCan releases monthly
_cache: tuple[dict, float] | None = None


def _fetch(vector_id: int, latest_n: int) -> list[dict]:
    try:
        from curl_cffi import requests as creq
    except ImportError:
        _LOG.warning("[statcan] curl_cffi not installed — pip install curl_cffi")
        return []
    try:
        resp = creq.post(
            _URL,
            json=[{"vectorId": vector_id, "latestN": latest_n}],
            timeout=_TIMEOUT,
            impersonate="chrome",
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        _LOG.warning(f"[statcan] vector {vector_id} fetch failed: {e}")
        return []
    if not payload or payload[0].get("status") != "SUCCESS":
        return []
    return payload[0].get("object", {}).get("vectorDataPoint") or []


def _val(point: dict) -> float | None:
    try:
        return float(point["value"])
    except (KeyError, TypeError, ValueError):
        return None


def statcan_snapshot() -> dict[str, Any]:
    """Canadian CPI (+ YoY inflation) and unemployment rate. Cached 12h."""
    global _cache
    now = time.time()
    if _cache and now - _cache[1] < _TTL:
        return _cache[0]

    out: dict[str, Any] = {"data_source": "Statistics Canada WDS (free, no key)"}

    cpi_points = _fetch(41690973, 13)  # 13 months → YoY
    if cpi_points:
        latest = cpi_points[-1]
        cur = _val(latest)
        out["cpi_all_items"] = {"value": cur, "ref_period": latest.get("refPer")}
        if cur is not None and len(cpi_points) >= 13:
            yr_ago = _val(cpi_points[-13])
            if yr_ago:
                out["cpi_yoy_pct"] = round((cur / yr_ago - 1) * 100, 2)

    unemp_points = _fetch(2062815, 2)
    if unemp_points:
        latest = unemp_points[-1]
        out["unemployment_rate_pct"] = {
            "value": _val(latest),
            "ref_period": latest.get("refPer"),
        }

    _cache = (out, now)
    return out
