---
name: pead-tracker
description: |
  Track Post-Earnings Announcement Drift (PEAD) across portfolio holdings.
  Use when user asks "which holdings had recent earnings surprises?", "is PEAD still active for X?",
  "post-earnings drift", "earnings momentum", "which stocks are still drifting after earnings?",
  or "show me recent earnings beats". Based on Bernard & Thomas (1989): stocks continue drifting
  in the direction of the earnings surprise for ~60 trading days after the report is public.
---

# PEAD Tracker (Post-Earnings Announcement Drift)

## Research basis

Bernard & Thomas (1989): stocks keep moving in direction of earnings surprise after earnings are public.
Drift window: ~60 trading days (~85 calendar days) from report date.
Expected abnormal return by size: small firms +5.1%, mid +4.3%, large +2.8%.

## How to run

1. Call `mcp__aifolimizer__get_profile` — account types and capital context
2. Call `mcp__aifolimizer__get_portfolio` — full holdings list
3. Call `mcp__aifolimizer__get_earnings_results` with all held symbols, `quarters=2` — get report dates, surprise %, outcome for last 2 quarters
4. Call `mcp__aifolimizer__get_fundamentals` for all symbols — market cap (determines expected drift magnitude), analyst target
5. Call `mcp__aifolimizer__get_technicals` for symbols with active drift — `pct_from_52w_high`, RSI, trend to assess whether drift is still running or exhausted

Call steps 3–5 in parallel after step 2 resolves.

## Drift window logic

For each holding with a recorded earnings report:
- Compute calendar days since report date (use today's date)
- **Active window**: 0–85 calendar days since report
- **Late window**: 55–85 calendar days (drift fading — last chance to ride or exit)
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

- **Expected Edge**: use market cap to estimate remaining drift. Large-cap (>$100B): 2.8% over full window, scale by days remaining. Mid ($2B–$100B): 4.3%. Small (<$2B): 5.1%. Pro-rate: `edge_remaining = full_edge × (days_remaining / 60)`
- **Action**: `ride` (beat, early window), `exit soon` (beat, late window), `trim` (miss, any window), `flat` (meet or expired)

### 2. Active plays (≤5 bullets)

Only holdings still in drift window. Format per line:
```
TICKER · beat/miss X.X% surprise · Day N of ~60 · ~Y% drift remaining · action
```

### 3. Expired positions check

List holdings where PEAD window just closed (85–100 days). Note: "PEAD edge gone — hold/trim on fundamentals only."

### 4. No-data gaps

List symbols where `get_earnings_results` returned empty or null dates. "TSX coverage gap" if .TO ticker. Do not fabricate dates.

### 5. Recommendation (Canadian tax-aware)

For each `ride` or `trim` action that conflicts with current portfolio weight:
- Which account to act in (TFSA > Non-Reg for gains, Non-Reg for harvesting losses)
- Max position cap: if adding via PEAD signal, cap at 5% incremental add — this is a momentum overlay, not a conviction change

## Rules

- Always use `get_profile` first — never hardcode accounts or capital
- Never fabricate report dates — only use dates from `get_earnings_results`
- Expected drift is statistical, not guaranteed — present as probabilistic edge, not certainty
- Under 500 words
- If fewer than 3 holdings have earnings results in last 85 days, say so explicitly — don't pad

## Gotchas

- `get_earnings_results` returns yfinance `earnings_history` — has date field but TSX (.TO) tickers sparse. Note gap, don't fabricate.
- "Days since report" requires comparing report date to today. Use currentDate from context. Do not assume.
- Large-cap beats often get priced faster (institutional speed) — 2.8% drift estimate is conservative and may already be gone by day 5 for mega-caps. Flag if stock already ran >2% since report.
- Miss + negative drift = same logic inverted. Don't add to a PEAD-miss name just because you like the company.
- `surprise_pct` from yfinance is EPS-only. Revenue miss can override EPS beat (stock sold off despite beat). Cross-check `get_news_headlines` if price moved opposite to EPS outcome.
- `get_technicals` cached 1h — RSI/trend signal may lag. Mention cache timestamp.
- PEAD effect strongest in first 20 trading days post-report, fades after. Strongest entry = day 1–14.
- Crypto holdings: no earnings. Skip. List in section 4.
