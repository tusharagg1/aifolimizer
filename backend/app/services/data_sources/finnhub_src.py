"""Finnhub adapter (free 60 calls/min, key required).

Strong real-time quote + earnings calendar. Solid US coverage, CA equities
via TO suffix. Set FINNHUB_KEY in backend/.env.
"""

from __future__ import annotations

import os
import time

from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    PriceBar,
    Quote,
    SourceUnavailable,
    fetch_json,
    to_float,
)

_BASE = "https://finnhub.io/api/v1"


class FinnhubSource(DataSource):
    name = "finnhub"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FINNHUB_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _fh_symbol(self, symbol: str) -> str:
        s = symbol.strip().upper()
        if s.endswith(".TO"):
            return s  # finnhub accepts .TO
        return s

    def get_quote(self, symbol: str) -> Quote:
        if not self.is_configured():
            raise SourceUnavailable("finnhub: no API key")
        data = fetch_json(
            f"{_BASE}/quote",
            name="finnhub",
            symbol=symbol,
            params={"symbol": self._fh_symbol(symbol), "token": self.api_key},
            timeout=10.0,
            default={},
        )

        price = float(data.get("c") or 0.0)
        prev = float(data.get("pc") or 0.0)
        if price <= 0:
            raise SourceUnavailable(f"finnhub: zero price for {symbol}")
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=prev,
            currency=None,
            day_change_pct=((price - prev) / prev * 100) if prev else None,
            source=self.name,
            as_of=time.time(),
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        if not self.is_configured():
            raise SourceUnavailable("finnhub: no API key")
        if interval != "1d":
            raise SourceUnavailable("finnhub free: daily only")
        # /stock/candle is not available for non-forex here; fall through to the next source.
        raise SourceUnavailable("finnhub: history unavailable")

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        if not self.is_configured():
            raise SourceUnavailable("finnhub: no API key")
        data = fetch_json(
            f"{_BASE}/stock/metric",
            name="finnhub",
            symbol=symbol,
            params={
                "symbol": self._fh_symbol(symbol),
                "metric": "all",
                "token": self.api_key,
            },
            timeout=10.0,
            default={},
        )

        m = data.get("metric") or {}
        if not m:
            raise SourceUnavailable(f"finnhub: empty metrics for {symbol}")
        return Fundamentals(
            symbol=symbol,
            pe_ratio=to_float(m.get("peTTM") or m.get("peNormalizedAnnual")),
            eps=to_float(m.get("epsTTM") or m.get("epsAnnual")),
            dividend_yield_pct=to_float(m.get("dividendYieldIndicatedAnnual")),
            payout_ratio_pct=to_float(m.get("payoutRatioTTM")),
            market_cap=to_float(m.get("marketCapitalization")),
            beta=to_float(m.get("beta")),
            analyst_target=None,
            earnings_date=None,
            sector=None,
            industry=None,
            institutional_pct=None,
            short_pct_float=None,
            source=self.name,
            as_of=time.time(),
        )
