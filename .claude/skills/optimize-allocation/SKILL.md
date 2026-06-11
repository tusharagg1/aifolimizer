---
name: optimize-allocation
description: Mean-variance (max-Sharpe) portfolio optimization over individual holdings. Use when the user asks "what are the optimal weights?", "how should I reweight?", "rebalance for best risk-adjusted return", "efficient frontier", "which positions to add/trim". For the active trading/holdings bucket - NOT the boring-core DCA sleeve (use auto-rebalance for that).
---

# Optimize Allocation (Max-Sharpe / Black-Litterman)

## Goal

Compute the optimal weight per holding and the concrete add/trim changes vs the
current book that maximise risk-adjusted return. Engine: PyPortfolioOpt
Efficient Frontier with Ledoit-Wolf shrinkage covariance, longs-only, 35% cap
per name. Analyst price targets are blended as Black-Litterman views when
available.

This is the **trading-bucket** reweighting tool. It WILL suggest selling
overweighted names - distinct from `auto-rebalance`, which only adds new cash to
the long-term ETF core and never sells.

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) - portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable reweight: for each BUY/SELL/TRIM/ADD issued, call `mcp__aifolimizer__log_recommendation` (`skill="optimize-allocation", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## When to invoke

- "What's the optimal allocation / optimal weights?"
- "How much of each should I add or trim?"
- "Rebalance my holdings for best risk-adjusted return"
- After a large drift, new capital, or a thesis change across multiple names

## How to run

**Step 1 - Profile + regime (call FIRST):**
1. `mcp__aifolimizer__get_profile` - account types, capital (never hardcode)
2. `mcp__aifolimizer__get_personal_context` - ground the Non-Reg-vs-registered tax
   note in the user's actual province / `marginal_tax_rate_pct` / `account_waterfall`
   instead of generic text. If `present=false`, keep the note generic and suggest
   running `profile-setup`.
3. `mcp__aifolimizer__get_market_breadth` - regime; if `bear_high_fear`, flag that
   max-Sharpe on trailing returns can over-tilt to recent winners

**Step 2 - Optimize:**
4. `mcp__aifolimizer__optimize_portfolio` with `top_n=20`, `use_analyst_views=true`
   - returns `optimal_weights`, `changes` (add/trim per name), `sharpe_ratio`,
     `expected_annual_return_pct`, `expected_annual_volatility_pct`, `method`

**Step 3 - Sanity gate before presenting:**
5. `mcp__aifolimizer__get_positioning_signals` on any name the optimizer says
   INCREASE - if crowding score ≥70, defer the add (negative expected alpha for
   late entries) even if the optimizer favours it.
6. `mcp__aifolimizer__get_concentration_warnings` - confirm the optimal weights
   don't breach single-name/sector limits the user cares about.

## Output

- Table: symbol | current % | optimal % | change | action, sorted by |change|
- Expected return / vol / Sharpe of the optimal book vs current
- Method used (`black_litterman` if analyst views applied, else `mean_historical`)
- Tax note: in Non-Reg, trimming winners realizes 50%-inclusion cap gains -
  prefer routing trims through registered accounts or new-cash adds where possible

## Caveats (state these)

- Mean-variance optimizes on **trailing** 2y returns - it tilts toward what has
  already worked. Treat as one input, not an order.
- `missing_symbols` in the result had insufficient price history and were
  excluded - call them out so the user knows they weren't optimized.
- Optimal weights are % only; position sizing in dollars stays with the user.
