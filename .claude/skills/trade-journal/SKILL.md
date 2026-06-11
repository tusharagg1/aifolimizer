---
name: trade-journal
description: Capture the psychological side of a trade - emotion, conviction source, confidence, and plan adherence - at entry and exit, then surface which felt-states actually lose money. Use when the user says "journal this trade", "log my entry", "I just bought/sold X", "reconcile my trade", "trade journal", "why do I keep losing", "what's my real psychological pattern", or wants emotional/behavioral review of their trading. Pairs with pre-trade-check (entry) and weekly-mirror (review).
---

# Trade Journal (Psychological Layer)

## Goal

Most retail losses are emotional, not analytical. `decision_memory` records the *facts* of a trade (price, conviction label, thesis) and `shadow_account` derives *biases from price/date*. Neither captures what the trader **felt** at the moment of entry. This skill does - emotion, conviction source, self-rated confidence, plan adherence - then `get_journal_insights` cross-tabs felt-state against realized outcomes to answer the one question that beats technical analysis: *what emotional state precedes my losing trades?*

This is a **capture + mirror** skill, not a recommender. It does not approve or reject trades (that's pre-trade-check).

## When to invoke

- ENTRY: user just placed (or is about to place) a trade and wants it journaled. Natural follow-on from pre-trade-check.
- EXIT: user closed a position - reconcile the open journal entry.
- REVIEW: "why do I keep losing", "what's my pattern", "trade journal insights".

## How to run

**Step 0 - Detect phase (REQUIRED):** entry, exit, or review. If ambiguous, ask.

### ENTRY phase

**Step 1 - Capture felt-state (ask only what the user has not already said):**
1. **Ticker?**
2. **Emotion right now?** → one of: `calm | fomo | fear | revenge | conviction | bored | uncertain`
3. **Where did the conviction come from?** → `thesis | chart | tip | social | news | gut`
4. **Confidence 1-5?** (1 = a flyer, 5 = highest conviction)
5. **What is the plan?** (entry / stop / exit stated BEFORE the trade - one sentence)
6. **Did pre-trade-check pass?** (yes/no - if they skipped it, note that, it is itself a signal)

**Step 2 - Persist:**
```
Call mcp__aifolimizer__log_trade_journal with:
  ticker=<TICKER>
  emotion=<emotion>
  conviction_source=<source>
  confidence_1to5=<1-5>
  plan_intended=<one-sentence plan>
  felt_note=<free text, optional>
  pre_trade_check_passed=<true/false>
```

**Step 3 - Mirror back one line.** If `emotion` ∈ {fomo, revenge, fear} OR `conviction_source` ∈ {tip, social, gut} OR `pre_trade_check_passed=false`, flag it plainly: "Logged. Note: this is a {emotion}/{source} entry - historically your weakest setup (check insights)." Do not moralize beyond one line.

### EXIT phase

**Step 1 - Reconcile (ask):**
1. **Ticker?**
2. **Did you follow the plan you wrote at entry?** (yes/no - be honest)
3. **Emotion at exit?** (same enum)
4. **Was the outcome a surprise?** → `expected | mild_surprise | shock`
5. **One-line lesson?**

**Step 2 - Persist:**
```
Call mcp__aifolimizer__log_trade_journal_exit with:
  ticker=<TICKER>
  plan_followed=<true/false>
  exit_emotion=<emotion>
  outcome_surprise=<level>
  lesson=<one line>
```
If it returns `reconciled=false`, there was no open entry - tell the user the entry was never journaled; offer to log a retroactive entry.

### REVIEW phase

**Step 1 - Mark outcomes current, then pull insights (in order):**
1. `mcp__aifolimizer__resolve_trade_outcomes` (marks decisions to market so wins/losses are fresh)
2. `mcp__aifolimizer__get_journal_insights`

**Step 2 - Present the mirror** (use the output template). Lead with the single worst felt-state by win-rate. State the confidence-calibration verdict bluntly: if `avg_confidence_losses >= avg_confidence_wins`, the user's confidence is **inverted** - they feel most sure exactly when they are most wrong.

## Output template (REVIEW)

```
TRADE JOURNAL - PSYCHOLOGICAL MIRROR
Scored trades: <scored_entries> of <total_entries>  (<open_unreconciled> not yet closed)

WIN-RATE BY ENTRY EMOTION
  conviction   <n>  <win_rate_pct>%
  calm         <n>  <win_rate_pct>%
  fomo         <n>  <win_rate_pct>%   <-- worst
  ...

WIN-RATE BY CONVICTION SOURCE
  thesis       <n>  <win_rate_pct>%
  social       <n>  <win_rate_pct>%   <-- worst
  ...

CONFIDENCE CALIBRATION
  avg confidence on WINS:   <avg_confidence_wins>
  avg confidence on LOSSES: <avg_confidence_losses>
  verdict: <calibrated | INVERTED - you are most sure when most wrong>

THE PATTERN: <one sentence naming the emotion+source that loses money>
```

## Rules

- Capture only - never approve/reject a trade (route to pre-trade-check for that).
- Never invent felt-state. If the user did not state an emotion, ask; do not guess it from price action.
- Enums are fixed - map the user's words to the nearest enum value; if none fits, ask.
- Keep entry/exit interactions to <120 words. The review is the only long output.
- No PII. Journal stores ticker + felt-state only; no balances, no account IDs.

## Gotchas

- `get_journal_insights` can only score entries whose ticker has a **resolved** decision (target_hit/stop_hit). If `scored_entries` is 0 but `total_entries` > 0, tell the user outcomes aren't resolved yet - run `resolve_trade_outcomes` or wait for trades to hit target/stop.
- Insights need a decision logged too (via pre-trade-check / log_trade_decision). A journal entry with no matching decision is captured but unscoreable. Encourage logging both.
- Small-sample caveat: with <5 scored trades per bucket, win-rates are noise. Say so.
- This skill feeds `weekly-mirror` (lessons) - do not duplicate the performance-math output; defer P&L/R-multiple to weekly-mirror.
