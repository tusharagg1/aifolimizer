---
name: top-trades-today
description: Surface the top N actionable trade ideas for today across holdings + watchlist, each with entry, stop, target, R:R and conviction. Use when the user asks "top trades today", "what should I trade today", "best entries right now", "top stock picks", or wants a ranked, decision-ready trade shortlist. Composes get_trade_ideas + crowding + macro guards. One-shot.
requires_profile: true
---

# Top Trades Today (ranked, decision-ready)

## Goal
Short ranked list of names worth trading TODAY - held or watchlist - each with explicit entry / stop / target / R:R / conviction. Built on the same scoring engine as the dashboard signals (`get_trade_ideas`), then gated by crowding + macro catalysts. Designed to be pushed to Telegram each morning.

## How to run
Call `get_profile` FIRST (accounts, capital - never hardcode). Then in parallel:

1. `mcp__aifolimizer__get_trade_ideas` with `top_n=5, include_watchlist=true, min_risk_reward=1.5`
2. `mcp__aifolimizer__get_macro_snapshot` (regime + scheduled catalysts)
3. `mcp__aifolimizer__get_positioning_signals` with the symbols returned by step 1 (crowding guard)

## Catalyst gate (FIRST - before ranking)
Check macro snapshot for events TODAY (US Eastern): FOMC, CPI, NFP, PCE, GDP advance, VIX > 25. If a major catalyst hits today, prefix the headline `⚠️ CATALYST: <event> @ <time>` and add: "Halve intraday size or wait until after the print." Do not suppress the list - annotate it.

## Crowding guard (per CLAUDE.md)
For each idea, cross-check `get_positioning_signals`:
- `crowding_label == consensus` (score ≥ 70) AND action is BUY/ADD → downgrade conviction one notch, tag `consensus-crowded - late-entry risk`. Do NOT promote a crowded name to the top slot on a BUY.
- `crowding_label == contrarian` (score ≤ 30) AND fundamentals/score support → tag `contrarian setup` (favourable).
- TSX (.TO) names with null crowding → treat as neutral, don't over-weight the absence.

## Output structure (≤ 350 words, Telegram-friendly)

### Headline
`Top N trades · [Regime label] · [catalyst flag or "no catalyst today"]`

### Ranked ideas (one block per idea, best first)
```
N. TICKER (held / watch) · ACTION · conviction
   entry ~$X · stop $Y · target $Z · R:R N.N
   why: <one line - the strongest reason>
   flag: <consensus-crowded / contrarian / earnings in Nd / none>
```
Use the idea's own `current_price` as entry reference; if `entry_timing == acceptable`, say "entry acceptable now"; the engine already excludes wait-for-pullback names.

### Skipped / thin
One line: how many names scored, how many were actionable, and why the rest dropped (e.g. "12 scored, 4 actionable; rest HOLD/WATCH or R:R < 1.5").

## Rules
- Direct. No hedging. If `get_trade_ideas` returns zero ideas, say so plainly: "No actionable setups today - all names HOLD/WATCH or below R:R floor." Do not invent trades.
- Entry/stop/target come straight from `get_trade_ideas` - do NOT recompute or round differently.
- Respect account context from `get_profile` for tax framing only; ranking is account-agnostic.
- One-shot - do not chain into another skill unless the user asks. For a deep dive on one name, suggest `/stock-analysis TICKER` or `/adversarial-research TICKER`.

## Gotchas
- `get_trade_ideas` already filters `wait_pullback` and `risk_reward < min_risk_reward` - don't re-add names it dropped.
- SELL/TRIM ideas can appear (held names) alongside BUY/ADD - label the action clearly; a SELL is a trade too.
- Crypto + ETFs may surface with thin reasons - keep them but note "index/crypto - different dynamics."
- `risk_reward` may be null for TRIM (target/RR intentionally omitted by the engine) - show stop only, omit R:R, don't treat null as an error.
- Watchlist names have weight 0 - that's expected (not held), not a data gap.
