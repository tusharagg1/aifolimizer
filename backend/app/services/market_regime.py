"""Market-regime classifier (Phase 8).

Composes existing macro + breadth data into a single composite regime label
+ per-skill multipliers. No new external data sources.

Output dimensions (all best-effort, fall back to neutral on missing data):
  trend:      "up" | "down" | "sideways"   — SPY vs SMA200 + magnitude
  volatility: "low" | "normal" | "high"     — VIX banding
  breadth:    "broad" | "narrow"            — SPY vs SMA200 magnitude (proxy)
  macro:      "risk_on" | "risk_off"        — 10y yield + Fed funds heuristic
  composite:  hyphenated combination like "trend_up_low_vol"

Per-skill multipliers seed `regime_skill_multipliers` table on first call
per composite — initial values are hand-picked baselines. Nightly tuner
(Phase 11) adjusts them once enough samples accumulate per regime bucket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Regime:
    trend: str  # "up" | "down" | "sideways"
    volatility: str  # "low" | "normal" | "high"
    breadth: str  # "broad" | "narrow"
    macro: str  # "risk_on" | "risk_off"
    composite: str
    confidence: float  # 0..1
    vix: float | None = None
    spy_vs_sma200_pct: float | None = None
    ten_y_yield: float | None = None
    fed_funds: float | None = None
    ts: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.ts:
            d["ts"] = self.ts.isoformat()
        return d


# ── Initial per-skill multipliers (Phase 8 baselines) ─────────────────────
# Hand-picked from common-sense regime-strategy fit. Nightly tuner (Phase 11)
# will overwrite once attribution data is available.
#   trend_up_low_vol:   risk-on, momentum favored
#   trend_up_high_vol:  cautious, dividend/defensive lean
#   sideways_low_vol:   mean-revert favored
#   sideways_high_vol:  defensive, reduce all
#   trend_down_high_vol: defensive max
#   trend_down_low_vol: rare; cautious cut

_INITIAL_MULTIPLIERS: dict[str, dict[str, float]] = {
    # cash-deployment (momentum-add)
    "cash-deployment": {
        "trend_up_low_vol": 1.2,
        "trend_up_high_vol": 0.8,
        "sideways_low_vol": 0.8,
        "sideways_high_vol": 0.5,
        "trend_down_high_vol": 0.3,
        "trend_down_low_vol": 0.6,
    },
    # tax-loss-review (mean-revert / harvest)
    "tax-loss-review": {
        "trend_up_low_vol": 0.7,
        "trend_up_high_vol": 1.0,
        "sideways_low_vol": 1.1,
        "sideways_high_vol": 1.2,
        "trend_down_high_vol": 1.4,
        "trend_down_low_vol": 1.3,
    },
    # dividend-strategy (low-beta income)
    "dividend-strategy": {
        "trend_up_low_vol": 0.9,
        "trend_up_high_vol": 1.1,
        "sideways_low_vol": 1.1,
        "sideways_high_vol": 1.2,
        "trend_down_high_vol": 1.3,
        "trend_down_low_vol": 1.2,
    },
    # stock-analysis (Minervini stage-2 momentum)
    "stock-analysis": {
        "trend_up_low_vol": 1.3,
        "trend_up_high_vol": 0.8,
        "sideways_low_vol": 0.7,
        "sideways_high_vol": 0.5,
        "trend_down_high_vol": 0.3,
        "trend_down_low_vol": 0.5,
    },
    # risk-assessment (defensive)
    "risk-assessment": {
        "trend_up_low_vol": 0.9,
        "trend_up_high_vol": 1.2,
        "sideways_low_vol": 1.0,
        "sideways_high_vol": 1.3,
        "trend_down_high_vol": 1.5,
        "trend_down_low_vol": 1.3,
    },
}

_DEFAULT_MULTIPLIER = 1.0


def _classify_trend(spy_vs_sma200_pct: float | None) -> str:
    if spy_vs_sma200_pct is None:
        return "sideways"
    if spy_vs_sma200_pct > 2.0:
        return "up"
    if spy_vs_sma200_pct < -2.0:
        return "down"
    return "sideways"


def _classify_vol(vix: float | None) -> str:
    if vix is None:
        return "normal"
    if vix > 25:
        return "high"
    if vix < 15:
        return "low"
    return "normal"


def _classify_breadth(spy_vs_sma200_pct: float | None) -> str:
    # Proxy: large magnitude = broad participation; small = narrow.
    if spy_vs_sma200_pct is None:
        return "broad"
    return "broad" if abs(spy_vs_sma200_pct) > 5.0 else "narrow"


def _classify_macro(
    fed_funds: float | None,
    ten_y_yield: float | None,
) -> str:
    """Risk-off heuristic: yield curve inversion OR very tight Fed funds.

    No yield curve here (only need 10y + Fed funds); approximate as
    fed_funds >= 10y_yield (rough inversion) OR fed_funds > 5.0.
    """
    if fed_funds is None or ten_y_yield is None:
        return "risk_on"
    if fed_funds >= ten_y_yield:
        return "risk_off"
    if fed_funds > 5.0:
        return "risk_off"
    return "risk_on"


def _composite(trend: str, volatility: str) -> str:
    return f"trend_{trend}_{volatility}_vol".replace("trend_sideways", "sideways")


def classify(
    *,
    vix: float | None,
    spy_vs_sma200_pct: float | None,
    ten_y_yield: float | None = None,
    fed_funds: float | None = None,
) -> Regime:
    """Pure classifier — no I/O. Caller supplies signals."""
    trend = _classify_trend(spy_vs_sma200_pct)
    vol = _classify_vol(vix)
    breadth = _classify_breadth(spy_vs_sma200_pct)
    macro = _classify_macro(fed_funds, ten_y_yield)
    composite = _composite(trend, vol)

    # Confidence = how clean the trend + vol signals are
    confidence = 0.5
    if spy_vs_sma200_pct is not None and abs(spy_vs_sma200_pct) > 5.0:
        confidence += 0.25
    if vix is not None and (vix > 25 or vix < 15):
        confidence += 0.25
    confidence = round(min(1.0, confidence), 2)

    return Regime(
        trend=trend,
        volatility=vol,
        breadth=breadth,
        macro=macro,
        composite=composite,
        confidence=confidence,
        vix=vix,
        spy_vs_sma200_pct=spy_vs_sma200_pct,
        ten_y_yield=ten_y_yield,
        fed_funds=fed_funds,
        ts=datetime.now(tz=timezone.utc),
    )


def fetch_inputs() -> dict[str, float | None]:
    """Best-effort fetch of inputs from existing macro service."""
    out: dict[str, float | None] = {
        "vix": None,
        "spy_vs_sma200_pct": None,
        "ten_y_yield": None,
        "fed_funds": None,
    }
    try:
        from app.services.macro import market_breadth, macro_snapshot

        b = market_breadth() or {}
        out["vix"] = b.get("vix")
        out["spy_vs_sma200_pct"] = b.get("spy_vs_sma200_pct")
        m = macro_snapshot() or {}
        fed = (m.get("fed_funds_rate") or {}).get("value")
        ten = (m.get("10y_yield") or {}).get("value")
        out["fed_funds"] = float(fed) if fed is not None else None
        out["ten_y_yield"] = float(ten) if ten is not None else None
    except Exception as e:
        log.warning("fetch_inputs failed: %s", e)
    return out


def multiplier_for(skill: str, composite: str) -> float:
    """Get the (skill, regime) multiplier.

    Resolution order:
      1. Adaptive multipliers learned from per-(skill, regime) attribution
         (`.cache/regime_multipliers.json` written by adaptive_regime job).
      2. The static `_INITIAL_MULTIPLIERS` table.
      3. `_DEFAULT_MULTIPLIER` (1.0).
    """
    learned = _load_learned_multipliers()
    if learned:
        skill_tbl = learned.get(skill) or {}
        if composite in skill_tbl:
            return float(skill_tbl[composite])
    return _INITIAL_MULTIPLIERS.get(skill, {}).get(
        composite,
        _DEFAULT_MULTIPLIER,
    )


def _load_learned_multipliers() -> dict[str, dict[str, float]] | None:
    """Read JSON written by `adaptive_regime.recalibrate_multipliers`.

    File schema: {"updated_ts": float, "multipliers": {skill: {regime: float}}}.
    Returns the inner `multipliers` dict, or None on first run / missing
    file / decode error so callers fall back to the static table.
    """
    try:
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / ".cache" / "regime_multipliers.json"
        if not path.is_file():
            return None
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            inner = payload.get("multipliers")
            if isinstance(inner, dict):
                return inner
            # Fallback: old flat format (skill → {regime: float}).
            if all(isinstance(v, dict) for v in payload.values()):
                return payload
        return None
    except Exception:
        return None


def initial_multipliers_for(composite: str) -> dict[str, float]:
    """Returns {skill: multiplier} baseline for a given regime."""
    return {skill: table.get(composite, _DEFAULT_MULTIPLIER) for skill, table in _INITIAL_MULTIPLIERS.items()}


# ── Persistence + Redis cache ──────────────────────────────────────────────


async def persist(regime: Regime) -> None:
    """Insert into regime_history + cache Redis regime:current."""
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is not None:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO regime_history (
                      ts, trend, volatility, breadth, macro,
                      composite, confidence,
                      vix, spy_vs_sma200_pct, ten_y_yield, fed_funds
                    ) VALUES (
                      $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
                    )
                    ON CONFLICT (ts) DO NOTHING
                    """,
                    regime.ts,
                    regime.trend,
                    regime.volatility,
                    regime.breadth,
                    regime.macro,
                    regime.composite,
                    regime.confidence,
                    regime.vix,
                    regime.spy_vs_sma200_pct,
                    regime.ten_y_yield,
                    regime.fed_funds,
                )
    except Exception as e:
        log.warning("regime persist (PG) failed: %s", e)

    try:
        from app.cache import get_redis
        import json

        r = get_redis()
        if r is not None:
            await r.set(
                "regime:current",
                json.dumps(regime.to_dict()),
                ex=3600,
            )
    except Exception as e:
        log.warning("regime persist (Redis) failed: %s", e)


