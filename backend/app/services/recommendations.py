"""Multi-signal recommendation engine.

Scores each position using four independent sub-signals:
  tech_score    — Minervini stage, RSI, MACD, trend (volatile, intraday)
  fund_score    — analyst targets, EPS, short interest (stable, 6h cache)
  macro_score   — market regime, VIX, Fear & Greed (1h cache)
  sentiment     — Google News RSS headline polarity (30m cache)

Action is only escalated to BUY or SELL when sub-signals CONVERGE.
Conflicting signals cap at WATCH, preventing the BUY→SELL flip seen
when a single volatile indicator (MACD histogram, RSI) crosses a threshold.

Output is cached 30 minutes — eliminates per-page-load recalculation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import time
import xml.etree.ElementTree as ET

import httpx

from app.services import technicals as tech_svc
from app.services import fundamentals as fund_svc
from app.services.community import score_text_polarity
from app.services.fundamentals import get_earnings_expected_moves
from app.services.macro import market_breadth
from app.services.signal_quality import score_quality
from app.services import signal_history
from app.services import paper_trade

_log = logging.getLogger(__name__)


_SECTOR_ETF_MAP = {
    "technology": "XLK",
    "information technology": "XLK",
    "financials": "XLF",
    "financial services": "XLF",
    "healthcare": "XLV",
    "health care": "XLV",
    "consumer discretionary": "XLY",
    "consumer cyclical": "XLY",
    "consumer staples": "XLP",
    "consumer defensive": "XLP",
    "energy": "XLE",
    "industrials": "XLI",
    "utilities": "XLU",
    "real estate": "XLRE",
    "basic materials": "XLB",
    "materials": "XLB",
    "communication services": "XLC",
    "communications": "XLC",
}


def _sector_etf_for(sector: str | None) -> str | None:
    if not sector:
        return None
    return _SECTOR_ETF_MAP.get(sector.strip().lower())


_ACTION_ORDER = {"SELL": 0, "TRIM": 1, "BUY": 2, "WATCH": 3, "HOLD": 4, "NO_EDGE": 5}

_REGIME_BUY_HOSTILE = frozenset({"bear_high_fear", "bear_low_fear", "unknown"})
_REGIME_SELL_HOSTILE = frozenset({"bull_low_fear"})

_ETF_ASSET_CLASSES = {"etf", "index", "mutual_fund"}

# ── Output cache ───────────────────────────────────────────────────────────────
_REC_CACHE: dict[str, tuple[list, float]] = {}
_REC_CACHE_TTL = 1800  # 30 minutes

# ── Sub-signal weights (Phase 2). Default mirrors v3 baseline.
# Phase 5 overwrites this from the `weights` Postgres table.
_WEIGHTS_CACHE: dict[str, float] = {
    "w_tech": 1.0,
    "w_fund": 1.0,
    "w_macro": 1.0,
    "w_sentiment": 1.0,
    "w_skill": 0.5,
}
_WEIGHTS_CACHE_TS: float = 0.0
_WEIGHTS_CACHE_TTL = 300  # 5 minutes — short to react to nightly tuner


def _load_weights() -> dict[str, float]:
    """Best-effort fetch latest weights row from Postgres.

    Falls back to in-memory defaults if pool unavailable. Cached 5 min.
    """
    global _WEIGHTS_CACHE_TS
    if time.time() - _WEIGHTS_CACHE_TS < _WEIGHTS_CACHE_TTL:
        return _WEIGHTS_CACHE
    try:
        # Lazy import — avoid circular dep at module load.
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return _WEIGHTS_CACHE
        # Sync usage from a sync caller — run a brief asyncio.run if no loop.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already inside an event loop; skip refresh this tick.
                return _WEIGHTS_CACHE
        except RuntimeError:
            # No running loop in this thread — fall through and refresh synchronously.
            pass

        async def _q() -> dict | None:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT w_tech, w_fund, w_macro, w_sentiment, w_skill FROM weights ORDER BY version DESC LIMIT 1"
                )
            return dict(row) if row else None

        row = asyncio.run(_q())
        if row:
            _WEIGHTS_CACHE.update({k: float(v) for k, v in row.items()})
            _WEIGHTS_CACHE_TS = time.time()
    except Exception:
        # Silent — sub-signal weighting is best-effort; defaults still valid.
        _log.debug("suppressed exception", exc_info=True)
    return _WEIGHTS_CACHE


# ── News sentiment cache ───────────────────────────────────────────────────────
_SENT_CACHE: dict[str, tuple[float, float]] = {}
_SENT_TTL = 1800  # 30 minutes

_POSITIVE = frozenset(
    {
        "beat",
        "beats",
        "raises",
        "surges",
        "gains",
        "strong",
        "upgrade",
        "upgrades",
        "bullish",
        "record",
        "tops",
        "exceeds",
        "rally",
        "soars",
        "positive",
        "better",
        "outperforms",
        "boosts",
        "grows",
        "growth",
    }
)
_NEGATIVE = frozenset(
    {
        "miss",
        "misses",
        "falls",
        "drops",
        "weak",
        "downgrade",
        "downgrades",
        "bearish",
        "loss",
        "warns",
        "cut",
        "cuts",
        "disappoints",
        "below",
        "negative",
        "worse",
        "concern",
        "plunges",
        "slumps",
        "selloff",
        "layoffs",
    }
)


def _try_llm_sentiment(symbol: str, headlines: list[str]) -> float | None:
    try:
        from app.services.llm_router import score_news_sentiment

        return asyncio.run(score_news_sentiment(symbol, headlines))
    except Exception:
        return None


def _fetch_sentiment(symbol: str) -> float:
    """News headline polarity via LLM (falls back to keyword). Returns -1.0 to +1.0."""
    try:
        url = f"https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
        resp = httpx.get(url, timeout=5.0, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(resp.text)
        raw_titles = [item.findtext("title") or "" for item in root.iter("item")][:15]
        if not raw_titles:
            return 0.0
        llm_score = _try_llm_sentiment(symbol, raw_titles)
        if llm_score is not None:
            return llm_score
        # Negation-aware token scan: counts whole tokens only and flips polarity
        # when preceded by "not"/"no"/etc — "not bullish" → bear, not bull.
        pos = neg = 0
        for title in raw_titles:
            p, n = score_text_polarity(title, _POSITIVE, _NEGATIVE)
            pos += p
            neg += n
        total = pos + neg
        return round((pos - neg) / total, 2) if total > 0 else 0.0
    except Exception:
        return 0.0


def _get_sentiment(symbol: str) -> float:
    entry = _SENT_CACHE.get(symbol)
    if entry and time.time() - entry[1] < _SENT_TTL:
        return entry[0]
    score = _fetch_sentiment(symbol)
    _SENT_CACHE[symbol] = (score, time.time())
    return score


def _direction(score: float, threshold: float = 0.4) -> int:
    """Convert a sub-score to directional signal: +1 bullish, -1 bearish, 0 neutral."""
    if score >= threshold:
        return 1
    if score <= -threshold:
        return -1
    return 0


def _score_position(
    symbol: str,
    position: dict,
    tech: dict,
    fund: dict,
    macro: dict,
    sentiment: float,
    earnings_move: dict | None = None,
    skill_evidence: dict | None = None,
) -> dict:
    asset_class = (position.get("asset_class") or "").lower()
    is_etf = asset_class in _ETF_ASSET_CLASSES

    em = earnings_move or {}

    tech_score = 0.0
    fund_score = 0.0
    macro_score = 0.0
    reasons: list[str] = []
    flags: list[str] = []

    # ── Technical sub-score ────────────────────────────────────────────────────
    stage = tech.get("stage")
    minervini_score = tech.get("minervini_score") or 0

    if stage == 2:
        tech_score += 2.0
        reasons.append("Minervini stage 2 — price above rising SMA200 (uptrend)")
    elif stage == 4:
        tech_score -= 2.0
        reasons.append("Minervini stage 4 — below falling SMA200 (decline)")
    elif stage == 3:
        tech_score -= 0.5
        reasons.append("Stage 3 distribution — SMA200 flattening, trend weakening")
    elif stage == 1:
        tech_score += 0.5
        reasons.append("Stage 1 basing — SMA200 rising, potential accumulation")

    if minervini_score >= 6:
        tech_score += 0.5
        reasons.append(f"Strong Minervini template — {minervini_score}/7 criteria met")
    elif minervini_score <= 2 and stage is not None:
        tech_score -= 0.3

    rsi = tech.get("rsi_14")
    if rsi is not None:
        if 40 <= rsi <= 65:
            tech_score += 0.4
            reasons.append(f"RSI {rsi:.0f} — healthy momentum, not extended")
        elif rsi > 75:
            tech_score -= 0.7
            reasons.append(f"RSI {rsi:.0f} — overbought, elevated pullback risk")
            flags.append("overbought")
        elif rsi > 70:
            tech_score -= 0.3
            reasons.append(f"RSI {rsi:.0f} — approaching overbought territory")
        elif rsi < 30:
            tech_score += 0.4
            reasons.append(f"RSI {rsi:.0f} — oversold, mean-reversion potential")

    macd_hist = tech.get("macd_hist")
    if macd_hist is not None:
        # Reduced weight vs old ±0.5 — MACD histogram is the most volatile signal
        if macd_hist > 0:
            tech_score += 0.3
            reasons.append("MACD histogram positive — bullish momentum")
        else:
            tech_score -= 0.3
            reasons.append("MACD histogram negative — bearish momentum")

    trend = tech.get("trend")
    if trend == "uptrend":
        tech_score += 0.5
        reasons.append("Price above SMA200 — long-term uptrend intact")
    elif trend == "downtrend":
        tech_score -= 0.5
        reasons.append("Price below SMA200 — long-term downtrend")

    pct_from_52w_high = tech.get("pct_from_52w_high")
    if pct_from_52w_high is not None and pct_from_52w_high < -30 and stage != 4:
        tech_score += 0.3
        reasons.append(f"{abs(pct_from_52w_high):.0f}% off 52-week high — deep value entry zone")

    # ── Fundamental sub-score (skip for index ETFs) ────────────────────────────
    if not is_etf and fund:
        analyst_rec = (fund.get("analyst_recommendation") or "").lower()
        if analyst_rec in ("buy", "strong_buy"):
            fund_score += 1.0
            reasons.append(f"Analyst consensus: {analyst_rec.replace('_', ' ').title()}")
        elif analyst_rec in ("sell", "underperform"):
            fund_score -= 1.0
            reasons.append(f"Analyst consensus: {analyst_rec.replace('_', ' ').title()}")

        current_price = tech.get("current_price")
        target = fund.get("analyst_target_price")
        upside_pct: float | None = None
        if current_price and target and current_price > 0:
            upside_pct = round((target - current_price) / current_price * 100, 1)
            if upside_pct > 20:
                fund_score += 1.0
                reasons.append(f"Analyst target +{upside_pct:.0f}% upside ({_fmt_price(target)})")
            elif upside_pct > 10:
                fund_score += 0.5
                reasons.append(f"Analyst target +{upside_pct:.0f}% upside ({_fmt_price(target)})")
            elif upside_pct < -10:
                fund_score -= 1.0
                reasons.append(f"Analyst target implies {upside_pct:.0f}% downside")

        eps_growth = fund.get("eps_growth_yoy")
        if eps_growth is not None:
            if eps_growth > 0.20:
                fund_score += 0.5
                reasons.append(f"EPS +{eps_growth * 100:.0f}% YoY — strong earnings momentum")
            elif eps_growth < 0:
                fund_score -= 0.5
                reasons.append(f"EPS declining {eps_growth * 100:.0f}% YoY — earnings headwind")

        short_int = fund.get("short_interest")
        if short_int is not None:
            if short_int > 0.15:
                fund_score -= 0.5
                reasons.append(f"High short interest {short_int * 100:.0f}% — elevated bearish pressure")
                flags.append("high_short_interest")
            elif short_int < 0.03:
                fund_score += 0.3
                reasons.append(f"Low short interest {short_int * 100:.0f}% — minimal bearish overhang")

        revenue_growth = fund.get("revenue_growth_yoy")
        if revenue_growth is not None and revenue_growth > 0.15:
            fund_score += 0.3
            reasons.append(f"Revenue +{revenue_growth * 100:.0f}% YoY — top-line growth")

    # ── Macro sub-score ────────────────────────────────────────────────────────
    market_regime = macro.get("market_regime") or "unknown"
    if market_regime == "bear_high_fear":
        macro_score -= 1.0
        reasons.append("Bear market + elevated fear — defensive positioning favored")
    elif market_regime == "bull_high_fear":
        macro_score -= 0.25
        reasons.append("Fear spike in bull trend — caution, potential pullback")
    elif market_regime == "bear_low_fear":
        macro_score -= 0.5
        reasons.append("Bear trend with complacency — volatility expansion risk")

    vix = macro.get("vix")
    if vix and vix > 30:
        macro_score -= 0.5
        reasons.append(f"VIX {vix:.0f} — extreme fear, risk-off environment")

    fg_score = macro.get("fear_greed_score")
    if fg_score is not None:
        if fg_score >= 75:
            macro_score -= 0.4
            reasons.append(f"Fear & Greed {fg_score:.0f} (Extreme Greed) — market euphoria risk")
        elif fg_score <= 25:
            macro_score += 0.3
            reasons.append(f"Fear & Greed {fg_score:.0f} (Extreme Fear) — contrarian buy signal")

    # Yield curve inversion — strongest macro recession signal (leads by 6-18 months)
    yc_signal = macro.get("yield_curve_signal")
    if yc_signal == "deeply_inverted":
        macro_score -= 0.8
        spread_val = macro.get("yield_curve_spread", 0)
        reasons.append(f"Yield curve deeply inverted ({spread_val:+.2f}%) — recession warning, reduce risk")
    elif yc_signal == "inverted":
        macro_score -= 0.4
        reasons.append("Yield curve inverted — elevated recession risk over next 12–18 months")

    # ── Sentiment sub-score ────────────────────────────────────────────────────
    if sentiment > 0.3:
        reasons.append(f"News sentiment positive ({sentiment:+.2f}) — recent headlines bullish")
    elif sentiment < -0.3:
        reasons.append(f"News sentiment negative ({sentiment:+.2f}) — recent headlines bearish")

    # ── Position management (applied directly to final score) ─────────────────
    weight = position.get("weight") or 0
    overweight_penalty = 0.0
    if weight > 20:
        overweight_penalty = -0.5
        reasons.append(f"Overweight {weight:.0f}% of portfolio — consider trimming")
        flags.append("overweight")

    total_return = position.get("total_return_pct") or 0
    loss_penalty = 0.0
    if total_return < -25:
        loss_penalty = -0.25
        reasons.append(f"Down {abs(total_return):.0f}% — review stop-loss")

    # ── Skill evidence (Phase 2 — 5th sub-signal) ─────────────────────────────
    # Skills augment, never replace the 4 quantitative sub-signals. They vote
    # in the convergence gate ONLY when skill_confidence ≥ 0.5 (enough skills
    # ran). Contribution to raw_score is clamped to ±2 so a runaway skill
    # consensus cannot dominate quantitative signals.
    weights = _load_weights()
    w_skill = weights.get("w_skill", 0.5)
    skill_evidence = skill_evidence or {}
    skill_consensus = int(skill_evidence.get("skill_consensus") or 0)
    skill_confidence = float(skill_evidence.get("skill_confidence") or 0.0)
    skill_score = max(-2.0, min(2.0, (skill_consensus / 4.0) * w_skill))
    skill_dir = _direction(skill_score, 0.25) if skill_confidence >= 0.5 else 0
    if skill_consensus != 0 and skill_confidence >= 0.5:
        reasons.append(f"Skills consensus {skill_consensus:+d} (confidence {skill_confidence:.0%}, w={w_skill:.2f})")

    # ── Signal convergence → confidence ───────────────────────────────────────
    tech_dir = _direction(tech_score, 0.5)
    fund_dir = _direction(fund_score, 0.5) if not is_etf else 0
    macro_dir = _direction(macro_score, 0.3)
    sent_dir = _direction(sentiment, 0.3)

    # 4-of-5 gate: skill_dir included only when confidence ≥ 0.5 (handled above)
    primary_dirs = [d for d in [tech_dir, fund_dir, macro_dir, sent_dir, skill_dir] if d != 0]
    if primary_dirs:
        dominant = max(set(primary_dirs), key=primary_dirs.count)
        confirming = primary_dirs.count(dominant)
        conflicting = primary_dirs.count(-dominant)
    else:
        dominant, confirming, conflicting = 0, 0, 0

    # Updated thresholds for 5-signal gate: HIGH = 4-of-5 with no conflict,
    # LOW = conflicts >= confirming, otherwise MEDIUM. Legacy 4-of-4 path
    # still triggers HIGH when skill is absent and 3 of 4 align cleanly.
    if confirming >= 4 and conflicting == 0:
        confidence = "high"
    elif confirming >= 3 and conflicting == 0:
        confidence = "high"  # backwards-compatible with 4-signal HIGH
    elif conflicting >= confirming:
        confidence = "low"
    else:
        confidence = "medium"

    # ── Composite score ────────────────────────────────────────────────────────
    # Scale sub-scores: tech(-4→+4), fund(-3→+3), macro(-2→+2), sentiment(-1→+1)
    # + skill(-2→+2, clamped above). Weighted by w_* loaded above.
    w_tech = weights.get("w_tech", 1.0)
    w_fund = weights.get("w_fund", 1.0)
    w_macro = weights.get("w_macro", 1.0)
    w_sent = weights.get("w_sentiment", 1.0)
    raw_score = (
        5.0
        + tech_score * w_tech
        + fund_score * w_fund
        + (sentiment * 0.8) * w_sent
        + macro_score * w_macro
        + skill_score  # already weighted by w_skill above
        + overweight_penalty
        + loss_penalty
    )
    score = max(0.0, min(10.0, round(raw_score, 1)))

    # ── Action with NO_EDGE / convergence / adversarial / regime gates ────────
    # Strict accuracy-first decision rules. BUY requires 3-of-4 sub-signal
    # convergence with no conflicts; SELL requires explicit deterioration
    # (stage 3/4 + weak fundamentals). Otherwise downgrade to TRIM/WATCH/
    # NO_EDGE. This trades signal frequency for precision.
    primary_count = confirming + conflicting
    sq_overall = score_quality(" ".join(reasons), symbol=symbol)["overall"]

    action, gate_reason = _decide_action(
        score=score,
        primary_count=primary_count,
        confirming=confirming,
        conflicting=conflicting,
        dominant_dir=dominant,
        stage=stage,
        fund_score=fund_score,
        macro_score=macro_score,
        signal_quality=sq_overall,
        market_regime=market_regime,
        is_etf=is_etf,
    )
    if gate_reason:
        reasons.append(gate_reason)

    current_price = tech.get("current_price")
    analyst_target = fund.get("analyst_target_price") if fund else None
    upside: float | None = None
    if current_price and analyst_target and current_price > 0:
        upside = round((analyst_target - current_price) / current_price * 100, 1)

    # ── Entry / exit / stop-loss levels ───────────────────────────────────────
    sma50 = tech.get("sma_50")
    stop_loss: float | None = None
    stop_type: str | None = None
    take_profit: float | None = None
    risk_reward: float | None = None

    if current_price and current_price > 0:
        if action == "SELL":
            # SELL stop = where bear thesis breaks (price recovers above here)
            # SMA50 above current = reclaim of SMA50 invalidates downtrend
            if sma50 and sma50 > current_price:
                stop_loss = round(sma50, 2)
                stop_type = "SMA50"
            else:
                stop_loss = round(current_price * 1.08, 2)
                stop_type = "+8%"
            # SELL target = analyst downside target or -15%
            if analyst_target and analyst_target < current_price:
                take_profit = round(analyst_target, 2)
            else:
                take_profit = round(current_price * 0.85, 2)
        else:
            # BUY/HOLD/WATCH stop = floor below current price
            hard_stop = round(current_price * 0.92, 2)
            if sma50 and sma50 < current_price and sma50 > hard_stop:
                stop_loss = round(sma50, 2)
                stop_type = "SMA50"
            else:
                stop_loss = hard_stop
                stop_type = "-8%"
            # BUY target = analyst upside target or +15%
            if analyst_target and analyst_target > current_price:
                take_profit = round(analyst_target, 2)
            else:
                take_profit = round(current_price * 1.15, 2)

    if current_price and stop_loss and take_profit:
        if action == "SELL" and stop_loss > current_price > take_profit:
            risk = stop_loss - current_price
            reward = current_price - take_profit
            risk_reward = round(reward / risk, 1) if risk > 0 else None
        elif action != "SELL" and current_price > stop_loss:
            reward = take_profit - current_price
            risk = current_price - stop_loss
            risk_reward = round(reward / risk, 1) if risk > 0 else None

    # Entry timing: flag if RSI extended — better to wait for pullback
    entry_timing: str
    if rsi and rsi > 65 and action in ("BUY", "ADD"):
        entry_timing = "wait_pullback"
    else:
        entry_timing = "acceptable"

    # ── Position sizing — Kelly GATED on calibration ──────────────────────────
    # win_prob is an indicative heuristic interpolated symmetrically from score.
    # The old analyst_rec→win_prob map (buy=0.62 / sell=0.35) was INVERTED vs
    # realized outcomes (analyst-buy names realized ~18% win, sell ~94%) and is
    # removed. Kelly sizing is SUPPRESSED until confidence labels calibrate
    # against realized returns — an uncalibrated win_prob must never drive bet
    # size. kelly_pct stays None (EV/max-loss cascade off it) until then.
    # Interpolate from score: score=5 → 45%, score=10 → 65%.
    win_prob = min(0.65, max(0.35, 0.35 + (score / 10) * 0.30))

    kelly_pct: float | None = None
    _sizing_calibrated = _evidence_context().get("calibrated", False)
    if _sizing_calibrated and risk_reward and risk_reward > 0:
        b = risk_reward
        p = win_prob
        q = 1.0 - p
        full_kelly = (b * p - q) / b
        kelly_pct = round(min(20.0, max(0.0, full_kelly / 2) * 100), 1)  # half-Kelly

    # ── Expected Value in dollars ─────────────────────────────────────────────
    position_value = position.get("market_value_cad") or 0
    ev_dollars: float | None = None
    max_loss_dollars: float | None = None

    if kelly_pct and risk_reward and position_value > 0:
        bet_size = position_value * (kelly_pct / 100)
        gain = bet_size * risk_reward
        loss = bet_size
        ev_dollars = round(win_prob * gain - (1 - win_prob) * loss, 2)

    # Max loss: how many dollars lost if stop is hit on Kelly-sized position
    if stop_loss and current_price and kelly_pct and position_value > 0:
        if action == "SELL" and stop_loss > current_price:
            stop_gap_pct = (stop_loss - current_price) / current_price
        elif action != "SELL" and current_price > stop_loss:
            stop_gap_pct = (current_price - stop_loss) / current_price
        else:
            stop_gap_pct = 0.0
        if stop_gap_pct > 0:
            max_loss_dollars = round(position_value * (kelly_pct / 100) * stop_gap_pct, 2)

    # HOLD/NO_EDGE: no trade levels — showing them implies action
    if action in ("HOLD", "NO_EDGE"):
        stop_loss = None
        stop_type = None
        take_profit = None
        risk_reward = None
        kelly_pct = None
        ev_dollars = None
        max_loss_dollars = None
    # TRIM: already in position, stop_loss is still useful; target/RR are not
    elif action == "TRIM":
        take_profit = None
        risk_reward = None
        kelly_pct = None
        ev_dollars = None

    # ── Signal quality score ──────────────────────────────────────────────────
    # Scored on the full reasons text so quality reflects thesis depth/specificity
    reasons_text = " ".join(reasons) + (
        f" stop {stop_loss} target {take_profit} risk/reward {risk_reward}" if stop_loss and take_profit else ""
    )
    sq = score_quality(reasons_text, symbol=symbol)

    # ── Hedge flag ────────────────────────────────────────────────────────────
    # Fires when position has elevated binary or reversal risk
    hedge_flag = False
    hedge_reason: str | None = None
    if em and em.get("days_to_earnings") is not None and em.get("days_to_earnings", 99) <= 14:
        if weight > 5:
            hedge_flag = True
            hedge_reason = f"Earnings in {em['days_to_earnings']}d with >{weight:.0f}% weight — consider trimming or protective put"
    elif stage == 3 and weight > 8:
        hedge_flag = True
        hedge_reason = f"Stage 3 distribution + {weight:.0f}% weight — trim risk before trend breaks"
    elif rsi and rsi > 78 and weight > 5:
        hedge_flag = True
        hedge_reason = f"RSI {rsi:.0f} overbought with {weight:.0f}% weight — consider partial trim"

    # ── Earnings risk flag ────────────────────────────────────────────────────
    earnings_risk: str | None = None
    days_to_earnings: int | None = em.get("days_to_earnings")
    expected_move_pct: float | None = em.get("expected_move_pct")
    if days_to_earnings is not None and expected_move_pct is not None:
        position_at_risk = round(position_value * expected_move_pct / 100, 0) if position_value else None
        if days_to_earnings <= 7:
            earnings_risk = "imminent"
            reasons_entry = f"Earnings in {days_to_earnings}d — options imply ±{expected_move_pct}%" + (
                f" (${position_at_risk:,.0f} at risk)" if position_at_risk else ""
            )
        elif days_to_earnings <= 21:
            earnings_risk = "upcoming"
            reasons_entry = f"Earnings in {days_to_earnings}d — implied move ±{expected_move_pct}%"
        else:
            earnings_risk = None
            reasons_entry = None
        if earnings_risk and reasons_entry:
            reasons.insert(0, reasons_entry)  # bump to top of reasons list
            if earnings_risk == "imminent" and action == "BUY":
                entry_timing = "wait_pullback"  # don't enter right before binary event

    _ev = _evidence_context()
    # Conviction decouple: HIGH tracked bullish/analyst convergence → mapped to the
    # negative-EV long leg (HIGH realized 23.6% win vs MED 74.1%). Until calibration
    # proves the HIGH>MED>LOW ordering, cap long-side conviction at medium so an
    # un-earned HIGH label cannot drive sizing/trust on the losing BUY bucket.
    # Self-reverts once _evidence_context reports calibrated=True.
    if action in ("BUY", "ADD") and confidence == "high" and not _ev.get("calibrated"):
        confidence = "medium"
    evidence_tier, evidence_note = _evidence_tier(action, confidence, _ev["forward_n"], _ev["calibrated"], ev_dollars)

    return {
        "symbol": symbol,
        "name": position.get("name") or symbol,
        "currency": str(position.get("currency") or "USD").upper(),
        "action": action,
        "score": score,
        "confidence": confidence,
        "evidence_tier": evidence_tier,
        "evidence_note": evidence_note,
        "reasons": reasons[:5],
        "flags": flags,
        "asset_class": position.get("asset_class") or "",
        "weight": weight,
        "total_return_pct": total_return,
        "current_price": current_price,
        "analyst_target": analyst_target,
        "analyst_upside_pct": upside,
        "stage": stage,
        "rsi": rsi,
        "market_regime": market_regime,
        "sentiment": sentiment,
        # Sub-scores
        "tech_score": round(tech_score, 2),
        "fund_score": round(fund_score, 2),
        "macro_score": round(macro_score, 2),
        # Actionable trade levels
        "stop_loss": stop_loss,
        "stop_type": stop_type,
        "take_profit": take_profit,
        "risk_reward": risk_reward,
        "entry_timing": entry_timing,
        "kelly_pct": kelly_pct,
        # Sizing/EV are indicative only: win_prob is a heuristic, not yet
        # calibrated from realized outcomes — do not treat as precise.
        "sizing_basis": "indicative_heuristic",
        # Expected value
        "ev_dollars": ev_dollars,
        "win_prob": round(win_prob, 3),
        "win_prob_basis": "heuristic",
        "max_loss_dollars": max_loss_dollars,
        # Earnings
        "days_to_earnings": days_to_earnings,
        "expected_move_pct": expected_move_pct,
        "earnings_risk": earnings_risk,
        # Hedge flag
        "hedge_flag": hedge_flag,
        "hedge_reason": hedge_reason,
        # Signal quality (AI-Trader heuristic-v1): 0-5 overall, sub-scores available
        "signal_quality": sq["overall"],
        "signal_quality_detail": {
            "verifiability": sq["verifiability"],
            "evidence": sq["evidence"],
            "specificity": sq["specificity"],
            "novelty": sq["novelty"],
            "completeness": sq["completeness"],
            "direction": sq["prediction"]["direction"],
        },
    }


# ── Evidence tiering ─────────────────────────────────────────────────────────
# Honest separation: a recommendation is only "proven" once enough closed
# forward signals exist AND probabilities are calibrated. Until then it is
# experimental, no matter how cleanly today's sub-signals converge. This stops
# in-sample backtests + live convergence from masquerading as realized edge.
_MIN_FORWARD_PROVEN = 100
_MIN_FORWARD_THESIS = 30
_CALIBRATION_HORIZON = 21
_EVIDENCE_TTL = 300
_EVIDENCE_CACHE: tuple[dict, float] | None = None


def _evidence_context() -> dict:
    """Forward (out-of-sample) sample size + empirical calibration status,
    cached 5 min. Forward closed signals are the only OOS evidence we have;
    in-sample backtests and live sub-signal convergence do not count as proof.

    `calibrated` is now derived from realized outcomes via
    signal_history.calibrate_confidence (sync, jsonl-backed): it flips True
    ONLY when the HIGH>MEDIUM>LOW win-rate ordering holds with a >=10pp spread
    at the eval horizon. Insufficient data or an inverted ordering keeps it
    False, so nothing is labeled proven_edge until the labels actually earn it.
    Self-activates as signals age into the eval horizon."""
    global _EVIDENCE_CACHE
    if _EVIDENCE_CACHE and time.time() - _EVIDENCE_CACHE[1] < _EVIDENCE_TTL:
        return _EVIDENCE_CACHE[0]
    forward_n = 0
    try:
        tr = paper_trade.get_track_record(windows=[90])
        forward_n = max(
            (w.get("count", 0) for w in tr.get("windows", {}).values()),
            default=0,
        )
    except Exception:
        forward_n = 0
    calibrated = False
    try:
        cal = signal_history.calibrate_confidence(horizon=_CALIBRATION_HORIZON)
        calibrated = cal.get("verdict") == "calibrated"
    except Exception:
        calibrated = False
    ctx = {"forward_n": forward_n, "calibrated": calibrated}
    _EVIDENCE_CACHE = (ctx, time.time())
    return ctx


def _evidence_tier(
    action: str,
    confidence: str,
    forward_n: int,
    calibrated: bool,
    ev_dollars: float | None,
) -> tuple[str, str]:
    """Classify a recommendation: proven_edge / reasonable_thesis /
    experimental / no_edge. Determines whether 'high confidence' is earned."""
    if action == "NO_EDGE":
        return "no_edge", "No measurable edge — no clean directional signal."
    positive_ev = ev_dollars is None or ev_dollars > 0
    if forward_n >= _MIN_FORWARD_PROVEN and calibrated and positive_ev and confidence == "high":
        return (
            "proven_edge",
            f"High convergence + {forward_n} calibrated forward signals.",
        )
    if forward_n >= _MIN_FORWARD_THESIS and confidence in ("high", "medium"):
        return (
            "reasonable_thesis",
            f"Signals align; {forward_n} forward signals logged but not yet calibrated/proven.",
        )
    return (
        "experimental",
        f"Only {forward_n} closed forward signals — experimental until calibrated (need ~{_MIN_FORWARD_PROVEN}).",
    )


def _fmt_price(price: float) -> str:
    return f"${price:.2f}"


def _decide_action(
    *,
    score: float,
    primary_count: int,
    confirming: int,
    conflicting: int,
    dominant_dir: int,
    stage: int | None,
    fund_score: float,
    macro_score: float,
    signal_quality: float,
    market_regime: str,
    is_etf: bool,
) -> tuple[str, str | None]:
    """Accuracy-first action selector.

    Returns (action, gate_reason). Rules — strictest first:

    1. NO_EDGE: zero or one directional sub-signal, OR thesis too thin
       (signal_quality < 2 of 5). The engine refuses to call when evidence
       is insufficient — better to skip a trade than emit a wrong one.

    2. Adversarial / conflict cap: if conflicting >= confirming, downgrade
       to WATCH regardless of score.

    3. SELL gate: only emit SELL when stage in {3,4} + fund_score <= -0.5
       + score < 3.5 + 3-signal convergence. Otherwise TRIM/WATCH/NO_EDGE.
       ETFs skip the fundamentals requirement.

    4. BUY gate (tightened — live BUY leg was negative EV): score >= 8.0 +
       3-signal convergence + zero conflicts + fundamentals >= 0 (ETFs exempt)
       + signal_quality >= 3 + regime not hostile. Otherwise WATCH/HOLD/NO_EDGE.

    5. Default HOLD for everything in between.
    """
    if signal_quality < 2:
        return "NO_EDGE", f"Signal quality {signal_quality:.1f}/5 — thesis too thin to act"

    if primary_count == 0:
        return "NO_EDGE", "No directional sub-signals — no measurable edge"

    if confirming <= 1 and conflicting >= 1:
        return "NO_EDGE", f"Signals contradict ({confirming} for, {conflicting} against) — no advantage"

    if conflicting >= confirming:
        return "WATCH", "Adversarial check — bear case as strong as bull, refusing directional call"

    # Bearish path
    if dominant_dir == -1:
        fund_ok = is_etf or fund_score <= -0.5
        if (
            score < 3.5
            and stage in (3, 4)
            and fund_ok
            and confirming >= 3
            and market_regime not in _REGIME_SELL_HOSTILE
        ):
            return "SELL", "Stage 3/4 trend + weak fundamentals + 3-signal convergence"
        if score < 5.0 and stage in (3, 4):
            return "TRIM", "Bearish lean — trim risk, not yet full SELL conviction"
        if score < 5.5:
            return "WATCH", "Bearish but insufficient deterioration for SELL"
        return "HOLD", None

    # Bullish path — BUY gate tightened (live BUY leg was negative expectancy:
    # 197 sigs, 17.8% win, -5.3% alpha vs XEQT). Raise score bar 7.5→8.0, add a
    # fundamentals floor (ETFs exempt) and a signal-quality floor; demote
    # marginal longs to WATCH so they route away from the losing BUY bucket.
    if dominant_dir == 1:
        buy_ok = (
            score >= 8.0
            and confirming >= 3
            and conflicting == 0
            and (is_etf or fund_score >= 0)
            and signal_quality >= 3
            and market_regime not in _REGIME_BUY_HOSTILE
        )
        if buy_ok:
            return "BUY", "Score≥8 + 3-signal convergence + fundamentals≥0 + quality≥3 + regime ok"
        if score >= 8.0 and market_regime in _REGIME_BUY_HOSTILE:
            return "WATCH", f"Bullish setup but {market_regime} regime — defer until trend confirms"
        if score >= 7.0 and confirming >= 2:
            return "WATCH", "Promising — needs score≥8 + fundamentals + quality + 3-signal confirm"
        if score >= 5.5:
            return "HOLD", None
        return "NO_EDGE", "Bullish score too weak to act"

    return "HOLD", None


def get_recommendations(
    portfolio_positions: list[dict],
    skill_evidence_map: dict[str, dict] | None = None,
) -> list[dict]:
    """Score all positions. Returns list sorted by action urgency
    (SELL→BUY→WATCH→HOLD).

    Output is cached 30 minutes keyed by portfolio composition to prevent
    per-request recalculation which caused BUY→SELL flipping on volatile
    signals. Cache key includes skill_evidence digest so evidence changes
    invalidate the cache.
    """
    if not portfolio_positions:
        return []

    skill_evidence_map = skill_evidence_map or {}

    # Include rounded position weights in the cache key — overweight_penalty,
    # kelly_pct, ev_dollars and hedge_flag all depend on weight, so a key built
    # on symbols alone would serve stale outputs after a rebalance for 30 min.
    # Skill evidence digest invalidates cache when consensus changes for any
    # holding.
    composition = sorted(
        (
            str(p.get("symbol")),
            round(float(p.get("weight") or 0), 1),
            round(float(p.get("market_value_cad") or 0), -2),
            int((skill_evidence_map.get(p.get("symbol")) or {}).get("skill_consensus") or 0),
        )
        for p in portfolio_positions
    )
    cache_key = hashlib.md5(repr(composition).encode(), usedforsecurity=False).hexdigest()
    entry = _REC_CACHE.get(cache_key)
    if entry and time.time() - entry[1] < _REC_CACHE_TTL:
        return entry[0]

    symbols = [p["symbol"] for p in portfolio_positions]
    # 4 workers serialized 23 HTTP-bound tasks (3 aggregate + N sentiment).
    # Sentiment is the long pole — bump the pool so each per-symbol HTTP can
    # overlap. Cap at 16 to avoid hammering the upstream sentiment provider.
    pool_size = min(16, 3 + len(symbols)) or 4
    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as pool:
        tech_future = pool.submit(tech_svc.get_technicals, symbols)
        fund_future = pool.submit(fund_svc.get_fundamentals, symbols)
        macro_future = pool.submit(market_breadth)
        sent_futures = {sym: pool.submit(_get_sentiment, sym) for sym in symbols}
        tech_data = tech_future.result()
        fund_data = fund_future.result()
        macro_data = macro_future.result()
        sent_data = {sym: f.result() for sym, f in sent_futures.items()}

    # Earnings expected move — non-blocking, cached 2h, fails gracefully
    try:
        em_data = get_earnings_expected_moves(symbols, fund_data, tech_data)
    except Exception:
        em_data = {}

    results = []
    for position in portfolio_positions:
        sym = position["symbol"]
        rec = _score_position(
            sym,
            position,
            tech_data.get(sym) or {},
            fund_data.get(sym) or {},
            macro_data,
            sent_data.get(sym, 0.0),
            em_data.get(sym) or {},
            skill_evidence=skill_evidence_map.get(sym),
        )
        results.append(rec)

    results.sort(
        key=lambda r: (
            _ACTION_ORDER.get(r["action"], 9),
            -r["score"] if r["action"] == "BUY" else r["score"],
        )
    )

    # Append each rec to signal_history.jsonl for forward-horizon accuracy
    # auditing. Idempotent per (date, source, symbol, action) — duplicate
    # same-day signals are skipped inside log_signal.
    for rec in results:
        try:
            signal_history.log_signal(rec, source="recommendations")
        except Exception:
            _log.debug("suppressed exception", exc_info=True)

    # Full-contract trade log into paper_trade for benchmark-relative
    # alpha scoring. Single benchmark fetch shared across the whole batch.
    try:
        sector_map: dict[str, str] = {}
        for rec in results:
            sym = rec.get("symbol")
            sector = (fund_data.get(sym) or {}).get("sector")
            etf = _sector_etf_for(sector)
            if sym and etf:
                sector_map[sym] = etf

        contract_recs: list[dict] = []
        for rec in results:
            action = rec.get("action")
            if action not in ("BUY", "SELL", "TRIM", "ADD"):
                continue
            target = rec.get("take_profit")
            entry = rec.get("current_price")
            stop = rec.get("stop_loss")
            target_pct = None
            stop_pct = None
            expected_upside_pct = None
            expected_downside_pct = None
            if entry and entry > 0:
                if target:
                    target_pct = round(abs(target - entry) / entry * 100, 2)
                    expected_upside_pct = round((target - entry) / entry * 100, 2)
                if stop:
                    stop_pct = round(abs(entry - stop) / entry * 100, 2)
                    expected_downside_pct = round((stop - entry) / entry * 100, 2)
            contract_recs.append(
                {
                    "symbol": rec.get("symbol"),
                    "action": action,
                    "conviction": (rec.get("confidence") or "MED").upper().replace("MEDIUM", "MED"),
                    "current_price": entry,
                    "thesis": " | ".join(rec.get("reasons") or [])[:500],
                    "invalidation": f"stop {stop} ({rec.get('stop_type')})" if stop else None,
                    "target_pct": target_pct,
                    "stop_pct": stop_pct,
                    "expected_upside_pct": expected_upside_pct,
                    "expected_downside_pct": expected_downside_pct,
                    "horizon_days": 21,
                    "reasons": rec.get("reasons"),
                    "features": {
                        "tech_score": rec.get("tech_score"),
                        "fund_score": rec.get("fund_score"),
                        "macro_score": rec.get("macro_score"),
                        "sentiment": rec.get("sentiment"),
                        "rsi": rec.get("rsi"),
                        "stage": rec.get("stage"),
                        "market_regime": rec.get("market_regime"),
                        "signal_quality": rec.get("signal_quality"),
                        "risk_reward": rec.get("risk_reward"),
                        "win_prob": rec.get("win_prob"),
                        "kelly_pct": rec.get("kelly_pct"),
                        "days_to_earnings": rec.get("days_to_earnings"),
                    },
                }
            )

        paper_trade.batch_log_recommendations(
            skill="recommendations_engine",
            recs=contract_recs,
            confidence_source="experimental",
            sector_etf_by_symbol=sector_map,
        )
    except Exception:
        _log.debug("suppressed exception", exc_info=True)

    _REC_CACHE[cache_key] = (results, time.time())
    return results
