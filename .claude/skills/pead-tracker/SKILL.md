---
name: pead-tracker
description: Track Post-Earnings Announcement Drift (PEAD) across ALL portfolio holdings - which held names beat/missed last quarter and are still drifting in the surprise direction. Use for "post-earnings-surprise drift", "is PEAD still active for X?", "which holdings are still drifting after earnings?", "earnings beats still drifting", "earnings-drift overlay across my book". This is a whole-book drift scan - NOT a single-name post-report verdict (use earnings-postmortem) and NOT price-momentum ranking (use momentum-scanner).
---

# PEAD Tracker (Post-Earnings Announcement Drift)

## Stage 0 — Decision Memory (load FIRST)

Before analysis, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` — portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`).

Reconciliation rule: if a prior decision exists and your new read flips it, state explicitly WHY it changed (new data / catalyst / price move). Never silently contradict a logged decision — that drift is exactly what this prevents.

## Research basis

Bernard & Thomas (1989): stocks keep moving in direction of earnings surprise after earnings are public.
Drift window: ~60 trading days (~85 calendar days) from report date.
Expected abnormal return by size: small firms +5.1%, mid +4.3%, large +2.8%.

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types and capital context
2. Call `mcp__aifolimizer__get_portfolio` - full holdings list
3. Call `mcp__aifolimizer__get_earnings_results` with all held symbols, `quarters=2` - get report dates, surprise %, outcome for last 2 quarters
4. Call `mcp__aifolimizer__get_fundamentals` for all symbols - market cap (determines expected drift magnitude), analyst target
5. Call `mcp__aifolimizer__get_technicals` for symbols with active drift - `pct_from_52w_high`, RSI, trend to assess whether drift is still running or exhausted

Call steps 3-5 in parallel after step 2 resolves.

## Drift window logic

Use ONE clock — calendar days — throughout, to avoid mixing trading-day and calendar-day units. The ~60-trading-day Bernard-Thomas window ≈ 85 calendar days; we measure everything against that 85-day calendar window.

For each holding with a recorded earnings report:
- Compute calendar days since report date (use today's date)
- **Active window**: 0-85 calendar days since report
- **Late window**: 55-85 calendar days (drift fading - last chance to ride or exit)
- **Expired**: > 85 calendar days (no PEAD edge remaining)

Drift direction:
- `outcome == "beat"` + positive `surprise_pct` → positive drift expected, bias HOLD/ADD
- `outcome == "miss"` + negative `surprise_pct` → negative drift expected, bias TRIM/EXIT
- `outcome == "meet"` → no directional drift signal

## Output structure

### 1. Drift summary table

Render markdown table. Columns:

| Ticker | Report Date | Days Since | Surprise % | Outcome | Drift Window | Expected Edge | Action |
|--------|------------|-----------|-----------|---------|-------------|--------------|--------|

- **Expected Edge**: use market cap to estimate remaining drift. Large-cap (>$100B): 2.8% over full window. Mid ($2B-$100B): 4.3%. Small (<$2B): 5.1%. Pro-rate on the same 85-day calendar clock: `days_remaining = max(0, 85 - calendar_days_since)`, then `edge_remaining = full_edge × days_remaining / 85`. The `max(0, …)` clamp means an expired name shows 0% remaining edge, never a negative number.
- **Action**: `ride` (beat, early window), `exit soon` (beat, late window), `trim` (miss, any window), `flat` (meet or expired)

### 2. Active plays (≤5 bullets)

Only holdings still in drift window. Format per line:
```
TICKER · beat/miss X.X% surprise · Day N of ~85 (calendar) · ~Y% drift remaining · action
```

### 3. Expired positions check

List holdings where PEAD window just closed (85-100 days). Note: "PEAD edge gone - hold/trim on fundamentals only."

### 4. No-data gaps

List symbols where `get_earnings_results` returned empty or null dates. "TSX coverage gap" if .TO ticker. Do not fabricate dates.

### 5. Recommendation (Canadian tax-aware)

For each `ride` or `trim` action that conflicts with current portfolio weight:
- Which account to act in (TFSA > Non-Reg for gains, Non-Reg for harvesting losses)
- Max position cap: if adding via PEAD signal, cap at 5% incremental add - this is a momentum overlay, not a conviction change

## After output - log decisions

For each Active play with action `ride` (ADD) or `trim`/`exit soon` (TRIM/EXIT):

- If the action is `ride` (ADD), first call `mcp__aifolimizer__get_positioning_signals` with `symbols=[ticker]`. If `crowding_score >= 70` the drift is already consensus-crowded (late entry = negative expected alpha) — downgrade to HOLD or cap the incremental add hard; this is a momentum overlay, not a conviction buy. Favor names with `crowding_score <= 30`.
- Then call `mcp__aifolimizer__log_recommendation` with `skill="pead-tracker"` (the param is `skill`, not `skill_used` — that belongs to `log_trade_decision`), `ticker`, `action` (ADD/HOLD/TRIM/SELL), `conviction` (HIGH/MED/LOW per surprise magnitude + days remaining), `target_pct` + `stop_pct` (% from entry — the schema takes percentages, not absolute prices), `rationale` (1-line citing surprise % + drift days remaining). Skip `flat` (no edge). Feeds forward win-rate / track-record loop.

## Rules

- Always use `get_profile` first - never hardcode accounts or capital
- Never fabricate report dates - only use dates from `get_earnings_results`
- Expected drift is statistical, not guaranteed - present as probabilistic edge, not certainty
- Under 500 words
- If fewer than 3 holdings have earnings results in last 85 days, say so explicitly - don't pad

## Gotchas

- `get_earnings_results` returns yfinance `earnings_history` - has date field but TSX (.TO) tickers sparse. Note gap, don't fabricate.
- "Days since report" requires comparing report date to today. Use currentDate from context. Do not assume.
- Large-cap beats often get priced faster (institutional speed) - 2.8% drift estimate is conservative and may already be gone by day 5 for mega-caps. Flag if stock already ran >2% since report.
- Miss + negative drift = same logic inverted. Don't add to a PEAD-miss name just because you like the company.
- `surprise_pct` from yfinance is EPS-only. Revenue miss can override EPS beat (stock sold off despite beat). Cross-check `get_news_headlines` if price moved opposite to EPS outcome.
- `get_technicals` cached 1h - RSI/trend signal may lag. Mention cache timestamp.
- PEAD effect strongest in first 20 trading days post-report, fades after. Strongest entry = day 1-14.
- Crypto holdings: no earnings. Skip. List in section 4.
