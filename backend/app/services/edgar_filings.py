"""SEC EDGAR recent filings via the submissions API (free, no key).

Surfaces material filings (8-K, 10-K, 10-Q, proxy, 13D/G, S-1, foreign 6-K/20-F)
with dates and direct document links — an event-detection feed for catalysts
the price/fundamentals tools don't flag. Reuses the ticker→CIK map already
loaded by fundamentals.py. US-listed only (EDGAR has no Canadian filings).

Docs: https://www.sec.gov/edgar/sec-api-documentation
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.security import get_logger
from app.services import fundamentals

_LOG = get_logger("aifolimizer.services.edgar_filings")

_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_HDR = {"User-Agent": "aifolimizer/1.0 (open-source portfolio analytics)"}
_TIMEOUT = 20.0
_TTL = 6 * 3600  # 6h
_cache: dict[str, tuple[dict, float]] = {}

# Material forms worth flagging by default (drops routine Form 4 / 144 noise).
_MATERIAL = {
    "8-K",
    "10-K",
    "10-Q",
    "6-K",
    "20-F",
    "40-F",
    "S-1",
    "DEF 14A",
    "SC 13D",
    "SC 13G",
    "SC 13D/A",
    "SC 13G/A",
}


def recent_filings(ticker: str, forms: list[str] | None = None, limit: int = 15) -> dict[str, Any]:
    """Recent material SEC filings with dates + document links. Cached 6h."""
    sym = ticker.strip().upper()
    limit = max(1, min(int(limit), 50))
    ck = f"{sym}_{','.join(sorted(forms)) if forms else 'material'}_{limit}"
    hit = _cache.get(ck)
    now = time.time()
    if hit and now - hit[1] < _TTL:
        return hit[0]

    cik = fundamentals._load_cik_map().get(sym)
    if not cik:
        return {"error": "no_cik", "ticker": sym, "note": "US-listed only (EDGAR has no Canadian filings)."}

    try:
        resp = httpx.get(_SUBMISSIONS.format(cik=cik), headers=_HDR, timeout=_TIMEOUT)
        resp.raise_for_status()
        recent = resp.json().get("filings", {}).get("recent", {})
    except Exception as e:
        _LOG.warning(f"[edgar_filings] {sym}: {e}")
        return {"error": "fetch_failed", "ticker": sym}

    form_list = recent.get("form") or []
    wanted = set(forms) if forms else _MATERIAL
    cik_int = int(cik)
    out: list[dict] = []
    for i, form in enumerate(form_list):
        if form not in wanted:
            continue
        acc = (recent.get("accessionNumber") or [""] * (i + 1))[i]
        doc = (recent.get("primaryDocument") or [""] * (i + 1))[i]
        acc_nodash = acc.replace("-", "")
        out.append(
            {
                "form": form,
                "filed": (recent.get("filingDate") or [None] * (i + 1))[i],
                "report_period": (recent.get("reportDate") or [None] * (i + 1))[i],
                "description": (recent.get("primaryDocDescription") or [None] * (i + 1))[i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}",
            }
        )
        if len(out) >= limit:
            break

    result = {
        "ticker": sym,
        "cik": cik,
        "filing_count": len(out),
        "filings": out,
        "data_source": "SEC EDGAR submissions (free, no key)",
    }
    _cache[ck] = (result, now)
    return result
