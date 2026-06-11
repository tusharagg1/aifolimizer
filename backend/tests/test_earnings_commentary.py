"""Earnings-call management-commentary language signal.

Scores guidance/management language for tone (bull vs bear lexicon, negation-
aware via community.score_text_polarity). Because prepared remarks are always
promotional (absolute tone clusters near 1.0), the PREDICTIVE signal is the
RELATIVE trend vs prior quarters (mgmt_tone_trend). Source chain (all free):
Alpha Vantage transcript (multi-quarter) -> SEC EDGAR 8-K text (single doc).
Reuses existing NLP primitive + fetch plumbing - no new ML dependency.
"""

from __future__ import annotations

import pytest

import app.services.earnings_commentary as ec


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch) -> None:
    # Disk cache persists across tests; isolate so each test exercises live logic.
    monkeypatch.setattr(ec.cache_layer, "cache_get", lambda ns, k: None)
    monkeypatch.setattr(ec.cache_layer, "cache_set", lambda ns, k, v, ttl: None)


def test_score_commentary_bullish_text() -> None:
    text = (
        "We raised full-year guidance on accelerating demand. Momentum is strong "
        "and we are confident the pipeline is expanding. We reiterate our record outlook."
    )
    out = ec.score_commentary(text)
    assert out["mgmt_tone"] > 0.6
    assert out["mgmt_tone_signal"] == "positive"
    assert out["n_words"] > 0


def test_score_commentary_bearish_text() -> None:
    text = (
        "We face significant headwinds and remain cautious given macro pressure. "
        "Demand is softening, guidance was lowered, and results came in below plan."
    )
    out = ec.score_commentary(text)
    assert out["mgmt_tone"] < 0.4
    assert out["mgmt_tone_signal"] == "negative"


def test_score_commentary_negation_flips() -> None:
    # "not confident" must not count as bullish.
    text = "We are not confident and demand is not strong this quarter."
    out = ec.score_commentary(text)
    assert out["mgmt_tone_signal"] in {"negative", "neutral"}


def test_score_commentary_empty_is_neutral() -> None:
    out = ec.score_commentary("The meeting is scheduled for Tuesday.")
    assert out["mgmt_tone_signal"] == "neutral"
    assert out["mgmt_tone"] is None


def test_tone_prefers_alphavantage_over_edgar(monkeypatch) -> None:
    monkeypatch.setattr(
        ec,
        "_fetch_alphavantage_quarters",
        lambda t, n=4: [
            {
                "quarter": "2025Q1",
                "text": "We raised guidance, accelerating demand, record momentum.",
                "av_sentiment": 0.4,
            },
        ],
    )
    monkeypatch.setattr(ec, "_fetch_edgar_text", lambda t: "should not be used")

    out = ec.get_commentary_tone("AAPL")
    assert out["source"] == "alpha_vantage"
    assert out["mgmt_tone_signal"] == "positive"
    assert out["ticker"] == "AAPL"


def test_trend_improving_when_latest_tone_above_prior(monkeypatch) -> None:
    # Latest quarter clearly more bullish than the two priors -> improving.
    monkeypatch.setattr(
        ec,
        "_fetch_alphavantage_quarters",
        lambda t, n=4: [
            {
                "quarter": "2025Q1",
                "text": "raised guidance accelerating demand record momentum strong confident expanding",
                "av_sentiment": 0.5,
            },
            {"quarter": "2024Q4", "text": "demand softening headwinds cautious pressure", "av_sentiment": 0.1},
            {"quarter": "2024Q3", "text": "headwinds cautious weak below lowered", "av_sentiment": -0.1},
        ],
    )
    monkeypatch.setattr(ec, "_fetch_edgar_text", lambda t: None)

    out = ec.get_commentary_tone("AAPL")
    assert out["relative"] is True
    assert out["mgmt_tone_trend"] == "improving"
    assert out["tone_delta"] > 0
    assert out["av_sentiment_delta"] > 0
    assert out["quarters_analyzed"] == ["2025Q1", "2024Q4", "2024Q3"]


