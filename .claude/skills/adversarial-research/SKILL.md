---
name: adversarial-research
description: Run a parallel bull/bear adversarial research pipeline on a specific ticker. Use when the user asks for "adversarial research", "bull bear analysis", "deep research on [ticker]", "should I buy X?", or wants a rigorous debate-style investment thesis. Fetches live data via aifolimizer MCP then spawns parallel sub-agents.
---

# Adversarial Research Pipeline (Stage 1-5)

Modelled on a multi-agent hedge fund workflow. Two sub-agents argue opposing sides simultaneously. You synthesize into probability-weighted scenarios.

## How to run

### Stage 0 — Profile

Call `mcp__aifolimizer__get_profile` first — account types, capital, and cash available. Used to tailor Canadian tax placement in Stage 4.

### Stage 1 — Data collection (call all 6 MCP tools in parallel)

1. Call `mcp__aifolimizer__get_portfolio` — confirm the ticker is held + get cost basis, weight, total return
2. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` — P/E, EPS, growth, dividend, analyst target, ownership
3. Call `mcp__aifolimizer__get_technicals` with `symbols=[ticker]` — SMA, RSI, MACD, Bollinger Bands, trend
4. Call `mcp__aifolimizer__get_news_headlines` with `ticker=ticker` — latest headlines
5. Call `mcp__aifolimizer__get_macro_snapshot` — rates, CPI, CAD/USD context
6. Call `mcp__aifolimizer__get_positioning_signals` with `symbols=[ticker]` — crowding score, institutional ownership, short interest, headline velocity (feeds Consensus agent in Stage 2)

### Stage 2 — Adversarial sub-agents (spawn ALL THREE in parallel via Agent tool)

**Bull Agent prompt:**
> You are a senior equity analyst at a long-only growth fund. Given the following data for [TICKER], construct the strongest possible 12-month bull case. No hedging. Be specific about catalysts, price targets, and entry rationale. Data: [paste full fundamentals + technicals + news + macro snapshot]

**Bear Agent prompt:**
> You are a short-seller at a quantitative hedge fund. Given the following data for [TICKER], construct the strongest possible bear case for the next 12 months. No hedging. Be specific about failure modes, downside targets, and what would trigger a sell. Data: [paste full fundamentals + technicals + news + macro snapshot]

**Consensus Agent prompt (NEW — crowding/positioning lens):**
> You are a positioning analyst at a multi-strategy hedge fund. Given the positioning data for [TICKER] (institutional ownership, short interest, analyst coverage, headline velocity, crowding score), determine: (1) Is this name already consensus-crowded by AI-driven retail + quant flows? (2) What is the marginal buyer thesis — who is left to buy if everyone is already long? (3) What's the contrarian view that current price ignores? (4) If consensus is wrong, what's the unwind path (which holders sell first, where does it cascade)? No hedging. Data: [paste positioning_signals + fundamentals + news]

Run all three agents in parallel — do NOT wait between spawns.

### Stage 3 — Scenario modeling

Build three scenarios from the agent outputs:

| Scenario | Probability | Price Target | Key Driver |
|----------|------------|--------------|------------|
| Bull     | 35%        | $X           | [catalyst] |
| Base     | 40%        | $X           | [trend]    |
| Bear     | 25%        | $X           | [risk]     |

Probability-weighted expected value = (bull_target × 0.35) + (base_target × 0.40) + (bear_target × 0.25)

### Stage 4 — Decision output

Format the final output as:

---
**[TICKER] — Adversarial Research Summary**

**Decision summary (read this first):**
[2-3 sentences: recommended action, conviction level, primary reason]

**Probability-weighted EV:** $X vs current price $X → implied upside/downside: X%

**Bull case (35%):** [Key thesis in 3 bullets]
**Bear case (25%):** [Key risks in 3 bullets]
**Base case (40%):** [Most likely path in 2 bullets]
**Consensus / crowding read:** [crowding score + label; marginal-buyer thesis in 1 line; "edge already priced" or "contrarian opportunity" verdict]

**Entry zone:** $X–$X
**Stop-loss:** $X (invalidates bull thesis if breached)
**12-month target:** $X

**Canadian context:**
- Optimal account: TFSA (capital gains tax-free) / RRSP (US withholding applies) / Non-Reg
- Tax note: [relevant based on account types from get_profile]

**Confidence rating:** Strong Buy / Buy / Neutral / Sell / Strong Sell
---

## Rules

- Always run Stage 2 agents in parallel (single message, THREE Agent tool calls — Bull, Bear, Consensus)
- Never invent data — if MCP returns empty for a field, note "data unavailable"
- Keep the full output under 700 words
- Reference the user's actual cost basis and current portfolio weight in the decision framing
- For Canadian tickers (.TO suffix): use TSX context and note CAD/USD impact if applicable

## Gotchas

- Bull/Bear agents MUST see the same data snapshot — collect Stage 1 first, then pass identical data to both. Asymmetric inputs invalidate the comparison.
- Spawn both agents in ONE message with two tool calls; sequential spawning is not parallel and wastes context.
- Sub-agents must NOT hedge — if either agent returns "on the other hand..." reasoning, the prompt failed. Reject and re-prompt.
- Probability weights (35/40/25) are a default — adjust to evidence, do NOT force the template when data clearly leans one way.
- `get_macro_snapshot` cached 12h — for rate-decision-week analyses, WebSearch before relying on it.
- Stop-loss must invalidate the BULL thesis specifically, not just be a generic % drop — tie to a thesis breakpoint (e.g. "below 200-SMA breaks the uptrend assumption").
- Confidence rating must reflect data completeness — if 2+ MCP fields are null, max rating is Neutral.
- Consensus agent should NOT default to bearish — a crowded long can keep working. Its job is to surface the marginal-buyer thesis, not to argue short. Reject its output if it just restates the Bear case.
- When `crowding_score >= 70` AND `Bull case` is the dominant scenario, downgrade confidence one notch (consensus risk on late entries).
- When `crowding_score <= 30` AND `Bull case` is dominant AND data complete, this is a contrarian setup — flag explicitly.
