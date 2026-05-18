"""Tiingo adapter (free 50 calls/hr, key required).

Good EOD price coverage incl. ETFs and Canadian via /tiingo/daily.
Set TIINGO_KEY in backend/.env.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import httpx

from app.services.data_sources.base import (
    DataSource,
    PriceBar,
    Quote,
    SourceUnavailable,
)

_BASE = "https://api.tiingo.com"

_PERIOD_DAYS = {
    "1mo": 31, "3mo": 93, "6mo": 186, "1y": 365,
    "2y": 365 * 2, "3y": 365 * 3, "5y": 365 * 5,
    "10y": 365 * 10, "ytd": 365, "max": 365 * 30,
}


class TiingoSource(DataSource):
    name = "tiingo"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TIINGO_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Token {self.api_key}",
        }

    def _tg_symbol(self, symbol: str) -> str:
        s = symbol.strip().upper()
        if s.endswith(".TO"):
            return s.split(".")[0] + "-CA"  # Tiingo Canadian convention
        if s.endswith(".V"):
            return s.split(".")[0] + "-CA"
        return s

    def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> list[PriceBar]:
        if not self.is_configured():
            raise SourceUnavailable("tiingo: no API key")
        if interval != "1d":
            raise SourceUnavailable("tiingo free: daily only")
        days = _PERIOD_DAYS.get(period, 365)
        start = (date.today() - timedelta(days=days)).isoformat()
        sym = self._tg_symbol(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/tiingo/daily/{sym}/prices",
                params={"startDate": start},
                headers=self._headers(),
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json() or []
        except Exception as e:
            raise SourceUnavailable(f"tiingo http {symbol}: {e}") from e

        if not data:
            raise SourceUnavailable(f"tiingo: empty history for {symbol}")
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
                        adj_close=float(row.get("adjClose") or row.get("close") or 0.0),
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            except (TypeError, ValueError):
                continue
        if not bars:
            raise SourceUnavailable(f"tiingo: parsed zero bars for {symbol}")
        return bars

    def get_quote(self, symbol: str) -> Quote:
        bars = self.get_history(symbol, period="1mo", interval="1d")
        if len(bars) < 2:
            raise SourceUnavailable(f"tiingo: insufficient bars for quote {symbol}")
        last, prev = bars[-1], bars[-2]
        return Quote(
            symbol=symbol,
            price=last.close,
            prev_close=prev.close,
            currency=None,
            day_change_pct=((last.close - prev.close) / prev.close * 100) if prev.close else None,
            source=self.name,
            as_of=time.time(),
        )
