"""Multi-source data adapter package.

Each adapter implements the DataSource interface in base.py and is composed
by data_router.py into a fallback chain. Order of preference:
  yfinance (free, fast)
    -> stooq        (free, EOD only, no key)
    -> finnhub      (free 60/min, key)
    -> alpha_vantage(free 25/day, key)
    -> tiingo       (free 50/hr, key)

Every response carries a `source` field so callers (and trust-signal reports)
can attribute which provider supplied the bytes.
"""

from app.services.data_sources.base import (
    DataSource,
    PriceBar,
    Quote,
    Fundamentals,
    SourceUnavailable,
)

__all__ = [
    "DataSource",
    "PriceBar",
    "Quote",
    "Fundamentals",
    "SourceUnavailable",
]
