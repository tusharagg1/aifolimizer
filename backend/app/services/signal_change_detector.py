"""Signal change detector (Phase 4).

Compares this tick's integrated signals against the previous tick's snapshot
stored in Redis. Material flips/score-moves are persisted to Postgres
signal_changes and pushed via ntfy.sh.

Material change rules:
  - action flipped (HOLD → BUY, BUY → SELL, etc.) — always material
    EXCEPT noise pairs HOLD↔WATCH and NO_EDGE↔HOLD
  - conviction stepped UP on BUY/SELL (medium → high)
  - score moved ≥ 2.0 points in either direction (large re-rating)

Dedup: per (symbol, new_action, today) — one push per direction per day.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

log = logging.getLogger(__name__)


# Action pairs we ignore as noise — keep ntfy clean.
_NOISE_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"HOLD", "WATCH"}),
    frozenset({"NO_EDGE", "HOLD"}),
    frozenset({"NO_EDGE", "WATCH"}),
})

# Conviction ordering for "stepped up" detection.
_CONVICTION_ORDER = {"low": 0, "medium": 1, "high": 2}

_STRONG_ACTIONS = frozenset({"BUY", "SELL"})


@dataclass
class SignalChange:
    symbol: str
    ts: datetime
    prev_action: str | None
    new_action: str
    prev_conviction: str | None
    new_conviction: str | None
    prev_score: float | None
    new_score: float
    reasons: list[str]
    # Phase 11: optional sizing surface in the push body.
    kelly_pct: float | None = None
    win_prob: float | None = None
    risk_reward: float | None = None

    def dedup_key(self) -> str:
        return f"{self.symbol}:{self.new_action}:{date.today().isoformat()}"

    def ntfy_title(self) -> str:
        prev = self.prev_action or "?"
        conv = (
            f" ({self.new_conviction.upper()})"
            if self.new_conviction else ""
        )
        return f"{self.symbol}: {prev} → {self.new_action}{conv}"

    def ntfy_body(self) -> str:
        ps = self.prev_score if self.prev_score is not None else "?"
        ps_str = f"{ps:.1f}" if isinstance(ps, (int, float)) else str(ps)
        reasons = " · ".join(self.reasons[:3])
        base = f"Score {ps_str} → {self.new_score:.1f}. {reasons}"
        if (self.kelly_pct or 0) > 0:
            sizing = f" · Kelly {self.kelly_pct:.1f}%"
            if self.risk_reward:
                sizing += f", R:R {self.risk_reward:.1f}"
            base += sizing
        return base

    def ntfy_priority(self) -> str:
        # 1 = lowest, 5 = highest. ntfy uses string names too.
        if self.new_action == "BUY" and self.new_conviction == "high":
            return "high"
        if self.new_action == "SELL":
            return "high"
        return "default"

    def ntfy_tags(self) -> str:
        if self.new_action in ("BUY", "ADD"):
            return "chart_with_upwards_trend"
        if self.new_action in ("SELL", "TRIM"):
            return "chart_with_downwards_trend"
        return "bell"


def _is_noise(prev_action: str | None, new_action: str) -> bool:
    if prev_action is None:
        return False
    pair = frozenset({prev_action, new_action})
    return pair in _NOISE_PAIRS


def _detect_one(
    prev: dict | None, new: dict, ts: datetime,
) -> SignalChange | None:
    """Compare one symbol's prev vs new signal. Return Change or None."""
    new_action = (new.get("action") or "HOLD").upper()
    new_score = float(new.get("score") or 0)
    new_conviction = (new.get("conviction") or "").lower() or None
    kelly = new.get("kelly_pct")
    win_prob = new.get("win_prob")
    rr = new.get("risk_reward")

    if prev is None:
        # First-ever signal — only material if it's a strong action.
        if new_action in _STRONG_ACTIONS:
            return SignalChange(
                symbol=new["symbol"],
                ts=ts,
                prev_action=None,
                new_action=new_action,
                prev_conviction=None,
                new_conviction=new_conviction,
                prev_score=None,
                new_score=new_score,
                reasons=["first observation"],
                kelly_pct=kelly, win_prob=win_prob, risk_reward=rr,
            )
        return None

    prev_action = (prev.get("action") or "HOLD").upper()
    prev_score = float(prev.get("score") or 0)
    prev_conviction = (prev.get("conviction") or "").lower() or None

    reasons: list[str] = []
    is_change = False

    if prev_action != new_action and not _is_noise(prev_action, new_action):
        is_change = True
        reasons.append(f"action {prev_action}→{new_action}")

    if (
        new_action in _STRONG_ACTIONS
        and prev_conviction != new_conviction
        and _CONVICTION_ORDER.get(new_conviction or "", -1)
            > _CONVICTION_ORDER.get(prev_conviction or "", -1)
    ):
        is_change = True
        reasons.append(f"conviction {prev_conviction}→{new_conviction}")

    if abs(new_score - prev_score) >= 2.0:
        is_change = True
        direction = "+" if new_score > prev_score else "-"
        reasons.append(
            f"score {direction}{abs(new_score - prev_score):.1f}pt"
        )

    if not is_change:
        return None

    return SignalChange(
        symbol=new["symbol"],
        ts=ts,
        prev_action=prev_action,
        new_action=new_action,
        prev_conviction=prev_conviction,
        new_conviction=new_conviction,
        prev_score=prev_score,
        new_score=new_score,
        reasons=reasons,
        kelly_pct=kelly, win_prob=win_prob, risk_reward=rr,
    )


