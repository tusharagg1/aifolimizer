"""Alpha Vantage adapter (free 25 calls/day, key required).

Free tier is very limited so we treat AV as a quality fallback for
fundamentals + earnings only, not as a primary quote source.

Set ALPHA_VANTAGE_KEY in backend/.env.
"""

from __future__ import annotations

import os
import time

import httpx

from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    SourceUnavailable,
    redact_secrets,
)

_BASE = "https://www.alphavantage.co/query"


class AlphaVantageSource(DataSource):
    name = "alpha_vantage"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        if not self.is_configured():
            raise SourceUnavailable("alpha_vantage: no API key")
        try:
            resp = httpx.get(
                _BASE,
                params={"function": "OVERVIEW", "symbol": symbol, "apikey": self.api_key},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"alpha_vantage http {symbol}: {redact_secrets(e)}") from e

        if not data or "Symbol" not in data:
            note = data.get("Note") or data.get("Information") or "empty"
            raise SourceUnavailable(f"alpha_vantage: {note}")

        return Fundamentals(
            symbol=symbol,
            pe_ratio=_f(data.get("TrailingPE")),
            eps=_f(data.get("EPS")),
            dividend_yield_pct=_pct100(data.get("DividendYield")),
            payout_ratio_pct=_pct100(data.get("PayoutRatio")),
            market_cap=_f(data.get("MarketCapitalization")),
            beta=_f(data.get("Beta")),
            analyst_target=_f(data.get("AnalystTargetPrice")),
            earnings_date=data.get("LatestQuarter") or None,
            sector=data.get("Sector") or None,
            industry=data.get("Industry") or None,
            institutional_pct=_pct100(data.get("PercentInstitutions")),
            short_pct_float=_pct100(data.get("ShortPercentFloat")),
            source=self.name,
            as_of=time.time(),
        )


def _f(x) -> float | None:
    if x is None or x == "None" or x == "-":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct100(x) -> float | None:
    f = _f(x)
    if f is None:
        return None
    return f * 100 if f < 1 else f
