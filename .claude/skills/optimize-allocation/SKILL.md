---
name: optimize-allocation
description: Mean-variance (max-Sharpe) portfolio optimization over individual holdings. Use when the user asks "what are the optimal weights?", "how should I reweight?", "rebalance for best risk-adjusted return", "efficient frontier", "which positions to add/trim". For the active trading/holdings bucket — NOT the boring-core DCA sleeve (use auto-rebalance for that).
---

# Optimize Allocation (Max-Sharpe / Black-Litterman)

## Goal

Compute the optimal weight per holding and the concrete add/trim changes vs the
current book that maximise risk-adjusted return. Engine: PyPortfolioOpt
Efficient Frontier with Ledoit-Wolf shrinkage covariance, longs-only, 35% cap
per name. Analyst price targets are blended as Black-Litterman views when
available.

This is the **trading-bucket** reweighting tool. It WILL suggest selling
overweighted names — distinct from `auto-rebalance`, which only adds new cash to
the long-term ETF core and never sells.

## When to invoke

- "What's the optimal allocation / optimal weights?"
- "How much of each should I add or trim?"
- "Rebalance my holdings for best risk-adjusted return"
- After a large drift, new capital, or a thesis change across multiple names

## How to run

**Step 1 — Profile + regime (call FIRST):**
1. `mcp__aifolimizer__get_profile` — account types, capital (never hardcode)
2. `mcp__aifolimizer__get_market_breadth` — regime; if `bear_high_fear`, flag that
   max-Sharpe on trailing returns can over-tilt to recent winners

**Step 2 — Optimize:**
3. `mcp__aifolimizer__optimize_portfolio` with `top_n=20`, `use_analyst_views=true`
   - returns `optimal_weights`, `changes` (add/trim per name), `sharpe_ratio`,
     `expected_annual_return_pct`, `expected_annual_volatility_pct`, `method`

**Step 3 — Sanity gate before presenting:**
4. `mcp__aifolimizer__get_positioning_signals` on any name the optimizer says
   INCREASE — if crowding score ≥70, defer the add (negative expected alpha for
   late entries) even if the optimizer favours it.
5. `mcp__aifolimizer__get_concentration_warnings` — confirm the optimal weights
   don't breach single-name/sector limits the user cares about.

## Output

- Table: symbol | current % | optimal % | change | action, sorted by |change|
- Expected return / vol / Sharpe of the optimal book vs current
- Method used (`black_litterman` if analyst views applied, else `mean_historical`)
- Tax note: in Non-Reg, trimming winners realizes 50%-inclusion cap gains —
  prefer routing trims through registered accounts or new-cash adds where possible

## Caveats (state these)

- Mean-variance optimizes on **trailing** 2y returns — it tilts toward what has
  already worked. Treat as one input, not an order.
- `missing_symbols` in the result had insufficient price history and were
  excluded — call them out so the user knows they weren't optimized.
- Optimal weights are % only; position sizing in dollars stays with the user.
