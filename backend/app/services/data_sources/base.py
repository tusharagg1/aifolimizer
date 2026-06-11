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
import re
import time
from dataclasses import dataclass, field, asdict

import httpx


class SourceUnavailable(Exception):
    """Raised when a source cannot serve the request (no key, rate limit, 404).

    Router catches this and falls through to the next source.
    Distinct from generic Exception so true bugs surface.
    """


# Adapters that pass credentials as URL query params leak the key into httpx
# exception strings (which embed the full request URL). Those messages flow into
# logs and data_source_reliability error fields. Scrub credential values before
# they reach any SourceUnavailable message so a live key never lands in a log.
_SECRET_QUERY_RE = re.compile(
    r"(?i)\b(apikey|api_key|api_token|access_token|access_key|token|secret)=[^&\s'\")>]+"
)


def redact_secrets(text: object) -> str:
    """Mask credential values in URL/query strings before logging or raising."""
    return _SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}=<redacted>", str(text))


def to_float(x: object) -> float | None:
    """Coerce a provider value to float, treating None/'None'/'-'/garbage as None."""
    if x is None or x == "None" or x == "-":
        return None
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def pct_normalize(x: object) -> float | None:
    """Normalize a ratio-or-percent field to percent (0.04 -> 4.0; 4.0 stays 4.0)."""
    f = to_float(x)
    if f is None:
        return None
    return f * 100 if f < 1 else f


def fetch_json(
    url: str,
    *,
    name: str,
    symbol: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
    default: object = None,
):
    """GET + raise_for_status + json, with credential-safe error wrapping.

    Every adapter HTTP error funnels through here so the request URL (which may
    carry an apikey query param) is scrubbed by redact_secrets before it reaches
    a SourceUnavailable message / log. `default` is returned when the body is
    falsy (pass {} for object endpoints, [] for list endpoints).
    """
    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json() or ({} if default is None else default)
    except SourceUnavailable:
        raise
    except Exception as e:
        raise SourceUnavailable(f"{name} http {symbol}: {redact_secrets(e)}") from e


@dataclass
class PriceBar:
    symbol: str
    date: str  # ISO YYYY-MM-DD (EOD) or full ISO timestamp (intraday)
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

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[PriceBar]:
        raise SourceUnavailable(f"{self.name} does not provide history")

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        raise SourceUnavailable(f"{self.name} does not provide fundamentals")
