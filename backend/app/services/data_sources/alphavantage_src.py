"""Alpha Vantage adapter (free 25 calls/day, key required).

Free tier is very limited so we treat AV as a quality fallback for
fundamentals + earnings only, not as a primary quote source.

Set ALPHA_VANTAGE_KEY in backend/.env.
"""

from __future__ import annotations

import os
import time

from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    SourceUnavailable,
    fetch_json,
    pct_normalize,
    to_float,
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
        data = fetch_json(
            _BASE,
            name="alpha_vantage",
            symbol=symbol,
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": self.api_key},
            timeout=10.0,
            default={},
        )

        if not data or "Symbol" not in data:
            note = data.get("Note") or data.get("Information") or "empty"
            raise SourceUnavailable(f"alpha_vantage: {note}")

        return Fundamentals(
            symbol=symbol,
            pe_ratio=to_float(data.get("TrailingPE")),
            eps=to_float(data.get("EPS")),
            dividend_yield_pct=pct_normalize(data.get("DividendYield")),
            payout_ratio_pct=pct_normalize(data.get("PayoutRatio")),
            market_cap=to_float(data.get("MarketCapitalization")),
            beta=to_float(data.get("Beta")),
            analyst_target=to_float(data.get("AnalystTargetPrice")),
            earnings_date=data.get("LatestQuarter") or None,
            sector=data.get("Sector") or None,
            industry=data.get("Industry") or None,
            institutional_pct=pct_normalize(data.get("PercentInstitutions")),
            short_pct_float=pct_normalize(data.get("ShortPercentFloat")),
            source=self.name,
            as_of=time.time(),
        )