def test_trend_deteriorating_when_latest_tone_below_prior(monkeypatch) -> None:
    monkeypatch.setattr(
        ec,
        "_fetch_alphavantage_quarters",
        lambda t, n=4: [
            {
                "quarter": "2025Q1",
                "text": "headwinds cautious softening weak below lowered pressure",
                "av_sentiment": -0.2,
            },
            {"quarter": "2024Q4", "text": "raised accelerating record momentum strong confident", "av_sentiment": 0.4},
        ],
    )
    monkeypatch.setattr(ec, "_fetch_edgar_text", lambda t: None)

    out = ec.get_commentary_tone("AAPL")
    assert out["mgmt_tone_trend"] == "deteriorating"
    assert out["tone_delta"] < 0


def test_trend_insufficient_history_single_quarter(monkeypatch) -> None:
    monkeypatch.setattr(
        ec,
        "_fetch_alphavantage_quarters",
        lambda t, n=4: [
            {"quarter": "2025Q1", "text": "raised guidance record momentum strong", "av_sentiment": 0.3},
        ],
    )
    monkeypatch.setattr(ec, "_fetch_edgar_text", lambda t: None)

    out = ec.get_commentary_tone("AAPL")
    assert out["relative"] is False
    assert out["mgmt_tone_trend"] == "insufficient_history"
    assert out["tone_delta"] is None
    assert out["mgmt_tone_signal"] == "positive"  # absolute still reported


def test_tone_falls_back_to_edgar_when_no_transcript(monkeypatch) -> None:
    monkeypatch.setattr(ec, "_fetch_alphavantage_quarters", lambda t, n=4: [])
    monkeypatch.setattr(ec, "_fetch_edgar_text", lambda t: "Headwinds, cautious, guidance lowered, demand softening.")

    out = ec.get_commentary_tone("AAPL")
    assert out["source"] == "edgar_8k"
    assert out["mgmt_tone_signal"] == "negative"
    assert out["relative"] is False  # single doc, no quarter history
    assert out["mgmt_tone_trend"] == "insufficient_history"


def test_tone_returns_unavailable_when_no_source(monkeypatch) -> None:
    monkeypatch.setattr(ec, "_fetch_alphavantage_quarters", lambda t, n=4: [])
    monkeypatch.setattr(ec, "_fetch_edgar_text", lambda t: None)

    out = ec.get_commentary_tone("AAPL")
    assert out["source"] is None
    assert out["mgmt_tone"] is None
    assert "note" in out


def test_relativize_stable_when_tones_close() -> None:
    scored = [
        {"quarter": "2025Q1", "tone": 0.95, "av_sentiment": 0.30},
        {"quarter": "2024Q4", "tone": 0.94, "av_sentiment": 0.31},
    ]
    rel = ec._relativize(scored)
    assert rel["mgmt_tone_trend"] == "stable"
    assert rel["relative"] is True


def test_pick_earnings_filing_prefers_results_description() -> None:
    filings = [
        {"description": "Item 5.02 Departure of Directors", "url": "u_other"},
        {"description": "Q1 2025 Results of Operations and Financial Condition", "url": "u_earnings"},
        {"description": "Item 8.01 Other Events", "url": "u_misc"},
    ]
    pick = ec._pick_earnings_filing(filings)
    assert pick["url"] == "u_earnings"


def test_pick_earnings_filing_falls_back_to_first() -> None:
    filings = [
        {"description": "Item 5.02 Departure of Directors", "url": "u_other"},
        {"description": "Item 8.01 Other Events", "url": "u_misc"},
    ]
    pick = ec._pick_earnings_filing(filings)
    assert pick["url"] == "u_other"  # newest, no earnings match


def test_pick_earnings_filing_empty() -> None:
    assert ec._pick_earnings_filing([]) is None
