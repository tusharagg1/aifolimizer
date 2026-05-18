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
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.services import technicals as tech_svc
from app.services import fundamentals as fund_svc
from app.services.fundamentals import get_earnings_expected_moves
from app.services.macro import market_breadth


_ACTION_ORDER = {"SELL": 0, "BUY": 1, "WATCH": 2, "HOLD": 3}

_ETF_ASSET_CLASSES = {"etf", "index", "mutual_fund"}
_CRYPTO_ASSET_CLASSES = {"crypto", "cryptocurrency"}

# ── Output cache ───────────────────────────────────────────────────────────────
_REC_CACHE: dict[str, tuple[list, float]] = {}
_REC_CACHE_TTL = 1800  # 30 minutes

# ── News sentiment cache ───────────────────────────────────────────────────────
_SENT_CACHE: dict[str, tuple[float, float]] = {}
_SENT_TTL = 1800  # 30 minutes

_POSITIVE = frozenset({
    "beat", "beats", "raises", "surges", "gains", "strong", "upgrade",
    "upgrades", "bullish", "record", "tops", "exceeds", "rally", "soars",
    "positive", "better", "outperforms", "boosts", "grows", "growth",
})
_NEGATIVE = frozenset({
    "miss", "misses", "falls", "drops", "weak", "downgrade", "downgrades",
    "bearish", "loss", "warns", "cut", "cuts", "disappoints", "below",
    "negative", "worse", "concern", "plunges", "slumps", "selloff", "layoffs",
})


def _try_llm_sentiment(symbol: str, headlines: list[str]) -> float | None:
    try:
        from app.services.llm_router import score_news_sentiment
        return asyncio.run(score_news_sentiment(symbol, headlines))
    except Exception:
        return None


