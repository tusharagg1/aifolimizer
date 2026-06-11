"""Binance public spot adapter - crypto, free, no API key.

Endpoints:
  https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT     # quote
  https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000  # history

USDT is treated as USD for our purposes (1:1 peg, deviation <0.5% always).
For CAD denomination caller should multiply by USDCAD via FX adapter.

Why: real-time, deep liquidity, no auth, no quota for public data.
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

_BASE = "https://api.binance.com/api/v3"

_INTERVAL_MAP = {
    "1d": "1d",
    "1h": "1h",
    "30m": "30m",
    "15m": "15m",
    "5m": "5m",
    "1m": "1m",
}

_PERIOD_LIMIT = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
    "ytd": 365,
    "max": 1000,  # binance hard cap 1000 klines per call
}


def _to_pair(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


class BinanceSource(DataSource):
    name = "binance"

    def is_configured(self) -> bool:
        return True  # public spot endpoints, no key

    def get_quote(self, symbol: str) -> Quote:
        pair = _to_pair(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/ticker/24hr",
                params={"symbol": pair},
                timeout=10.0,
            )
            if resp.status_code == 400:
                raise SourceUnavailable(f"binance: unknown pair {pair}")
            resp.raise_for_status()
            data = resp.json() or {}
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"binance http {symbol}: {e}") from e

        try:
            price = float(data.get("lastPrice") or 0.0)
            prev = float(data.get("prevClosePrice") or data.get("openPrice") or 0.0)
        except (TypeError, ValueError) as e:
            raise SourceUnavailable(f"binance bad payload {symbol}: {e}") from e
        if price <= 0:
            raise SourceUnavailable(f"binance: zero price for {symbol}")

        change_pct = ((price - prev) / prev * 100) if prev else None
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=prev,
            currency="USD",
            day_change_pct=change_pct,
            source=self.name,
            as_of=time.time(),
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        bn_interval = _INTERVAL_MAP.get(interval)
        if bn_interval is None:
            raise SourceUnavailable(f"binance: unsupported interval {interval}")
        limit = min(_PERIOD_LIMIT.get(period, 365), 1000)
        pair = _to_pair(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/klines",
                params={"symbol": pair, "interval": bn_interval, "limit": limit},
                timeout=15.0,
            )
            if resp.status_code == 400:
                raise SourceUnavailable(f"binance: unknown pair {pair}")
            resp.raise_for_status()
            data = resp.json() or []
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"binance http {symbol}: {e}") from e

        if not data:
            raise SourceUnavailable(f"binance: empty klines for {symbol}")
        bars: list[PriceBar] = []
        for k in data:
            try:
                ts_ms = int(k[0])
                d = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=d,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                        adj_close=float(k[4]),
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            except (TypeError, ValueError, IndexError):
                continue
        if not bars:
            raise SourceUnavailable(f"binance: parsed zero bars for {symbol}")
        return bars
