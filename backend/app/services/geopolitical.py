"""Geopolitical tension signals via GDELT 2.0 Doc API (free, no key).

Returns a tension score per region (0-100) and a global tension index.
Scores derive from GDELT article negative-tone and volume over the lookback
window. High scores flag elevated event risk that macro/sector skills should
incorporate when assessing portfolio exposure.
"""

from __future__ import annotations

import time
from typing import Any

import requests
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.geopolitical")

_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 3600  # 1 hour

_GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_TIMEOUT = 12  # seconds

# GDELT uses FIPS 10-4 country codes (not ISO 3166-2)
_REGION_FIPS: dict[str, list[str]] = {
    "Americas": ["US", "CA", "MX", "BR", "AR", "CH"],  # CH = Chile in FIPS
    "Europe": ["UK", "FR", "GM", "UP", "RS", "IT", "SP", "PL", "SW"],
    "Asia_Pacific": ["CH", "JA", "IN", "KS", "TW", "AS", "SN", "HK"],
    "Middle_East": ["IS", "SA", "IR", "AE", "TU", "EG", "IZ"],
    "Emerging": ["SF", "NI", "ID", "RP", "VM", "TH"],
}
# Note: "CH" appears in both Americas (Chile) and Asia_Pacific (China).
# In practice GDELT sourcecountry for China is "CH" and Chile is rarely
# in geopolitical articles — Asia_Pacific match is more likely when ambiguous.
# First region that matches wins.

_REGION_PRIORITY = ["Europe", "Asia_Pacific", "Middle_East", "Americas", "Emerging"]

# GDELT theme prefix → event category
_THEME_TO_CATEGORY: dict[str, str] = {
    "MILITARY_CONFLICT": "armed_conflict",
    "WB_1795_POLITICAL_STABILITY": "political_instability",
    "ECON_TARIFF": "trade_tensions",
    "ECON_SANCTIONS": "sanctions",
    "ECON_INFLATION": "macro_stress",
    "WB_696_ENERGY": "energy_events",
    "ECON_CURRENCY": "currency_stress",
    "TAX_FNCACT_CENTRAL_BANK": "central_bank",
    "NATURAL_DISASTER": "natural_disaster",
    "ENV_": "environmental",
}

# Category → market sectors/ETFs impacted
_CATEGORY_TO_MARKET: dict[str, list[str]] = {
    "armed_conflict": ["XLE (Energy +)", "ITA (Defense +)", "WEAT (Agriculture +)", "EWZ (EM -)"],
    "trade_tensions": ["QQQ (Tech -)", "XLI (Industrial -)", "EFA (Int'l -)", "DXY (USD +)"],
    "sanctions": ["XLE (Energy +/-)", "XLF (Financials -)", "GLD (Gold +)"],
    "macro_stress": ["TIP (TIPS +)", "GLD (Gold +)", "XLP (Staples +)", "TLT (Bonds +/-)"],
    "central_bank": ["TLT (Long bonds +/-)", "XLF (Financials +/-)", "DXY (USD +/-)"],
    "energy_events": ["XLE (Energy +)", "XOP (Oil&Gas +)", "XLP (Staples -)"],
    "currency_stress": ["GLD (Gold +)", "TLT (Bonds +)", "EFA (Int'l -)"],
    "political_instability": ["GLD (Gold +)", "VIX (+)", "XLU (Utilities +)", "EEM (EM -)"],
    "natural_disaster": ["CAT (Insurance -)", "XLU (Utilities -)"],
    "environmental": ["ICLN (Clean energy +/-)", "XLE (Energy -)"],
}


def _region_of_country(fips_code: str) -> str | None:
    for region in _REGION_PRIORITY:
        if fips_code in _REGION_FIPS.get(region, []):
            return region
    return None


def _parse_tone(tone_str: str) -> tuple[float, float] | None:
    """Parse GDELT tone string → (avg_tone, neg_score). Both floats."""
    if not tone_str:
        return None
    parts = tone_str.split(",")
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), abs(float(parts[2]))
    except ValueError:
        return None


def _fetch_articles(query: str, timespan: str, maxrecords: int = 50) -> list[dict[str, Any]]:
    """Fetch articles from GDELT Doc API. Returns [] on error."""
    try:
        resp = requests.get(
            _GDELT_DOC_API,
            params={
                "query": query,
                "mode": "ArtList",
                "maxrecords": maxrecords,
                "timespan": timespan,
                "format": "json",
            },
            timeout=_GDELT_TIMEOUT,
            headers={"User-Agent": "aifolimizer/1.0"},
        )
        resp.raise_for_status()
        return resp.json().get("articles") or []
    except Exception as e:
        _LOG.warning(f"[geopolitical] GDELT fetch failed: {e}")
        return []


