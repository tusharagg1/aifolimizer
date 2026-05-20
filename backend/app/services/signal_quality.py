"""Heuristic signal quality scoring adapted from AI-Trader (MIT).

Pure functions — no database. Call score_quality() on any text (recommendation
reasons, news summary, thesis) to get a 0-5 score on five dimensions plus a
weighted overall. Weights from AI-Trader heuristic-v1:
  verifiability 30%, evidence 25%, specificity 20%, novelty 15%, completeness 10%
"""
from __future__ import annotations

import re
from typing import Any

_DIRECTION_UP = frozenset({"buy", "long", "bull", "upside", "breakout", "accumulate"})
_DIRECTION_DOWN = frozenset({"sell", "short", "bear", "downside", "breakdown", "reduce"})
_DIRECTION_FLAT = frozenset({"hold", "neutral", "range", "sideways", "watch"})
_EVIDENCE_WORDS = frozenset({
    "because", "risk", "evidence", "data", "chart", "catalyst",
    "earnings", "revenue", "margin", "volume", "guidance", "macro",
})


def _clamp(v: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return round(max(lo, min(hi, v)), 4)


def extract_prediction(text: str, symbol: str | None = None) -> dict[str, Any]:
    """Parse direction, target price, and probability from free text."""
    lower = text.lower()
    words = set(lower.split())

    direction = None
    if words & _DIRECTION_UP or any(w in lower for w in ("bullish",)):
        direction = "up"
    elif words & _DIRECTION_DOWN or any(w in lower for w in ("bearish",)):
        direction = "down"
    elif words & _DIRECTION_FLAT:
        direction = "flat"

    price_match = re.search(
        r"(?:target|tp|price|upside)\D{0,12}\$?([0-9]+(?:\.[0-9]+)?)",
        text, flags=re.IGNORECASE,
    )
    prob_match = re.search(r"([0-9]{1,3})\s?%", text)
    conf_match = re.search(
        r"(?:confidence|conf)\D{0,12}([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE,
    )

    target_price = float(price_match.group(1)) if price_match else None
    target_prob = None
    if prob_match:
        raw = float(prob_match.group(1))
        if 1 <= raw <= 100:
            target_prob = round(raw / 100.0, 3)
    confidence = None
    if conf_match:
        raw = float(conf_match.group(1))
        confidence = round(raw / 100.0 if raw > 1 else raw, 3)
        confidence = max(0.0, min(1.0, confidence))

    return {
        "symbol": symbol,
        "direction": direction,
        "target_price": target_price,
        "target_probability": target_prob,
        "confidence": confidence,
        "evidence_keywords": [w for w in _EVIDENCE_WORDS if w in lower],
    }


def score_quality(
    text: str,
    symbol: str | None = None,
    existing_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Score text across 5 quality dimensions (each 0–5). Returns sub-scores + weighted overall.

    existing_texts: pass other recently-seen texts to detect near-duplicates (novelty penalty).
    """
    lower = text.lower()
    prediction = extract_prediction(text, symbol)

    verifiability = 1.0
    if prediction["direction"]:
        verifiability += 1.2
    if symbol:
        verifiability += 0.8
    if prediction["target_price"] is not None or prediction["target_probability"] is not None:
        verifiability += 1.2

    evidence = min(5.0, len(text) / 160.0 + len(prediction["evidence_keywords"]) * 0.7)

    has_price_level = bool(
        re.search(r"\$[0-9]+", text) or re.search(r"[0-9]+\.[0-9]{2}", text)
    )
    specificity = (
        1.0
        + (1.0 if symbol else 0.0)
        + (1.0 if has_price_level else 0.0)
        + min(len(text) / 320.0, 2.0)
    )

    duplicate_count = 0
    if existing_texts:
        normalized = " ".join(lower.split())
        for t in existing_texts:
            if " ".join(t.lower().split()) == normalized:
                duplicate_count += 1
    novelty = 5.0 if duplicate_count == 0 else max(0.5, 5.0 - duplicate_count)

    completeness = 1.0
    if any(w in lower for w in ("stop", "stop-loss", "stop loss")):
        completeness += 1.5
    if any(w in lower for w in ("target", "take profit", "tp")):
        completeness += 1.5
    if any(w in lower for w in ("risk/reward", "r/r", "risk reward")):
        completeness += 1.0

    overall = (
        verifiability * 0.30
        + evidence * 0.25
        + specificity * 0.20
        + novelty * 0.15
        + completeness * 0.10
    )

    return {
        "symbol": symbol,
        "verifiability": _clamp(verifiability),
        "evidence": _clamp(evidence),
        "specificity": _clamp(specificity),
        "novelty": _clamp(novelty),
        "completeness": _clamp(completeness),
        "overall": _clamp(overall),
        "duplicate_count": duplicate_count,
        "prediction": prediction,
    }
