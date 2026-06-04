"""Bank of Canada Valet API — official CAD macro data (free, no key).

Fills the Canadian gap left by FRED (US-centric): BoC policy rate, USD/CAD,
Government of Canada benchmark bond yields (2/5/10y) and the 10y-2y curve
slope. Public endpoint, no authentication.

Docs: https://www.bankofcanada.ca/valet/docs
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.boc_valet")

_BASE = "https://www.bankofcanada.ca/valet/observations"
_TIMEOUT = 12.0
_TTL = 12 * 3600  # 12h — BoC publishes at most daily
_cache: tuple[dict, float] | None = None

# series id -> (human label, kind)
_SERIES: dict[str, tuple[str, str]] = {
    "V39079": ("policy_rate_pct", "rate"),  # Target for the overnight rate
    "FXUSDCAD": ("usd_cad", "fx"),
    "BD.CDN.2YR.DQ.YLD": ("goc_2y_yield_pct", "rate"),
    "BD.CDN.5YR.DQ.YLD": ("goc_5y_yield_pct", "rate"),
    "BD.CDN.10YR.DQ.YLD": ("goc_10y_yield_pct", "rate"),
}


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _latest_per_series(observations: list[dict]) -> dict[str, tuple[str, float]]:
    """Walk observations newest-last; keep last non-null {date, value} per series."""
    out: dict[str, tuple[str, float]] = {}
    for row in observations:
        date = row.get("d", "")
        for sid in _SERIES:
            cell = row.get(sid)
            if isinstance(cell, dict):
                v = _f(cell.get("v"))
                if v is not None:
                    out[sid] = (date, v)
    return out


def boc_snapshot() -> dict[str, Any]:
    """BoC policy rate, USD/CAD, GoC 2/5/10y yields + 10y-2y slope. Cached 12h."""
    global _cache
    now = time.time()
    if _cache and now - _cache[1] < _TTL:
        return _cache[0]

    ids = ",".join(_SERIES.keys())
    url = f"{_BASE}/{ids}/json"
    try:
        resp = httpx.get(
            url,
            params={"recent": 10},
            timeout=_TIMEOUT,
            headers={"User-Agent": "aifolimizer/1.0"},
        )
        resp.raise_for_status()
        observations = resp.json().get("observations") or []
    except Exception as e:
        _LOG.warning(f"[boc_valet] fetch failed: {e}")
        return {"error": "fetch_failed", "data_source": "Bank of Canada Valet"}

    latest = _latest_per_series(observations)
    out: dict[str, Any] = {"data_source": "Bank of Canada Valet (free, no key)"}
    for sid, (label, _kind) in _SERIES.items():
        if sid in latest:
            date, val = latest[sid]
            out[label] = {"value": val, "date": date, "series_id": sid}

    two = out.get("goc_2y_yield_pct", {}).get("value")
    ten = out.get("goc_10y_yield_pct", {}).get("value")
    if two is not None and ten is not None:
        slope = round(ten - two, 3)
        out["curve_10y_2y_bps"] = round(slope * 100, 1)
        out["curve_signal"] = "inverted" if slope < 0 else "normal"

    _cache = (out, now)
    return out
