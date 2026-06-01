---
name: daily-briefing
description: Run a one-shot morning portfolio digest that ties together health, alerts, macro regime, crowding risk, concentration, and earnings calendar into a single decision-ready brief. Use when the user asks for "morning briefing", "daily digest", "what's happening today?", "what changed overnight?", or "give me the morning rundown". Composes existing MCP tools - no new data sources.
---

# Daily Briefing (morning digest)

## Goal

One scroll-length brief surfacing what matters today. Composes 7 MCP tools. No new data fetches outside listed below.

## State check (BEFORE any tool calls)

Read `.claude/context/STATE.md`. If `last_briefing_date` equals today's date (YYYY-MM-DD), output:
> "Daily briefing already ran today (`last_briefing_date`). Skipping re-fetch to save tokens. Re-run with `/daily-briefing force` to override."
Then stop - do not call any MCP tools.

If `last_crowding_regime` is set, use it as prior context when interpreting positioning signals (flag if regime changed).

## How to run

Call in parallel (no inter-dependencies):

1. `mcp__aifolimizer__get_profile`
2. `mcp__aifolimizer__get_portfolio`
3. `mcp__aifolimizer__get_macro_snapshot` (FRED + market regime)
4. `mcp__aifolimizer__get_concentration_warnings`
5. `mcp__aifolimizer__get_triggered_alerts` (since_hours=24)
6. `mcp__aifolimizer__get_earnings_calendar` (next 14d)
7. `mcp__aifolimizer__get_positioning_signals` (top 15 holdings)
8. `mcp__aifolimizer__get_technicals_intraday` (top 5 holdings + any focus-list tickers - only if US market is open or pre-market)

## Catalyst day check (FIRST - before anything else)

Catalysts drive intraday moves. Trading without knowing today's catalysts = trading blind.

Check macro snapshot for these scheduled events TODAY (US Eastern):
- **FOMC decision / Fed minutes** (8 per year, 2pm ET)
- **CPI release** (monthly, 8:30am ET)
- **NFP / jobs report** (first Friday monthly, 8:30am ET)
- **PCE release** (monthly, 8:30am ET)
- **GDP advance** (quarterly, 8:30am ET)
- **VIX > 25** (elevated fear regime - wider stops, smaller size)
- **Treasury auction** (10Y/30Y reopening - bond-proxy stocks reactive)
- **Turn-of-Month window** (McConnell & Xu, 1897-2005): last trading day of month OR first 3 trading days of new month. Historically, essentially ALL positive equity returns are concentrated in this 4-day window. If today falls in this window, flag: `📅 TOTM WINDOW - bias long, avoid aggressive intraday shorts, favor momentum adds.`

Cross-reference earnings calendar for next 24h (large-cap names only): MSFT/AAPL/NVDA/META/GOOGL/AMZN reporting tonight = next-day gap risk across whole portfolio if mega-cap.

If catalyst flagged, prefix section 1 headline with `⚠️ CATALYST: <event> @ <time>` and add section 2 bullet: "Reduce intraday position sizes by 50% until event resolves. Wide stops or no trade."

## Investor profile

- Age: 32, Canadian resident
- Philosophy: growth stocks, index ETFs, dividends, crypto
- Always pull capital + accounts from `get_profile` - never hardcode

## Output structure

Single-page brief, ≤ 400 words, this exact order:

### 1. Headline (one sentence)
Format: `[Portfolio value CAD] · [Day Δ %] · [Regime label] · [N alerts last 24h]`
Example: `$182,300 CAD · −0.7% · bull_high_fear · 3 alerts last 24h`

### 2. What changed (≤ 5 bullets)
Action-significant items only. No filler.
- New triggered alerts (price drop, RSI, earnings, concentration)
- Crowding score regime shifts (if positioning history available - flag consensus → contrarian or vice versa)
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

### 5. Intraday addendum (only if US market open or pre-market)
From `get_technicals_intraday` on focus-list + top 5 by weight, surface:
- Names with `intraday_score >= 0.7` AND `volume_spike >= 1.5` → "active setup"
- Names with `opening_range_break == "below"` AND held weight > 3% → "intraday weakness on size"
- Gap names: `abs(gap_pct) >= 3` → "gap watch"
Format per line: `TICKER · intraday_score · VWAP $X.XX (Δ Y.Y%) · OR break: <dir> · note`
If no intraday signals worth surfacing, write: `No notable intraday setups`.

### 6. Skipped today
One line listing tools/checks that returned empty or stale data. Example: `Skipped: crypto (no holdings), tax-loss (no underwater positions), intraday (market closed)`.

## After output - write STATE.md

After completing the brief, update `.claude/context/STATE.md`:
- `last_briefing_date`: today's date (YYYY-MM-DD)
- `last_crowding_regime`: regime label from positioning signals (bullish/neutral/cautious)
- `active_alerts`: count of triggered alerts from `get_triggered_alerts`
- `open_recs`: count of open recommendations (from brief context if available)

## Rules

- Direct. No hedging, no "you may want to consider".
- If tool errors or returns empty, list in section 5 - do NOT fabricate data.
- Currency: CAD aggregate unless user in specific account.
- Crowding rule (per CLAUDE.md): if `crowding_label == consensus` AND ticker in focus list, default action skews toward `trim` or `hold`, NOT `add`.
- One-shot - do not chain into another skill unless user asks.

## Gotchas

- `get_triggered_alerts` read-only - does NOT re-evaluate rules. If user wants fresh evaluation, call `run_alerts_now` first (only if explicitly requested - adds 5-10s).
- TSX (.TO) tickers may have null crowding fields → label "neutral" by fallback. Don't treat as real signal.
- Macro regime label `bull_low_fear` can co-exist with portfolio-level red day - regime is market-wide, day-change is portfolio-specific. Don't conflate.
- Earnings calendar is yfinance - sparse for non-US listings. Cross-check IR pages for TSX names if stake is large.
- "What changed" requires comparing to prior brief. If running fresh (no prior), say so in section 2 - don't invent comparisons.
- `get_technicals_intraday` returns empty dict outside US market hours - that is correct, list "intraday (market closed)" in skipped section, do not error
- Catalyst check is REQUIRED - never skip it. If macro snapshot data is unavailable, default to assuming a quiet day BUT explicitly state "catalyst data unavailable - assume normal" in the headline. Better to be wrong about a quiet day than to miss a Fed day.
- Earnings catalyst list (mega-caps reporting in next 24h) only matters for big names - TSX small-caps reporting do not move SPY. Filter `get_earnings_calendar` to weight > 2% of portfolio OR market_cap > $500B before flagging as portfolio-wide catalyst.
