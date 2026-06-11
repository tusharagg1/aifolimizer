"""Massive (formerly Polygon.io) data source — US stocks only.

TSX/Canadian symbols raise SourceUnavailable immediately so the router
falls through to yfinance without wasting an API call.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    PriceBar,
    Quote,
    SourceUnavailable,
    redact_secrets,
)

_TSX_SUFFIXES = (".TO", ".V", ".TSX", ".NE", ".CN")
_KNOWN_CANADIAN = frozenset(
    {
        "XEQT",
        "VFV",
        "XIC",
        "XIU",
        "ZSP",
        "XUS",
        "ZEB",
        "VDY",
        "CDZ",
        "XRE",
        "VCN",
        "ZCN",
        "XSP",
        "HXT",
        "HGRO",
        "VEQT",
        "VGRO",
        "VBAL",
        "VCIP",
    }
)


def is_tsx(symbol: str) -> bool:
    """True for TSX/Canadian symbols Massive doesn't cover."""
    upper = symbol.upper()
    return any(upper.endswith(s) for s in _TSX_SUFFIXES) or upper in _KNOWN_CANADIAN


def _period_dates(period: str) -> tuple[str, str]:
    to_dt = datetime.now()
    days = {
        "1d": 2,
        "5d": 7,
        "1mo": 32,
        "3mo": 95,
        "6mo": 185,
        "1y": 370,
        "2y": 740,
        "5y": 1830,
    }
    from_dt = to_dt - timedelta(days=days.get(period, 370))
    return from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d")


def _interval_params(interval: str) -> tuple[int, str]:
    return {
        "1d": (1, "day"),
        "1h": (1, "hour"),
        "30m": (30, "minute"),
        "15m": (15, "minute"),
        "5m": (5, "minute"),
        "1m": (1, "minute"),
    }.get(interval, (1, "day"))


class MassiveSource(DataSource):
    name = "massive"

    def __init__(self) -> None:
        self._key = os.environ.get("MASSIVE_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._key:
            raise SourceUnavailable("MASSIVE_API_KEY not set")
        try:
            from massive import RESTClient

            self._client = RESTClient(api_key=self._key)
            return self._client
        except ImportError as e:
            raise SourceUnavailable(f"massive package not installed: {e}") from e

    def is_configured(self) -> bool:
        return bool(self._key)

    def get_quote(self, symbol: str) -> Quote:
        """Last close from most recent daily agg bar.

        Snapshot endpoint requires paid plan; list_aggs is free-tier.
        """
        if is_tsx(symbol):
            raise SourceUnavailable(f"massive: {symbol} is TSX")
        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=5)
            bars = list(
                self._get_client().list_aggs(
                    ticker=symbol,
                    multiplier=1,
                    timespan="day",
                    from_=from_dt.strftime("%Y-%m-%d"),
                    to=to_dt.strftime("%Y-%m-%d"),
                    limit=5,
                    adjusted=True,
                )
            )
            if not bars:
                raise SourceUnavailable(f"massive: no bars for {symbol}")
            bars.sort(key=lambda b: b.timestamp)
            latest = bars[-1]
            price = float(latest.close)
            prev_close = float(bars[-2].close) if len(bars) >= 2 else price
            if price <= 0:
                raise SourceUnavailable(f"massive: zero price for {symbol}")
            change_pct = (price - prev_close) / prev_close * 100 if prev_close else None
            return Quote(
                symbol=symbol,
                price=price,
                prev_close=prev_close,
                currency="USD",
                day_change_pct=change_pct,
                source=self.name,
                as_of=time.time(),
            )
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"massive quote {symbol}: {redact_secrets(e)}") from e

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        if is_tsx(symbol):
            raise SourceUnavailable(f"massive: {symbol} is TSX")
        try:
            from_, to = _period_dates(period)
            multiplier, timespan = _interval_params(interval)
            bars: list[PriceBar] = []
            for agg in self._get_client().list_aggs(
                ticker=symbol,
                multiplier=multiplier,
                timespan=timespan,
                from_=from_,
                to=to,
                limit=50000,
                adjusted=True,
            ):
                try:
                    ts = datetime.fromtimestamp(agg.timestamp / 1000)
                    date_str = ts.strftime("%Y-%m-%d") if timespan == "day" else ts.strftime("%Y-%m-%dT%H:%M:%S")
                    bars.append(
                        PriceBar(
                            symbol=symbol,
                            date=date_str,
                            open=float(agg.open),
                            high=float(agg.high),
                            low=float(agg.low),
                            close=float(agg.close),
                            volume=float(agg.volume or 0),
                            adj_close=(float(agg.vwap) if getattr(agg, "vwap", None) else float(agg.close)),
                            source=self.name,
                            as_of=time.time(),
                        )
                    )
                except Exception:
                    continue
            if not bars:
                raise SourceUnavailable(f"massive: empty history for {symbol}")
            return bars
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"massive history {symbol}: {redact_secrets(e)}") from e

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        # ticker_details is rate-limited on free tier — delegate to yfinance
        raise SourceUnavailable(f"massive: fundamentals deferred to yfinance for {symbol}")
