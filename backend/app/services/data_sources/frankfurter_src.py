"""Frankfurter FX adapter — ECB reference rates, free, no API key.

Endpoint: https://api.frankfurter.dev/v1/latest?from=USD&to=CAD
Endpoint: https://api.frankfurter.dev/v1/2024-01-15..2025-01-15?from=USD&to=CAD

Coverage: 30+ currencies vs ECB reference rates. EOD only — published
~16:00 CET each business day. NOT real-time but always fresh, accurate,
and survives without a key. Use as the FX primary since CAD/USD only
moves ~1% intraday and ECB closes match end-of-day cash needs.

Symbol form accepted by classify_asset and this adapter:
  USDCAD     -> from=USD to=CAD
  USDCAD=X   -> stripped to USDCAD
  EURUSD     -> from=EUR to=USD
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import httpx

from app.services.data_sources.base import (
    DataSource,
    PriceBar,
    Quote,
    SourceUnavailable,
)

_BASE = "https://api.frankfurter.dev/v1"


def _split_pair(symbol: str) -> tuple[str, str]:
    s = symbol.upper().replace("=X", "")
    if len(s) != 6 or not s.isalpha():
        raise SourceUnavailable(f"frankfurter: bad pair {symbol}")
    return s[:3], s[3:]


class FrankfurterSource(DataSource):
    name = "frankfurter"

    def is_configured(self) -> bool:
        return True  # no key, public ECB feed

    def get_quote(self, symbol: str) -> Quote:
        base, target = _split_pair(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/latest",
                params={"base": base, "symbols": target},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"frankfurter http {symbol}: {e}") from e

        rates = data.get("rates") or {}
        rate = rates.get(target)
        if rate is None:
            raise SourceUnavailable(f"frankfurter: no rate for {symbol}")

        # Previous-day rate for change calc
        prev_rate = float(rate)
        try:
            yday = (date.today() - timedelta(days=2)).isoformat()  # 2d to skip weekends
            r2 = httpx.get(
                f"{_BASE}/{yday}",
                params={"base": base, "symbols": target},
                timeout=10.0,
            )
            r2.raise_for_status()
            d2 = r2.json() or {}
            prev = (d2.get("rates") or {}).get(target)
            if prev is not None:
                prev_rate = float(prev)
        except Exception:
            logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

        price = float(rate)
        change_pct = ((price - prev_rate) / prev_rate * 100) if prev_rate else None
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=prev_rate,
            currency=target,
            day_change_pct=change_pct,
            source=self.name,
            as_of=time.time(),
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        if interval != "1d":
            raise SourceUnavailable("frankfurter: daily only")
        base, target = _split_pair(symbol)
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
            "max": 365 * 25,
        }
        days = days_map.get(period, 365)
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()
        try:
            resp = httpx.get(
                f"{_BASE}/{start}..{end}",
                params={"base": base, "symbols": target},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"frankfurter http {symbol}: {e}") from e

        rates = data.get("rates") or {}
        if not rates:
            raise SourceUnavailable(f"frankfurter: empty range for {symbol}")
        bars: list[PriceBar] = []
        for d, kv in sorted(rates.items()):
            r = kv.get(target)
            if r is None:
                continue
            r = float(r)
            bars.append(
                PriceBar(
                    symbol=symbol,
                    date=d,
                    open=r,
                    high=r,
                    low=r,
                    close=r,
                    volume=0.0,
                    adj_close=r,
                    source=self.name,
                    as_of=time.time(),
                )
            )
        if not bars:
            raise SourceUnavailable(f"frankfurter: no bars parsed for {symbol}")
        return bars
