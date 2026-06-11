"""Finnhub adapter (free 60 calls/min, key required).

Strong real-time quote + earnings calendar. Solid US coverage, CA equities
via TO suffix. Set FINNHUB_KEY in backend/.env.
"""

from __future__ import annotations

import os
import time

import httpx

from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    PriceBar,
    Quote,
    SourceUnavailable,
    redact_secrets,
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
        try:
            resp = httpx.get(
                f"{_BASE}/quote",
                params={"symbol": self._fh_symbol(symbol), "token": self.api_key},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"finnhub http {symbol}: {redact_secrets(e)}") from e

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
        # Finnhub free plan dropped /stock/candle for non-forex in 2024.
        # Skip until paid tier; let router fall to next source.
        raise SourceUnavailable("finnhub: history requires paid plan")

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        if not self.is_configured():
            raise SourceUnavailable("finnhub: no API key")
        try:
            resp = httpx.get(
                f"{_BASE}/stock/metric",
                params={
                    "symbol": self._fh_symbol(symbol),
                    "metric": "all",
                    "token": self.api_key,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"finnhub http {symbol}: {redact_secrets(e)}") from e

        m = data.get("metric") or {}
        if not m:
            raise SourceUnavailable(f"finnhub: empty metrics for {symbol}")
        return Fundamentals(
            symbol=symbol,
            pe_ratio=_f(m.get("peTTM") or m.get("peNormalizedAnnual")),
            eps=_f(m.get("epsTTM") or m.get("epsAnnual")),
            dividend_yield_pct=_f(m.get("dividendYieldIndicatedAnnual")),
            payout_ratio_pct=_f(m.get("payoutRatioTTM")),
            market_cap=_f(m.get("marketCapitalization")),
            beta=_f(m.get("beta")),
            analyst_target=None,
            earnings_date=None,
            sector=None,
            industry=None,
            institutional_pct=None,
            short_pct_float=None,
            source=self.name,
            as_of=time.time(),
        )


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None
