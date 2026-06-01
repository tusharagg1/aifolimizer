---
name: weekly-mirror
description: Brutally honest weekly performance mirror. Use every Sunday or when the user asks "how am I doing?", "weekly review", "am I making money?", "show me my track record", "should I keep trading?". Surfaces real win rate, average win/loss R-multiple, trading P&L vs boring-core P&L, and recommends continue / cool-off / suspend.
---

# Weekly Mirror (Cold Performance Review)

## Goal

One scroll-length, no-sugarcoat performance review of the user's discretionary trading vs their boring-core (index / ETF) holdings. Tells the user the truth even when uncomfortable. Recommends one of: **continue**, **cool-off 30 days**, **suspend discretionary**.

The point: most retail traders never measure their real win rate. They remember winners, forget losers, and feel like they're doing fine until the account is down. This skill makes the math impossible to ignore.

## State check (BEFORE any tool calls)

Read `.claude/context/STATE.md`. If `last_mirror_date` is within the last 6 days, output:
> "Weekly mirror ran on `last_mirror_date` - only N days ago. Run again? (yes to proceed)"
Wait for user confirmation before continuing. If user says yes or forced, proceed normally.

## When to invoke

- User asks "how am I doing?", "am I making money?"
- Sunday evening review (can be scheduled via /loop)
- After any 7-day streak of losses
- Before any decision to increase position sizes

## How to run

**Step 1 - Pull state (parallel):**
1. `mcp__aifolimizer__get_profile` - total NAV per account, cash balances
2. `mcp__aifolimizer__get_portfolio` - current holdings + day/total returns
3. `mcp__aifolimizer__score_recommendations` - mark-to-market all open recs from `pre-trade-check` and other skills, mark stops/targets hit
4. `mcp__aifolimizer__get_live_track_record` with `lookback_days=7`, `lookback_days=30`, `lookback_days=90` (3 separate calls) - win rate + P&L per window
5. `mcp__aifolimizer__get_alpha_attribution` with `benchmarks=["SPY","XEQT.TO","QQQ"]` - am I beating the index?
6. `mcp__aifolimizer__snapshot_portfolio_equity` - append today's NAV to history (idempotent per day)
7. `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=5` - recurring patterns from prior stop-outs

## Investor profile

- Always pull capital from `get_profile` - never hardcode
- Boring-core tickers default: `["XEQT.TO", "VFV.TO", "VTI", "QQQ", "XIC.TO", "ZSP.TO"]`. Treat as passive bucket.
- Trading tickers = everything else in portfolio. Treat as discretionary bucket.

## Output structure

**Lead with the scoreboard. Save context for the verdict.**

### 1. The Scoreboard (top of brief)

```
WEEKLY MIRROR · <DATE> · <ACCOUNT or AGGREGATE>
================================================
Total NAV:           $X,XXX (Δ this week: ±A.A% / ±$BBB)
Total deposits YTD:  $X,XXX
Return vs deposits:  ±C.C%      ← THIS IS THE NUMBER THAT MATTERS

7-day P&L breakdown:
  Boring-core (ETFs/index):   ±$D     (B.B%)
  Discretionary trades:       ±$E     (E.E%)
  Difference:                 ±$F     ← are trades helping or hurting?

vs SPY this week:      ±G.G%
vs XEQT.TO this week:  ±H.H%
```

### 2. Trade Track Record (table)

| Window | Closed Trades | Win Rate | Avg Win | Avg Loss | R-multiple | Net P&L |
|---|---|---|---|---|---|---|
| 7d | N | X% | $X | $X | X.XR | ±$X |
| 30d | N | X% | $X | $X | X.XR | ±$X |
| 90d | N | X% | $X | $X | X.XR | ±$X |

Interpret R-multiple:
- R ≥ 2.0 → entries+exits are working (rare for retail)
- 1.0 ≤ R < 2.0 → break-even after costs, need higher win rate
- R < 1.0 → losers larger than winners - classic discipline failure
- Win rate < 40% + R < 1.5 → **STOP**

### 3. Open positions snapshot (from `score_recommendations`)
- Open recs count, total at-risk capital, total floating P&L
- Stops hit but not exited (action: close now)
- Targets hit but not trimmed (action: trim 50% now)
- Time-decay positions (>30 days open without resolution - review thesis)

