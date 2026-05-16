---
name: dividend-strategy
description: Run a Harvard Endowment dividend income analysis. Use when the user asks about dividends, passive income, DRIP, yield, payout ratio, or "what should I hold for income?". Fetches portfolio via aifolimizer MCP.
---

# Dividend Strategy (Harvard Endowment style)

## How to run

1. Call `mcp__aifolimizer__get_profile` — account types and cash balances. TFSA/RRSP/Non-Reg placement determines dividend tax treatment
2. Call `mcp__aifolimizer__get_portfolio` to identify dividend-paying holdings
3. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[]` (top 15 by weight) — extracts dividend_yield, payout_ratio, dividend_growth_streak, eps_ttm for all holdings
4. Use MCP data as primary source for yield, payout ratio, and dividend growth streak
5. WebSearch only for: specific DRIP calculator projections and new dividend stock recommendations not in current portfolio

## Investor profile

- Age: 32, Canadian resident
- Dividend investing is one pillar (not the whole portfolio)
- Account types and capital: always read from `get_profile` — never hardcode
- TFSA: dividends tax-free (Canadian stocks ideal here)
- RRSP: avoids 15% US withholding on US dividends (US dividend stocks ideal here)
- Non-Reg: Canadian dividends get the dividend tax credit; US dividends fully taxable

## Output structure

1. **Per current dividend holding:** yield, dividend safety score (1-10), payout ratio, consecutive years of growth
2. **Unsustainable dividend flags** — payout ratio >80% or declining earnings
3. **10-year DRIP reinvestment projection** for current dividend holdings (show math)
4. **5 new dividend stock recommendations** (Canadian or US, with tickers + yield)
5. **Sector diversification** of dividend income — flag concentration
6. **Tax placement recommendations** — which payers belong in TFSA vs RRSP vs Non-Reg
7. **Projected annual dividend income** from current + recommended adds
8. **Ranked list** from safest to highest-yield

## Rules

- Format as dividend blueprint with income projection table
- Under 500 words
- Always factor in actual account types from `get_profile`

## Gotchas

- `payout_ratio` is negative or >100% when EPS is negative or near zero — flag as "unmeaningful", do NOT label the dividend "unsustainable" purely from the ratio.
- `dividend_growth_streak` from yfinance breaks on corporate actions (splits, spinoffs, ticker changes) — verify via WebSearch for any streak claim ≥10 years.
- US dividends in TFSA incur 15% non-recoverable withholding tax — this is a TFSA-specific cost the user often misses. Always call out.
- RRSP exempts US-listed US dividends from withholding only via the US-Canada treaty; CDN-listed US-business ETFs (e.g. VFV) do NOT qualify — they leak the 15%.
- Canadian dividend tax credit only applies to eligible dividends from Canadian corps; REIT distributions and US dividends do not qualify.
- DRIP projections assume constant yield and reinvestment price — state assumption, do NOT present as forecast.
