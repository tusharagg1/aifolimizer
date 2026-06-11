"""Stooq EOD CSV adapter.

Stooq endpoints (https://stooq.com/q/d/l/?s=<sym>&i=d) deliver daily OHLCV
CSV. Stooq added an apikey requirement in 2024 so we now need
STOOQ_KEY in the env (free, captcha-gated, no expiry).

Symbol mapping:
  AAPL    -> aapl.us
  XEQT.TO -> xeqt.ca
  ^GSPTSE -> ^spx is US S&P; for TSX Composite we use ^tsx

Only history + last-quote. No fundamentals.
"""

from __future__ import annotations

import csv
import io
import os
import time

import httpx

from app.services.data_sources.base import (
    DataSource,
    PriceBar,
    Quote,
    SourceUnavailable,
    redact_secrets,
)

_PERIOD_TO_DAYS = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 365,
    "2y": 365 * 2,
    "3y": 365 * 3,
    "5y": 365 * 5,
    "10y": 365 * 10,
    "ytd": 365,
    "max": 365 * 30,
}


def _stooq_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.endswith(".TO") or s.endswith(".V"):
        return s.split(".")[0].lower() + ".ca"
    if s.startswith("^"):
        # Index — best effort. Caller can fall through if mapping fails.
        idx_map = {
            "^GSPC": "^spx",
            "^GSPTSE": "^tsx",
            "^IXIC": "^ndq",
            "^DJI": "^dji",
            "^VIX": "^vix",
        }
        return idx_map.get(s, s.lower())
    return s.lower() + ".us"


class StooqSource(DataSource):
    name = "stooq"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("STOOQ_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        if not self.is_configured():
            raise SourceUnavailable("stooq: no STOOQ_KEY")
        if interval != "1d":
            raise SourceUnavailable("stooq supports daily interval only")
        ssym = _stooq_symbol(symbol)
        url = f"https://stooq.com/q/d/l/?s={ssym}&i=d&apikey={self.api_key}"
        try:
            resp = httpx.get(url, timeout=10.0)
            resp.raise_for_status()
        except Exception as e:
            raise SourceUnavailable(f"stooq http {ssym}: {redact_secrets(e)}") from e
        text = resp.text.strip()
        if not text or text.lower().startswith("no data"):
            raise SourceUnavailable(f"stooq: no data for {ssym}")
        if "apikey" in text.lower() and "Date,Open" not in text:
            raise SourceUnavailable("stooq: invalid or missing STOOQ_KEY")
        reader = csv.DictReader(io.StringIO(text))
        bars: list[PriceBar] = []
        days_keep = _PERIOD_TO_DAYS.get(period, 365)
        for row in reader:
            try:
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=row["Date"],
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0.0),
                        adj_close=float(row["Close"]),
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            except (KeyError, ValueError):
                continue
        if not bars:
            raise SourceUnavailable(f"stooq: parsed zero bars for {ssym}")
        return bars[-days_keep:]

    def get_quote(self, symbol: str) -> Quote:
        bars = self.get_history(symbol, period="1mo", interval="1d")
        if len(bars) < 2:
            raise SourceUnavailable(f"stooq: insufficient bars for quote {symbol}")
        last, prev = bars[-1], bars[-2]
        change_pct = ((last.close - prev.close) / prev.close * 100) if prev.close else None
        return Quote(
            symbol=symbol,
            price=last.close,
            prev_close=prev.close,
            currency=None,
            day_change_pct=change_pct,
            source=self.name,
            as_of=time.time(),
        )