def detect_changes(
    prev_map: dict[str, dict],
    new_signals: Iterable[dict],
    ts: datetime | None = None,
) -> list[SignalChange]:
    """Compute material changes. Pure function — no I/O.

    Args:
      prev_map: { symbol: prev_signal_dict }
      new_signals: iterable of current signal dicts (must contain `symbol`)
      ts: timestamp for change records (defaults to utcnow)
    """
    ts = ts or datetime.now(tz=timezone.utc)
    out: list[SignalChange] = []
    for s in new_signals:
        sym = s.get("symbol")
        if not sym:
            continue
        chg = _detect_one(prev_map.get(sym), s, ts)
        if chg is not None:
            out.append(chg)
    return out


async def detect_and_dispatch(
    tenant_hash: str,
    new_signals: list[dict],
    *,
    ntfy_topic: str | None = None,
) -> dict:
    """Full pipeline:
      1. read prev_map from Redis last_signals:{tenant_hash}
      2. detect material changes
      3. for each change: dedup-check against signal_changes, push ntfy, insert row
      4. overwrite last_signals:{tenant_hash} with new_signals

    Returns {detected, pushed, deduped}.
    """
    from app.cache import get_redis
    from app.db.repositories import changes_repo

    r = get_redis()
    prev_map: dict[str, dict] = {}
    if r is not None:
        try:
            raw = await r.get(f"last_signals:{tenant_hash}")
            if raw:
                prev_map = json.loads(raw)
        except Exception as e:
            log.warning("change_detector: redis read failed: %s", e)

    changes = detect_changes(prev_map, new_signals)
    pushed = 0
    deduped = 0

    if changes and ntfy_topic:
        # Local import to avoid pulling alerts.py into module-import cost
        # when ntfy is disabled.
        from app.services.alerts import _push_ntfy
        for ch in changes:
            dedup_key = ch.dedup_key()
            try:
                if await changes_repo.dedup_exists(dedup_key):
                    deduped += 1
                    continue
                _push_ntfy(
                    topic=ntfy_topic,
                    title=ch.ntfy_title(),
                    body=ch.ntfy_body(),
                    priority=ch.ntfy_priority(),
                    tags=ch.ntfy_tags(),
                )
                pushed += 1
                await changes_repo.insert(
                    tenant_hash=tenant_hash,
                    symbol=ch.symbol,
                    ts=ch.ts,
                    prev_action=ch.prev_action,
                    new_action=ch.new_action,
                    prev_conviction=ch.prev_conviction,
                    new_conviction=ch.new_conviction,
                    prev_score=ch.prev_score,
                    new_score=ch.new_score,
                    reasons=ch.reasons,
                    pushed=True,
                    dedup_key=dedup_key,
                )
            except Exception as e:
                log.exception(
                    "change_detector: dispatch failed for %s: %s",
                    ch.symbol, e,
                )

    # Update last_signals snapshot for next tick.
    if r is not None:
        try:
            snapshot = {
                s["symbol"]: {
                    "action": s.get("action"),
                    "conviction": s.get("conviction"),
                    "score": s.get("score"),
                }
                for s in new_signals if s.get("symbol")
            }
            await r.set(
                f"last_signals:{tenant_hash}",
                json.dumps(snapshot),
            )
        except Exception as e:
            log.warning("change_detector: redis write failed: %s", e)

    return {
        "detected": len(changes),
        "pushed": pushed,
        "deduped": deduped,
    }