def _articles_to_tension(articles: list[dict]) -> dict[str, Any]:
    """
    Aggregate articles into per-region tension scores and detected categories.
    Returns dict with regions, categories, article metadata.
    """
    region_neg_tones: dict[str, list[float]] = {r: [] for r in _REGION_FIPS}
    region_counts: dict[str, int] = {r: 0 for r in _REGION_FIPS}
    categories: set[str] = set()

    for art in articles:
        country = art.get("sourcecountry", "")
        region = _region_of_country(country) if country else None

        tone_parsed = _parse_tone(art.get("tone", ""))

        if region and tone_parsed:
            region_neg_tones[region].append(tone_parsed[1])
            region_counts[region] += 1

        # Classify themes
        raw_themes = art.get("themes", "") or ""
        for theme in raw_themes.split(";"):
            theme = theme.strip()
            for prefix, cat in _THEME_TO_CATEGORY.items():
                if theme.startswith(prefix):
                    categories.add(cat)
                    break

    # Compute tension scores (0-100)
    # GDELT neg_score: typically 1-5 normal news, 5-15 conflict zones
    # Formula: avg_neg * 5 + volume_bonus (max 20) → clamp 0-100
    region_tension: dict[str, dict] = {}
    for region in _REGION_FIPS:
        negs = region_neg_tones[region]
        count = region_counts[region]
        if negs:
            avg_neg = sum(negs) / len(negs)
            score = min(100, round(avg_neg * 5 + min(count, 10) * 2, 0))
        else:
            score = 0
        region_tension[region] = {
            "tension_score": int(score),
            "article_count": count,
            "level": "high" if score >= 60 else ("moderate" if score >= 30 else "low"),
        }

    return {
        "regions": region_tension,
        "categories": sorted(categories),
        "total_articles": len(articles),
    }


def get_geopolitical_signals(lookback_hours: int = 24) -> dict[str, Any]:
    """
    Geopolitical tension index from GDELT 2.0.

    Queries last lookback_hours of news for conflict/trade/sanction/energy themes.
    Returns:
      global_tension_index (0-100): weighted mean across regions
      level: "high" | "moderate" | "low"
      regions: per-region tension_score, article_count, level
      hot_regions: regions with score >= 60
      categories_detected: event types found (armed_conflict, trade_tensions, ...)
      market_implications: list of ETF/sector impacts from detected categories
      articles_analyzed: article count processed
    Cached 1h.
    """
    lookback_hours = max(6, min(int(lookback_hours), 168))
    cache_key = f"geo_{lookback_hours}"
    entry = _CACHE.get(cache_key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]

    query = (
        "theme:MILITARY_CONFLICT OR theme:ECON_TARIFF OR "
        "theme:ECON_SANCTIONS OR theme:WB_1795_POLITICAL_STABILITY OR "
        "theme:WB_696_ENERGY OR theme:ECON_CURRENCY"
    )
    timespan = f"{lookback_hours}h"
    articles = _fetch_articles(query, timespan=timespan, maxrecords=75)

    if not articles:
        result: dict[str, Any] = {
            "global_tension_index": 0,
            "level": "low",
            "regions": {},
            "hot_regions": [],
            "categories_detected": [],
            "market_implications": [],
            "articles_analyzed": 0,
            "lookback_hours": lookback_hours,
            "data_source": "GDELT 2.0 Doc API",
            "error": "no_data",
        }
        return result

    agg = _articles_to_tension(articles)

    # Global tension = simple mean of non-zero regions (or all if all zero)
    scores = [v["tension_score"] for v in agg["regions"].values()]
    nonzero = [s for s in scores if s > 0]
    global_index = round(sum(nonzero) / len(nonzero), 1) if nonzero else 0.0

    hot_regions = [r for r, d in agg["regions"].items() if d["tension_score"] >= 60]

    # Market implications from detected categories (deduplicated, ordered)
    seen: set[str] = set()
    market_implications: list[str] = []
    for cat in agg["categories"]:
        for implication in _CATEGORY_TO_MARKET.get(cat, []):
            if implication not in seen:
                seen.add(implication)
                market_implications.append(implication)

    result = {
        "global_tension_index": global_index,
        "level": "high" if global_index >= 60 else ("moderate" if global_index >= 30 else "low"),
        "regions": agg["regions"],
        "hot_regions": hot_regions,
        "categories_detected": agg["categories"],
        "market_implications": market_implications,
        "articles_analyzed": agg["total_articles"],
        "lookback_hours": lookback_hours,
        "data_source": "GDELT 2.0 Doc API (free, no key)",
    }
    _CACHE[cache_key] = (result, time.time())
    return result
