"""Abstract data source interface + value types.

Every concrete source (yfinance, stooq, finnhub, alpha_vantage, tiingo)
implements DataSource. Missing capability is signalled by raising
SourceUnavailable so the router can try the next adapter.

Staleness:
- Each Quote/PriceBar carries `as_of` (epoch seconds).
- Router checks (now - as_of) against staleness budget before accepting.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field, asdict


class SourceUnavailable(Exception):
    """Raised when a source cannot serve the request (no key, rate limit, 404).

    Router catches this and falls through to the next source.
    Distinct from generic Exception so true bugs surface.
    """


@dataclass
class PriceBar:
    symbol: str
    date: str            # ISO YYYY-MM-DD (EOD) or full ISO timestamp (intraday)
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None
    source: str = ""
    as_of: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Quote:
    symbol: str
    price: float
    prev_close: float
    currency: str | None
    day_change_pct: float | None
    source: str = ""
    as_of: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Fundamentals:
    symbol: str
    pe_ratio: float | None = None
    eps: float | None = None
    dividend_yield_pct: float | None = None
    payout_ratio_pct: float | None = None
    market_cap: float | None = None
    beta: float | None = None
    analyst_target: float | None = None
    earnings_date: str | None = None
    sector: str | None = None
    industry: str | None = None
    institutional_pct: float | None = None
    short_pct_float: float | None = None
    source: str = ""
    as_of: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class DataSource(abc.ABC):
    """Interface every data adapter implements.

    Methods raise SourceUnavailable if the source cannot serve the call
    (e.g. fundamentals not exposed, no API key, hit rate limit).
    """

    name: str = "base"

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True if source has needed credentials / can be called at all."""

    def get_quote(self, symbol: str) -> Quote:
        raise SourceUnavailable(f"{self.name} does not provide quotes")

    def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> list[PriceBar]:
        raise SourceUnavailable(f"{self.name} does not provide history")

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        raise SourceUnavailable(f"{self.name} does not provide fundamentals")
