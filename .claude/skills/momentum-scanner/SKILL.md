---
name: momentum-scanner
description: |
  Scan portfolio holdings for 12-month price momentum and 52-week high proximity.
  Use when user asks "which stocks have momentum?", "rank my holdings by performance",
  "momentum scan", "12-month winners and losers", "52-week high analysis", "which should I trim?",
  or "show momentum signals". Based on Jegadeesh & Titman (1993): past 12-month winners earn
  up to 1.49%/month excess return. George & Hwang (2004): stocks near 52-week highs outperform ~1.23%/month.
requires_profile: true
---

# Momentum Scanner (12-Month Momentum + 52-Week High Effect)

## Research basis

**Jegadeesh & Titman (1993):** Past 12-month winners outperform losers by 12.01% annually.
Effect persists independently of beta, size, or industry. Strongest version: skip most-recent month (reversal noise), rank on months 2–12.

**George & Hwang (2004):** Stocks within 10% of 52-week high earn ~1.23%/month long-short vs stocks far from high.
Mechanism: anchoring bias — investors slow to push past prior resistance, so nearness predicts continuation.

**Combined signal:** High 12m momentum AND near 52wk high = double confirmation. Strongest continuation setup.

## How to run

1. Call `mcp__aifolimizer__get_profile` — account types, capital
2. Call `mcp__aifolimizer__get_portfolio` — all holdings, current weights
3. Call `mcp__aifolimizer__get_technicals` for all held symbols — `pct_from_52w_high`, `pct_from_52w_low`, `minervini_score`, `technical_score`, trend
4. Call `mcp__aifolimizer__backtest_portfolio` with `strategy="buy_hold"` for 12-month return per symbol
5. Call `mcp__aifolimizer__get_positioning_signals` for top 8 holdings — crowding check before any add signal

Call steps 3–5 in parallel after step 2 resolves.

## Momentum scoring

For each holding, compute composite momentum score (0–100):

| Component | Weight | Signal |
|-----------|--------|--------|
| 12m return rank (percentile within portfolio) | 50% | Higher = stronger |
| 52wk high proximity (1 − abs(pct_from_52w_high)/100) | 30% | Closer to high = stronger |
| Minervini score / 7 | 20% | Stage 2 breakout = strongest |

**Labels:**
- Score ≥ 70: `Strong Momentum` — continuation bias
- Score 45–69: `Mixed` — hold, no directional edge
- Score < 45: `Weak / Laggard` — trim candidate per J&T framework

## Output structure

### 1. Momentum ranking table

Render markdown table, sorted by composite score descending:

| Ticker | Weight % | 12m Return | vs 52wk High | Minervini | Composite | Label |
|--------|---------|-----------|-------------|-----------|-----------|-------|

### 2. Strong momentum plays (≤5)

Holdings with score ≥ 70. Per line:
```
TICKER · +XX% 12m · X% from 52wk high · Minervini N/7 · [Hold / Add small if not crowded]
```
Cross-check crowding: if `crowding_score ≥ 70` (consensus), suppress add signal — state "momentum real but consensus already long."

### 3. Laggards (score < 45)

Per line:
```
TICKER · −XX% 12m · XX% below 52wk high · account: TFSA/Non-Reg · action: trim / watch / exit
```
For Non-Reg positions at a loss: flag tax-loss harvest opportunity.
For TFSA positions at a loss: trim — no tax benefit for waiting, capital better deployed in momentum names.

### 4. Rebalance signal

If total weight in Strong Momentum names < 40% of portfolio: "Momentum tilt underweight — consider trimming bottom-quintile laggards and rotating into top-quintile names."
If total weight in Weak names > 30%: "Drag risk — laggards consuming capital that momentum names would compound faster."

## Rules

- Always call `get_profile` first — never hardcode accounts or capital
- 12m return from `backtest_portfolio` buy_hold — do NOT use cost basis return (that's entry-date dependent, not trailing 12m)
- 52wk proximity from `get_technicals` directly — `pct_from_52w_high` is negative (stock below high). Nearness = `abs(pct_from_52w_high)` closer to 0
- Under 500 words
- Skip crypto for 12m momentum ranking — crypto volatility overwhelms equity signals. Note separately.

## Gotchas

- `backtest_portfolio` returns annualized return for buy_hold — confirm lookback covers 12 months. If portfolio position held < 12 months, return is since-inception, not full 12m. Flag with "held < 12m — partial signal."
- `pct_from_52w_high` is typically negative (current price below high). If 0 or positive, stock is at or above 52wk high — maximum George & Hwang signal. Do not treat as data error.
- Momentum strategies have well-documented January reversal — laggards historically bounce in January. If analysis runs in December–January, note "January reversal risk — reduce trim urgency for laggards."
- J&T skip-1-month rule: skip the most recent month return (month 1) when computing 12m momentum signal to avoid short-term reversal contamination. `backtest_portfolio` buy_hold returns full period — acknowledge this approximation.
- ETFs (XEQT, VFV, etc.) have momentum but different dynamics than single stocks. They don't exhibit the same earnings-driven drift. Rank them separately or flag.
- Crowding data sparse for TSX names — label "neutral" if `crowding_score` null, don't suppress signal on absence of data.
- Minervini score null for tickers with <200 days price history. Score as 0/7, mark "insufficient history."