async def classify_and_persist() -> Regime:
    """One-shot orchestration: fetch inputs → classify → persist."""
    inputs = fetch_inputs()
    regime = classify(**inputs)
    await persist(regime)
    return regime


async def get_current() -> Regime | None:
    """Best-effort fetch latest regime from Redis (preferred) or PG."""
    try:
        from app.cache import get_redis
        import json

        r = get_redis()
        if r is not None:
            blob = await r.get("regime:current")
            if blob:
                data = json.loads(blob)
                ts_str = data.pop("ts", None)
                ts = datetime.fromisoformat(ts_str) if ts_str else None
                return Regime(ts=ts, **data)
    except Exception:
        pass
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM regime_history ORDER BY ts DESC LIMIT 1")
        if not row:
            return None
        return Regime(
            trend=row["trend"],
            volatility=row["volatility"],
            breadth=row["breadth"],
            macro=row["macro"],
            composite=row["composite"],
            confidence=float(row["confidence"] or 0),
            vix=float(row["vix"]) if row["vix"] else None,
            spy_vs_sma200_pct=float(row["spy_vs_sma200_pct"]) if row["spy_vs_sma200_pct"] else None,
            ten_y_yield=float(row["ten_y_yield"]) if row["ten_y_yield"] else None,
            fed_funds=float(row["fed_funds"]) if row["fed_funds"] else None,
            ts=row["ts"],
        )
    except Exception as e:
        log.warning("get_current regime failed: %s", e)
        return None
