"""_coerce_date_str — calendar value normalization for the dividend calendar.

yfinance returns Ex-Dividend / Dividend Date values in inconsistent shapes
(date, Timestamp, list-of-dates, pandas Series). _coerce_date_str must reduce
all of them to a YYYY-MM-DD string or None, never raise.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from app.services.fundamentals import _coerce_date_str


def test_none_returns_none() -> None:
    assert _coerce_date_str(None) is None


def test_plain_date() -> None:
    assert _coerce_date_str(dt.date(2026, 5, 7)) == "2026-05-07"


def test_timestamp() -> None:
    assert _coerce_date_str(pd.Timestamp("2026-06-09")) == "2026-06-09"


def test_list_takes_first() -> None:
    assert _coerce_date_str([dt.date(2026, 5, 7), dt.date(2026, 8, 6)]) == "2026-05-07"


def test_empty_list_returns_none() -> None:
    assert _coerce_date_str([]) is None


def test_series_takes_first() -> None:
    s = pd.Series([dt.date(2026, 5, 7), dt.date(2026, 8, 6)])
    assert _coerce_date_str(s) == "2026-05-07"


def test_iso_string_truncated_to_date() -> None:
    assert _coerce_date_str("2026-05-07T00:00:00") == "2026-05-07"
