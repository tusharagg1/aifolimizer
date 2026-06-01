"""EODHD adapter — strong Canadian/TSX coverage, key required.

Free tier: 20 calls/day (limited but valuable for TSX where most other
free providers are weak). Paid plans unlock 100k+/day.

Set EODHD_KEY in backend/.env. Without key, is_configured() returns
False so the router skips it.

Symbol form: AAPL.US, SHOP.TO, RY.TO, ASML.AS, BARC.LSE
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
)

_BASE = "https://eodhd.com/api"


def _eod_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if "." in s:
        # already has exchange suffix (e.g. SHOP.TO, ASML.AS)
        return s
    return f"{s}.US"


_SUFFIX_CCY = {
    "US": "USD",
    "TO": "CAD",
    "V": "CAD",
    "CN": "CAD",
    "NEO": "CAD",
    "L": "GBP",
    "LSE": "GBP",
    "DE": "EUR",
    "PA": "EUR",
    "MI": "EUR",
    "AS": "EUR",
    "MC": "EUR",
    "BR": "EUR",
    "LS": "EUR",
    "VI": "EUR",
    "HE": "EUR",
    "SW": "CHF",
    "CO": "DKK",
    "OL": "NOK",
    "ST": "SEK",
}


def _ccy_from_suffix(eod_sym: str) -> str | None:
    if "." not in eod_sym:
        return "USD"
    suf = eod_sym.rsplit(".", 1)[-1].upper()
    return _SUFFIX_CCY.get(suf)


class EODHDSource(DataSource):
    name = "eodhd"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("EODHD_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_quote(self, symbol: str) -> Quote:
        if not self.is_configured():
            raise SourceUnavailable("eodhd: no key")
        sym = _eod_symbol(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/real-time/{sym}",
                params={"api_token": self.api_key, "fmt": "json"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"eodhd http {symbol}: {e}") from e

        if not isinstance(data, dict) or "close" not in data:
            raise SourceUnavailable(f"eodhd: empty quote for {symbol}")
        try:
            price = float(data.get("close") or 0.0)
            prev = float(data.get("previousClose") or 0.0)
        except (TypeError, ValueError) as e:
            raise SourceUnavailable(f"eodhd bad payload {symbol}: {e}") from e
        if price <= 0:
            raise SourceUnavailable(f"eodhd: zero price for {symbol}")
        ccy = _ccy_from_suffix(sym)
        change_pct = ((price - prev) / prev * 100) if prev else None
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=prev,
            currency=ccy,
            day_change_pct=change_pct,
            source=self.name,
            as_of=time.time(),
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        if not self.is_configured():
            raise SourceUnavailable("eodhd: no key")
        if interval != "1d":
            raise SourceUnavailable("eodhd: daily only on free tier")
        sym = _eod_symbol(symbol)
        from datetime import date, timedelta

        days_map = {
            "1mo": 31,
            "3mo": 93,
            "6mo": 186,
            "1y": 365,
            "2y": 730,
            "3y": 1095,
            "5y": 1825,
            "10y": 3650,
            "ytd": 365,
            "max": 365 * 30,
        }
        days = days_map.get(period, 365)
        start = (date.today() - timedelta(days=days)).isoformat()
        try:
            resp = httpx.get(
                f"{_BASE}/eod/{sym}",
                params={
                    "api_token": self.api_key,
                    "from": start,
                    "fmt": "json",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json() or []
        except Exception as e:
            raise SourceUnavailable(f"eodhd http {symbol}: {e}") from e

        if not data:
            raise SourceUnavailable(f"eodhd: empty history for {symbol}")
        bars: list[PriceBar] = []
        for row in data:
            try:
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=str(row.get("date") or "")[:10],
                        open=float(row.get("open") or 0.0),
                        high=float(row.get("high") or 0.0),
                        low=float(row.get("low") or 0.0),
                        close=float(row.get("close") or 0.0),
                        volume=float(row.get("volume") or 0.0),
                        adj_close=float(row.get("adjusted_close") or row.get("close") or 0.0),
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            except (TypeError, ValueError):
                continue
        if not bars:
            raise SourceUnavailable(f"eodhd: parsed zero bars for {symbol}")
        return bars

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        if not self.is_configured():
            raise SourceUnavailable("eodhd: no key")
        sym = _eod_symbol(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/fundamentals/{sym}",
                params={"api_token": self.api_key},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"eodhd http {symbol}: {e}") from e

        if not data or not isinstance(data, dict):
            raise SourceUnavailable(f"eodhd: empty fundamentals for {symbol}")
        gen = data.get("General") or {}
        hi = data.get("Highlights") or {}
        tech = data.get("Technicals") or {}
        return Fundamentals(
            symbol=symbol,
            pe_ratio=_f(hi.get("PERatio")),
            eps=_f(hi.get("EarningsShare")),
            dividend_yield_pct=_pct(hi.get("DividendYield")),
            payout_ratio_pct=_pct(hi.get("PayoutRatio")),
            market_cap=_f(hi.get("MarketCapitalization")),
            beta=_f(tech.get("Beta")),
            analyst_target=_f(hi.get("WallStreetTargetPrice")),
            earnings_date=hi.get("MostRecentQuarter"),
            sector=gen.get("Sector"),
            industry=gen.get("Industry"),
            institutional_pct=None,
            short_pct_float=_f(tech.get("ShortPercent")),
            source=self.name,
            as_of=time.time(),
        )


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _pct(x) -> float | None:
    f = _f(x)
    return f * 100 if f is not None and f < 1 else f
