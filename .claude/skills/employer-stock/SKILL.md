---
name: employer-stock
description: Analyze employer / equity-plan stock (shares held via an employer equity plan, RSUs, ESPP) as a SEPARATE concentration and wealth problem from the rest of the portfolio. Use when the user mentions employer stock, RSUs, ESPP, vesting, an equity-plan platform, "my company shares", or asks "should I keep / sell / trim my employer stock". Treats single-employer exposure as the dominant long-term risk it usually is.
---

# Employer Stock Deep Dive (concentration · vesting · tax · opportunity cost)

## Goal

Decide hold / trim-gradual / trim-now / sell on employer or equity-plan stock,
treating it as a distinct risk bucket. The core question is long-term:
**single-employer concentration is undiversified human + financial capital** —
salary AND a chunk of net worth ride the same company. Default skepticism
toward large employer-stock weights; the burden is on KEEPING it, not selling.

## Why this is its own skill

`get_profile` only sees Wealthsimple accounts. Employer equity plans live
OUTSIDE WS — the system is blind to them. So this skill MUST ask the user for
the plan data it cannot fetch.

## Decision Memory Protocol (load first, log after)

**Before** the analysis, load prior decisions on this employer ticker so the verdict stays consistent across sessions:
- `mcp__aifolimizer__get_ticker_decision_history` (`ticker=TICKER, max_decisions=5`) + `mcp__aifolimizer__get_ticker_reflection` (`symbol=TICKER, n=3`) + `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log the verdict: call `mcp__aifolimizer__log_recommendation` (`skill="employer-stock", ticker, action, conviction, rationale, target_pct, stop_pct`).

## Stage 0 — Gather what the system can't see (ASK)

Request from the user (don't guess):
- Ticker + shares held in the plan, current plan value
- Cost basis / acquisition type: RSU (vests as ordinary income), ESPP (often
  15% discount, look-back), open-market, options
- Vesting schedule + unvested amount + vest dates
- Account/tax wrapper the shares sit in (registered? taxable? US plan w/ 401k?)
- Annual salary from the SAME employer (to size human-capital concentration)
- Any blackout windows / trading restrictions / sale-after-vest rules

Then pull the public side:
- `get_profile` + `get_portfolio` — total invested + whether the same ticker is
  ALSO held in WS (double-counting the bet)
- `get_fundamentals` (ticker) — yield, payout, growth, beta, analyst target
- `get_dividend_calendar` (symbols=[ticker]) — ex-div / pay timing for hold-vs-sell
- `get_dcf_valuation` (US ticker) — intrinsic-value anchor (most US large-caps qualify)
- `get_positioning_signals` (symbols=[ticker]) — is the Street crowded / contrarian
- `get_news_headlines` (ticker) — live catalyst check

## Stage 1 — Concentration math (the heart of it)

Compute and state explicitly:
- **Financial concentration** = employer-stock value ÷ (WS portfolio + plan value).
  Flag > 10% (elevated), > 20% (high), > 30% (severe).
- **Total exposure** = include salary. If salary + stock both depend on the
  employer, a 10% portfolio weight understates true risk. Name this.
- **Overlap**: if WS also holds the same ticker or a sector ETF heavy in it,
  add that — the real bet is larger than the plan line alone.

## Stage 2 — Keep vs cut (the decision)

Weigh, long-term lens:
- **Reasons to KEEP (temporarily)**: large embedded gain in a taxable wrapper
  (selling triggers tax now), strong + safe dividend, ESPP shares still inside
  the discount/qualifying-disposition window, near a vest with favorable tax.
- **Reasons to CUT**: concentration > 20%, weak fundamentals vs `get_dcf_valuation`,
  consensus-crowded (`crowding ≥ 70`) with no edge, dividend at risk (high payout
  + falling FCF), thesis is "I work here" not "it's the best use of capital".
- **Opportunity cost**: frame against the user's default long-term vehicle
  (XEQT/VFV). "Is this single name a better 10-yr hold than a diversified index?"
  If not, the bar to keep concentrated is high.

## Stage 3 — Execution shape

- **Trim gradually** (default for big embedded gains): sell on a schedule
  (e.g. every vest, or quarterly) to spread tax + average exit, target a
  concentration cap (e.g. ≤ 10%).
- **Trim now**: if concentration severe AND fundamentals weak — discipline beats
  tax optimization when the position can halve.
- **Sell-at-vest policy**: for future RSU vests, recommend auto-sell-on-vest
  (no new tax cost — already taxed as income at vest) to stop re-concentrating.
- Tax note: vest = ordinary income (already taxed); subsequent gain = cap gain.
  ESPP discount is ordinary income; qualifying dispositions change treatment.
  Canadian taxable account: 50% cap-gains inclusion on the post-vest gain.

## Output

```
EMPLOYER STOCK: <ticker> via <plan>
Concentration: financial X% · with-salary lens: <note> · WS overlap: <Y/N>
Fundamentals: yield X% (safe/at-risk) · DCF fair value $Z (over/under) · crowding <label>
Verdict: KEEP | TRIM GRADUAL | TRIM NOW | SELL
Plan: <schedule / cap target / sell-at-vest policy>
Tax note: <wrapper-specific>
What would change the verdict: <triggers>
```

## Rules

- Never fabricate plan data — if the user hasn't given shares/cost/vesting, ASK; do not assume.
- Concentration is the headline. A great company at 30% of net worth is still a risk problem.
- Don't let an embedded tax gain alone justify keeping a severe concentration — name the trade-off, let the user decide.
- Default long-term framing (the user's stated goal). Intraday/swing logic does not apply to plan stock.
- US ticker → use `get_dcf_valuation`. Non-US → say DCF unavailable, lean on analyst target + multiples.