### 4. Recurring patterns (from cross-ticker lessons)
List up to 3 repeat-mistake patterns the user is making. Examples:
- "5 of last 8 stop-outs were single-stock semiconductors entered after >10% week"
- "All 4 losing trades in last 30d had crowding_score >70 at entry"
- "Average hold time on winners: 2.1 days. On losers: 14.8 days. Holding losers too long."

This is the most valuable section - repeated mistakes are the only fixable thing.

### 5. Boring-core check
- Total in boring-core (XEQT/VFV/etc): $X (Y% of NAV)
- Auto-DCA active? (check if user has recurring buys)
- Boring-core YTD return: ±%
- Discretionary YTD return: ±%
- **Honest math: would moving 100% to XEQT 1 year ago have outperformed?** Compute using boring-core return and apply to total NAV.

### 6. Verdict (REQUIRED - no soft language)

One of:

**🟢 CONTINUE**
Criteria: 30d win rate ≥ 50% AND R-multiple ≥ 1.5 AND discretionary beating boring-core by ≥3% over 90d.
Output: "Discipline is working. Maintain position sizes."

**🟡 COOL-OFF 30 DAYS**
Criteria: 30d win rate 40-50%, R-multiple 1.0-1.5, OR discretionary underperforming boring-core by 0-5% over 90d.
Output: "Reduce position sizes by 50% for next 30 days. Take half the trades. Journal every entry."

**🔴 SUSPEND DISCRETIONARY**
Criteria: 30d win rate < 40%, OR R-multiple < 1.0, OR discretionary underperforming boring-core by ≥5% over 90d.
Output: "Stop discretionary trading for 30 days. Allocate next 4 weeks of contributions to XEQT.TO or VFV.TO DCA only. Revisit after 30d."

### 7. Next actions (≤ 3 bullets)
- Specific calls to action with $ amounts
- Example: "Sell PLTR (stop hit 4d ago, still holding): −$340 realized > continued risk"
- Example: "Move $2,000 settled cash → XEQT.TO biweekly DCA"
- Example: "Skip next 5 swing-trade temptations. Journal them but do not enter."

## After output - write STATE.md

After completing the mirror review, update `.claude/context/STATE.md`:
- `last_mirror_date`: today's date (YYYY-MM-DD)
- `open_recs`: count of open recommendations from `score_recommendations`

## Rules

- ≤ 500 words total
- NO hedging language. "Suspend discretionary" not "you might consider reducing"
- Always show the boring-core counterfactual (what would XEQT have done?)
- If 90d window has < 5 closed trades, mark stats as "insufficient sample" but still show 7d/30d
- Verdict is determined by math thresholds above, not feel. State the math
- If user disputes verdict, re-state thresholds. Do not flip on argument
- Currency: report in CAD (account base), mark USD positions explicitly

## Gotchas

- `score_recommendations` only sees recs that were logged via `log_recommendation`. If user trades without invoking `pre-trade-check`, this skill is blind to those trades. Recommend: enforce `pre-trade-check` on every entry
- `get_live_track_record` cached per session - for fresh stats, call `score_recommendations` first to mark-to-market open positions
- `get_alpha_attribution` needs `snapshot_portfolio_equity` history. New users with <30 days of history will see "insufficient history" - note in output
- Wealthsimple Managed account returns are not user-controlled - exclude from discretionary attribution. Only attribute self-directed accounts (TFSA-self-directed, RRSP-self-directed, Non-Reg-self-directed)
- Day-trade P&L: if user has intraday closes, `score_recommendations` may not see them because rec was opened+closed same session. Document this as known blind spot until intraday-rec logging exists
- Boring-core tickers list is configurable - if user holds different ETFs (e.g. VOO, SCHD), update the default list in Step "Investor profile"
- Crypto P&L (CADC, BTC, etc.) - treat as separate bucket, not boring-core, not discretionary. Volatility skews trade stats
- Do NOT recommend specific replacement trades in this skill. This is a mirror, not an advisor. Trade picking belongs in cash-deployment or stock-analysis
- Verdict thresholds calibrated for typical retail. For users with prop-trading background, ask if they want tighter thresholds