def _fetch_sentiment(symbol: str) -> float:
    """News headline polarity via LLM (falls back to keyword). Returns -1.0 to +1.0."""
    try:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
        )
        resp = httpx.get(url, timeout=5.0, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(resp.text)
        raw_titles = [
            item.findtext("title") or ""
            for item in root.iter("item")
        ][:15]
        if not raw_titles:
            return 0.0
        llm_score = _try_llm_sentiment(symbol, raw_titles)
        if llm_score is not None:
            return llm_score
        titles = [t.lower() for t in raw_titles]
        pos = sum(1 for t in titles if any(w in t.split() for w in _POSITIVE))
        neg = sum(1 for t in titles if any(w in t.split() for w in _NEGATIVE))
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
) -> dict:
    asset_class = (position.get("asset_class") or "").lower()
    is_etf = asset_class in _ETF_ASSET_CLASSES
    is_crypto = asset_class in _CRYPTO_ASSET_CLASSES

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
    market_regime = macro.get("market_regime") or "bull_low_fear"
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

    # ── Signal convergence → confidence ───────────────────────────────────────
    tech_dir = _direction(tech_score, 0.5)
    fund_dir = _direction(fund_score, 0.5) if not is_etf else 0
    macro_dir = _direction(macro_score, 0.3)
    sent_dir = _direction(sentiment, 0.3)

    primary_dirs = [d for d in [tech_dir, fund_dir, macro_dir, sent_dir] if d != 0]
    if primary_dirs:
        dominant = max(set(primary_dirs), key=primary_dirs.count)
        confirming = primary_dirs.count(dominant)
        conflicting = primary_dirs.count(-dominant)
    else:
        dominant, confirming, conflicting = 0, 0, 0

    if confirming >= 3 and conflicting == 0:
        confidence = "high"
    elif conflicting >= confirming:
        confidence = "low"
    else:
        confidence = "medium"

    # ── Composite score ────────────────────────────────────────────────────────
    # Scale sub-scores: tech(-4→+4), fund(-3→+3), macro(-2→+2), sentiment(-1→+1)
    raw_score = (
        5.0
        + tech_score
        + fund_score
        + (sentiment * 0.8)
        + macro_score
        + overweight_penalty
        + loss_penalty
    )
    score = max(0.0, min(10.0, round(raw_score, 1)))

    # ── Action with convergence gate ──────────────────────────────────────────
    if score >= 7.5:
        action = "BUY"
    elif score >= 5.5:
        action = "HOLD"
    elif score >= 3.5:
        action = "WATCH"
    else:
        action = "SELL"

    # Conflicting signals → cap at WATCH. Don't take strong action without conviction.
    if confidence == "low" and action in ("BUY", "SELL"):
        action = "WATCH"
        reasons.append("Mixed signals across technical/fundamental/macro — holding at WATCH")

    current_price = tech.get("current_price")
    analyst_target = fund.get("analyst_target_price") if fund else None
    analyst_rec = (fund.get("analyst_recommendation") or "").lower() if fund else ""
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
    if rsi and rsi > 65 and action == "BUY":
        entry_timing = "wait_pullback"
    else:
        entry_timing = "acceptable"

    # ── Kelly Criterion — position sizing ─────────────────────────────────────
    # f* = (b·p − q) / b  →  half-Kelly for safety, capped at 20%
    if analyst_rec in ("buy", "strong_buy"):
        win_prob = 0.62
    elif analyst_rec in ("sell", "underperform"):
        win_prob = 0.35
    else:
        # Interpolate from score: score=5 → 45%, score=10 → 65%
        win_prob = min(0.65, max(0.35, 0.35 + (score / 10) * 0.30))

    kelly_pct: float | None = None
    if risk_reward and risk_reward > 0:
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
            reasons_entry = (
                f"Earnings in {days_to_earnings}d — options imply ±{expected_move_pct}%"
                + (f" (${position_at_risk:,.0f} at risk)" if position_at_risk else "")
            )
        elif days_to_earnings <= 21:
            earnings_risk = "upcoming"
            reasons_entry = f"Earnings in {days_to_earnings}d — implied move ±{expected_move_pct}%"
        else:
            earnings_risk = None
            reasons_entry = None
        if earnings_risk and reasons_entry:
            reasons.insert(0, reasons_entry)   # bump to top of reasons list
            if earnings_risk == "imminent" and action == "BUY":
                entry_timing = "wait_pullback"  # don't enter right before binary event

    return {
        "symbol": symbol,
        "name": position.get("name") or symbol,
        "action": action,
        "score": score,
        "confidence": confidence,
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
        # Expected value
        "ev_dollars": ev_dollars,
        "win_prob": round(win_prob, 3),
        "max_loss_dollars": max_loss_dollars,
        # Earnings
        "days_to_earnings": days_to_earnings,
        "expected_move_pct": expected_move_pct,
        "earnings_risk": earnings_risk,
        # Hedge flag
        "hedge_flag": hedge_flag,
        "hedge_reason": hedge_reason,
    }


def _fmt_price(price: float) -> str:
    return f"${price:.2f}"


def get_recommendations(portfolio_positions: list[dict]) -> list[dict]:
    """Score all positions. Returns list sorted by action urgency (SELL→BUY→WATCH→HOLD).

    Output is cached 30 minutes keyed by portfolio composition to prevent
    per-request recalculation which caused BUY→SELL flipping on volatile signals.
    """
    if not portfolio_positions:
        return []

    cache_key = hashlib.md5(
        ",".join(sorted(p["symbol"] for p in portfolio_positions)).encode()
    ).hexdigest()
    entry = _REC_CACHE.get(cache_key)
    if entry and time.time() - entry[1] < _REC_CACHE_TTL:
        return entry[0]

    symbols = [p["symbol"] for p in portfolio_positions]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
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
        )
        results.append(rec)

    results.sort(
        key=lambda r: (
            _ACTION_ORDER.get(r["action"], 9),
            -r["score"] if r["action"] == "BUY" else r["score"],
        )
    )

    _REC_CACHE[cache_key] = (results, time.time())
    return results
