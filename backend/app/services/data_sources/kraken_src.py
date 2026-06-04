"""Kraken public REST adapter — crypto spot, free, no API key.

Endpoints:
  https://api.kraken.com/0/public/Ticker?pair=XBTUSD            # quote
  https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440 # daily history

Public market-data endpoints need no key and no account; throttle to
~1 req/s per IP. Kraken renames assets (BTC->XBT, DOGE->XDG) and returns
data under its OWN canonical pair key, so we request exactly one pair and
read the single result entry — a wrong/unknown pair yields an `error` or an
empty result, which we surface as SourceUnavailable (anti-mixup guard).

USD-denominated; caller converts to CAD via the FX adapter. Unlike Binance
global (geo-blocked in Canada), Kraken is fully accessible from Canada.
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from app.services.data_sources.base import (
    DataSource,
    PriceBar,
    Quote,
    SourceUnavailable,
)

_BASE = "https://api.kraken.com/0/public"

# Kraken uses non-standard asset codes for a few coins.
_ALIAS = {"BTC": "XBT", "DOGE": "XDG"}

_INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1wk": 10080}

_PERIOD_DAYS = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 365,
    "2y": 720,
    "3y": 720,
    "5y": 720,
    "ytd": 365,
    "max": 720,  # OHLC returns at most ~720 candles per call
}


def _to_pair(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USD"):
        s = s[:-3]
    base = _ALIAS.get(s, s)
    return f"{base}USD"


def _result_entry(payload: dict, symbol: str) -> dict:
    err = payload.get("error") or []
    if err:
        raise SourceUnavailable(f"kraken {symbol}: {err}")
    result = payload.get("result") or {}
    if not result:
        raise SourceUnavailable(f"kraken: empty result for {symbol}")
    # Exactly one pair requested -> exactly one entry, keyed by Kraken's name.
    return next(iter(result.values()))


class KrakenSource(DataSource):
    name = "kraken"

    def is_configured(self) -> bool:
        return True  # public endpoints, no key

    def get_quote(self, symbol: str) -> Quote:
        pair = _to_pair(symbol)
        try:
            resp = httpx.get(f"{_BASE}/Ticker", params={"pair": pair}, timeout=10.0)
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"kraken http {symbol}: {type(e).__name__}") from e

        entry = _result_entry(payload, symbol)
        try:
            price = float(entry["c"][0])  # c = [last trade price, lot volume]
            open_ = float(entry["o"])  # today's opening price
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise SourceUnavailable(f"kraken bad payload {symbol}: {e}") from e
        if price <= 0:
            raise SourceUnavailable(f"kraken: zero price for {symbol}")

        change_pct = ((price - open_) / open_ * 100) if open_ else None
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=open_,
            currency="USD",
            day_change_pct=change_pct,
            source=self.name,
            as_of=time.time(),
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        kr_int = _INTERVAL_MIN.get(interval)
        if kr_int is None:
            raise SourceUnavailable(f"kraken: unsupported interval {interval}")
        pair = _to_pair(symbol)
        try:
            resp = httpx.get(f"{_BASE}/OHLC", params={"pair": pair, "interval": kr_int}, timeout=15.0)
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"kraken http {symbol}: {type(e).__name__}") from e

        entry = _result_entry(payload, symbol)
        if not isinstance(entry, list) or not entry:
            raise SourceUnavailable(f"kraken: empty OHLC for {symbol}")

        days = _PERIOD_DAYS.get(period, 365)
        rows = entry[-days:] if interval == "1d" else entry
        bars: list[PriceBar] = []
        for k in rows:
            # [time, open, high, low, close, vwap, volume, count]
            try:
                d = datetime.utcfromtimestamp(int(k[0])).strftime("%Y-%m-%d")
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=d,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[6]),
                        adj_close=float(k[4]),
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            except (TypeError, ValueError, IndexError):
                continue
        if not bars:
            raise SourceUnavailable(f"kraken: parsed zero bars for {symbol}")
        return bars
