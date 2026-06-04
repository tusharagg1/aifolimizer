"""Coinbase Exchange (Advanced Trade) public REST adapter — crypto, free, no key.

Endpoints:
  https://api.exchange.coinbase.com/products/BTC-USD/ticker     # quote (last price)
  https://api.exchange.coinbase.com/products/BTC-USD/stats      # 24h open for change%
  https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400  # history

Public market-data endpoints need no key/account; ~10 req/s per IP. The
product id (BTC-USD) is in the URL path, so an unknown symbol returns 404 ->
SourceUnavailable (anti-mixup guard). USD-denominated; Canada-accessible.
Candles row order is [time, low, high, open, close, volume]; max 300/call.
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

_BASE = "https://api.exchange.coinbase.com"
_HEADERS = {"User-Agent": "aifolimizer/1.0"}  # Coinbase 403s requests with no UA

_GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}

_PERIOD_DAYS = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 300,
    "2y": 300,
    "3y": 300,
    "5y": 300,
    "ytd": 300,
    "max": 300,  # candles capped at 300 points per call
}


def _to_product(symbol: str) -> str:
    s = symbol.upper().replace("-USD", "")
    if s.endswith("USD"):
        s = s[:-3]
    return f"{s}-USD"


class CoinbaseSource(DataSource):
    name = "coinbase"

    def is_configured(self) -> bool:
        return True  # public endpoints, no key

    def get_quote(self, symbol: str) -> Quote:
        pid = _to_product(symbol)
        try:
            resp = httpx.get(f"{_BASE}/products/{pid}/ticker", headers=_HEADERS, timeout=10.0)
            if resp.status_code == 404:
                raise SourceUnavailable(f"coinbase: unknown product {pid}")
            resp.raise_for_status()
            tick = resp.json() or {}
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"coinbase http {symbol}: {type(e).__name__}") from e

        try:
            price = float(tick.get("price") or 0.0)
        except (TypeError, ValueError) as e:
            raise SourceUnavailable(f"coinbase bad payload {symbol}: {e}") from e
        if price <= 0:
            raise SourceUnavailable(f"coinbase: zero price for {symbol}")

        open_ = price
        try:
            s = httpx.get(f"{_BASE}/products/{pid}/stats", headers=_HEADERS, timeout=10.0)
            if s.status_code == 200:
                open_ = float((s.json() or {}).get("open") or price)
        except Exception:
            open_ = price
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
        gran = _GRANULARITY.get(interval)
        if gran is None:
            raise SourceUnavailable(f"coinbase: unsupported interval {interval}")
        pid = _to_product(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/products/{pid}/candles",
                params={"granularity": gran},
                headers=_HEADERS,
                timeout=15.0,
            )
            if resp.status_code == 404:
                raise SourceUnavailable(f"coinbase: unknown product {pid}")
            resp.raise_for_status()
            data = resp.json() or []
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"coinbase http {symbol}: {type(e).__name__}") from e

        if not isinstance(data, list) or not data:
            raise SourceUnavailable(f"coinbase: empty candles for {symbol}")
        days = _PERIOD_DAYS.get(period, 300)
        rows = sorted(data, key=lambda r: r[0])[-days:] if interval == "1d" else sorted(data, key=lambda r: r[0])
        bars: list[PriceBar] = []
        for k in rows:
            # [time, low, high, open, close, volume]
            try:
                d = datetime.utcfromtimestamp(int(k[0])).strftime("%Y-%m-%d")
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=d,
                        open=float(k[3]),
                        high=float(k[2]),
                        low=float(k[1]),
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
            raise SourceUnavailable(f"coinbase: parsed zero bars for {symbol}")
        return bars
