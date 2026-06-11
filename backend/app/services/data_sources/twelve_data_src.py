"""Twelve Data adapter — 800 calls/day free tier, key required.

Strong because:
- Real-time US + EU equities, ETFs, FX, crypto, indices in one feed.
- Canadian TSX coverage via .TO suffix passthrough.
- Single endpoint shape (/quote, /price, /time_series).

Set TWELVE_DATA_KEY in backend/.env. Without key, is_configured() returns
False so the router skips it.

Rate-limit handling: 429 / "rate limit exceeded" responses raise
SourceUnavailable so the circuit breaker can trip and the router falls
through.
"""

from __future__ import annotations

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

_BASE = "https://api.twelvedata.com"

_INTERVAL_MAP = {
    "1d": "1day",
    "1h": "1h",
    "30m": "30min",
    "15m": "15min",
    "5m": "5min",
    "1m": "1min",
}

_PERIOD_OUTPUTSIZE = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
    "10y": 3650,
    "ytd": 365,
    "max": 5000,
}


def _td_symbol(symbol: str) -> str:
    """Map portfolio symbol -> Twelve Data symbol form.

    Twelve Data conventions (verified against their /quote endpoint):
      - US equities:   AAPL
      - TSX:           SHOP  (root, exchange resolved by API)
      - LSE:           BARC.L
      - XETRA:         SAP.DE
      - FX pairs:      USD/CAD  (slash separator)
      - Crypto:        BTC/USD
    """
    s = symbol.strip().upper()
    s = s.replace("=X", "")
    # FX pair form (6 alpha chars) -> insert slash
    if len(s) == 6 and s.isalpha():
        return f"{s[:3]}/{s[3:]}"
    # Canadian: strip suffix; TD resolves TSX as default exchange
    if s.endswith(".TO") or s.endswith(".TSX"):
        return s.rsplit(".", 1)[0]
    if s.endswith(".V"):
        return s[:-2]
    if s.endswith(".NE") or s.endswith(".CN"):
        return s.rsplit(".", 1)[0]
    return s


def _td_extra_params(symbol: str) -> dict:
    """Per-symbol query overrides (exchange, country) when ambiguous."""
    s = symbol.strip().upper()
    if s.endswith(".TO") or s.endswith(".TSX"):
        return {"exchange": "TSX"}
    if s.endswith(".V"):
        return {"exchange": "TSXV"}
    if s.endswith(".NE"):
        return {"exchange": "NEO"}
    return {}


class TwelveDataSource(DataSource):
    name = "twelve_data"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TWELVE_DATA_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _check_error(self, data: dict, symbol: str) -> None:
        if isinstance(data, dict) and data.get("status") == "error":
            msg = data.get("message", "unknown")
            code = data.get("code")
            if code == 429 or "limit" in msg.lower():
                raise SourceUnavailable(f"twelve_data rate limited: {msg}")
            raise SourceUnavailable(f"twelve_data {symbol}: {msg}")

    def get_quote(self, symbol: str) -> Quote:
        if not self.is_configured():
            raise SourceUnavailable("twelve_data: no key")
        sym = _td_symbol(symbol)
        params = {"symbol": sym, "apikey": self.api_key}
        params.update(_td_extra_params(symbol))
        try:
            resp = httpx.get(
                f"{_BASE}/quote",
                params=params,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"twelve_data http {symbol}: {redact_secrets(e)}") from e

        self._check_error(data, symbol)
        try:
            price = float(data.get("close") or data.get("price") or 0.0)
            prev = float(data.get("previous_close") or 0.0)
        except (TypeError, ValueError) as e:
            raise SourceUnavailable(f"twelve_data bad payload {symbol}: {e}") from e
        if price <= 0:
            raise SourceUnavailable(f"twelve_data: zero price for {symbol}")
        ccy = (data.get("currency") or "").upper() or None
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
            raise SourceUnavailable("twelve_data: no key")
        td_int = _INTERVAL_MAP.get(interval)
        if td_int is None:
            raise SourceUnavailable(f"twelve_data: unsupported interval {interval}")
        outputsize = min(_PERIOD_OUTPUTSIZE.get(period, 365), 5000)
        sym = _td_symbol(symbol)
        params = {
            "symbol": sym,
            "interval": td_int,
            "outputsize": outputsize,
            "apikey": self.api_key,
            "order": "ASC",
        }
        params.update(_td_extra_params(symbol))
        try:
            resp = httpx.get(
                f"{_BASE}/time_series",
                params=params,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"twelve_data http {symbol}: {redact_secrets(e)}") from e

        self._check_error(data, symbol)
        values = data.get("values") or []
        if not values:
            raise SourceUnavailable(f"twelve_data: empty history for {symbol}")
        bars: list[PriceBar] = []
        for v in values:
            try:
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=str(v.get("datetime") or "")[:10],
                        open=float(v.get("open") or 0.0),
                        high=float(v.get("high") or 0.0),
                        low=float(v.get("low") or 0.0),
                        close=float(v.get("close") or 0.0),
                        volume=float(v.get("volume") or 0.0),
                        adj_close=float(v.get("close") or 0.0),
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            except (TypeError, ValueError):
                continue
        if not bars:
            raise SourceUnavailable(f"twelve_data: parsed zero bars for {symbol}")
        return bars
