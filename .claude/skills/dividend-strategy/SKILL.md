---
name: dividend-strategy
description: Harvard Endowment-style dividend income analysis - yield, payout-ratio safety, DRIP projection, and tax-account placement for income holdings. Use when the user asks about dividends, passive income, DRIP, yield, payout ratio, dividend safety, or "what should I hold for income?".
---

# Dividend Strategy (Harvard Endowment style)

## Stage 0 - Decision Memory (load FIRST)

Before analysis, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` - portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`).

Reconciliation rule: if a prior decision exists and your new read flips it, state explicitly WHY it changed (new data / catalyst / price move). Never silently contradict a logged decision - that drift is exactly what this prevents.

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types and cash balances. TFSA/RRSP/Non-Reg placement determines dividend tax treatment
2. Call `mcp__aifolimizer__get_personal_context` - province, marginal tax rate, account waterfall, FHSA. Grounds §6 tax-placement (which payer in which account) and after-tax yield. If `present=false`, flag placement advice as generic and suggest profile-setup
3. Call `mcp__aifolimizer__get_portfolio` - identify dividend-paying holdings
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[]` (top 15 by weight) - extracts dividend_yield, payout_ratio, dividend_growth_streak, eps_ttm for all holdings
5. Use MCP data as primary source for yield, payout ratio, dividend growth streak
6. WebSearch only for: specific DRIP calculator projections and new dividend stock recommendations not in current portfolio
7. Before recommending any NEW dividend name as an initiate/BUY (§4), call `mcp__aifolimizer__get_positioning_signals` with `symbols=[candidate tickers]`. If `crowding_score >= 70` the name is consensus-crowded (late entry = negative expected alpha) - defer the add or pick a less-crowded payer with comparable yield/safety; favor `crowding_score <= 30` when the dividend is safe

## Investor profile

- Canadian retail investor
- Dividend investing is one pillar (not whole portfolio)
- Account types and capital: always read from `get_profile` - never hardcode
- TFSA: dividends tax-free (Canadian stocks ideal here)
- RRSP: avoids 15% US withholding on US dividends (US dividend stocks ideal here)
- Non-Reg: Canadian dividends get dividend tax credit; US dividends fully taxable

## Output structure

1. **Per current dividend holding:** yield, dividend safety score (1-10), payout ratio, consecutive years of growth
2. **Unsustainable dividend flags** - payout ratio >80% or declining earnings
3. **10-year DRIP reinvestment projection** for current dividend holdings (show math)
4. **5 new dividend stock recommendations** (Canadian or US, with tickers + yield)
5. **Sector diversification** of dividend income - flag concentration
6. **Tax placement recommendations** - which payers belong in TFSA vs RRSP vs Non-Reg
7. **Projected annual dividend income** from current + recommended adds
8. **Ranked list** from safest to highest-yield

## After output - log decisions

For each new ticker recommended (initiate) AND any existing holding flagged unsustainable (TRIM/EXIT), call `mcp__aifolimizer__log_recommendation` with `skill="dividend-strategy"` (the param is `skill`, not `skill_used` - that belongs to `log_trade_decision`), `ticker`, `action` (BUY/HOLD/TRIM/SELL), `conviction` (HIGH/MED/LOW), `target_pct` + `stop_pct` (% from entry - the schema takes percentages, not absolute prices; entry is logged live at call time), `rationale` 1-line (yield + safety + tax placement). Feeds forward win-rate / track-record loop.

## Rules

- Format as dividend blueprint with income projection table
- Under 500 words
- Always factor in actual account types from `get_profile`

## Gotchas

- `payout_ratio` negative or >100% when EPS negative or near zero - flag as "unmeaningful", do NOT label dividend "unsustainable" purely from ratio.
- `dividend_growth_streak` from yfinance breaks on corporate actions (splits, spinoffs, ticker changes) - verify via WebSearch for any streak claim ≥10 years.
- US dividends in TFSA incur 15% non-recoverable withholding tax - TFSA-specific cost user often misses. Always call out.
- RRSP exempts US-listed US dividends from withholding only via US-Canada treaty; CDN-listed US-business ETFs (e.g. VFV) do NOT qualify - they leak 15%.
- Canadian dividend tax credit only applies to eligible dividends from Canadian corps; REIT distributions and US dividends do not qualify.
- DRIP projections assume constant yield and reinvestment price - state assumption, do NOT present as forecast.
