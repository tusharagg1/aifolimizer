---
name: daily-briefing
description: Run a one-shot morning portfolio digest that ties together health, alerts, macro regime, crowding risk, concentration, and earnings calendar into a single decision-ready brief. Use when the user asks for "morning briefing", "daily digest", "what's happening today?", "what changed overnight?", or "give me the morning rundown". Composes existing MCP tools — no new data sources.
---

# Daily Briefing (morning digest)

## Goal

One scroll-length brief surfacing what matters today. Composes 7 MCP tools. No new data fetches outside listed below.

## How to run

Call in parallel (no inter-dependencies):

1. `mcp__aifolimizer__get_profile`
2. `mcp__aifolimizer__get_portfolio`
3. `mcp__aifolimizer__get_macro_snapshot` (FRED + market regime)
4. `mcp__aifolimizer__get_concentration_warnings`
5. `mcp__aifolimizer__get_triggered_alerts` (since_hours=24)
6. `mcp__aifolimizer__get_earnings_calendar` (next 14d)
7. `mcp__aifolimizer__get_positioning_signals` (top 15 holdings)

## Investor profile

- Age: 32, Canadian resident
- Philosophy: growth stocks, index ETFs, dividends, crypto
- Always pull capital + accounts from `get_profile` — never hardcode

## Output structure

Single-page brief, ≤ 400 words, this exact order:

### 1. Headline (one sentence)
Format: `[Portfolio value CAD] · [Day Δ %] · [Regime label] · [N alerts last 24h]`
Example: `$182,300 CAD · −0.7% · bull_high_fear · 3 alerts last 24h`

### 2. What changed (≤ 5 bullets)
Action-significant items only. No filler.
- New triggered alerts (price drop, RSI, earnings, concentration)
- Crowding score regime shifts (if positioning history available — flag consensus → contrarian or vice versa)
- Earnings within 3 days
- Macro regime change (e.g. bull_low_fear → bull_high_fear)

### 3. Today's focus list (≤ 3 tickers)
Pick from cross of: alerts triggered + earnings imminent + crowding flag. Format per ticker:
```
TICKER · weight X% · reason · suggested action
```
Suggested action is one of: `review`, `trim`, `hedge`, `hold`, `add (small)`.

### 4. Risks on radar
- Single-position concentration > 10%
- Sector concentration > 35%
- Consensus-crowded names with negative day change > 3% (late-entry risk materializing)
- Yield curve / VIX / Fear-Greed extremes if macro snapshot flags

### 5. Skipped today
One line listing tools/checks that returned empty or stale data. Example: `Skipped: crypto (no holdings), tax-loss (no underwater positions)`.

## Rules

- Direct. No hedging, no "you may want to consider".
- If tool errors or returns empty, list in section 5 — do NOT fabricate data.
- Currency: CAD aggregate unless user in specific account.
- Crowding rule (per CLAUDE.md): if `crowding_label == consensus` AND ticker in focus list, default action skews toward `trim` or `hold`, NOT `add`.
- One-shot — do not chain into another skill unless user asks.

## Gotchas

- `get_triggered_alerts` read-only — does NOT re-evaluate rules. If user wants fresh evaluation, call `run_alerts_now` first (only if explicitly requested — adds 5-10s).
- TSX (.TO) tickers may have null crowding fields → label "neutral" by fallback. Don't treat as real signal.
- Macro regime label `bull_low_fear` can co-exist with portfolio-level red day — regime is market-wide, day-change is portfolio-specific. Don't conflate.
- Earnings calendar is yfinance — sparse for non-US listings. Cross-check IR pages for TSX names if stake is large.
- "What changed" requires comparing to prior brief. If running fresh (no prior), say so in section 2 — don't invent comparisons.
