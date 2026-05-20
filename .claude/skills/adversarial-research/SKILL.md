---
name: adversarial-research
description: Run a parallel bull/bear adversarial research pipeline on a specific ticker. Use when the user asks for "adversarial research", "bull bear analysis", "deep research on [ticker]", "should I buy X?", or wants a rigorous debate-style investment thesis. Fetches live data via aifolimizer MCP then spawns parallel sub-agents.
---

# Adversarial Research Pipeline (Stage 0–5)

Modelled on TradingAgents multi-agent hedge fund workflow. Explicit DAG: memory recall → parallel data → parallel advocates → three-tier risk debate → portfolio manager synthesis → log decision. Each layer waits for prior layer to complete.

```
Layer 0 (serial):   get_profile + get_ticker_decision_history + get_cross_ticker_lessons + recall_preferences
Layer 1 (parallel): get_portfolio | get_fundamentals | get_technicals |
                    get_news_headlines | get_macro_snapshot | get_positioning_signals |
                    get_stocktwits_sentiment | get_community_sentiment
Layer 2 (parallel): Bull Agent | Bear Agent | Consensus Agent
                    (all receive identical Layer 1 snapshot + Layer 0 memory context)
Layer 3 (parallel): Risk Aggressive | Risk Neutral | Risk Conservative
                    (all receive Layer 1 snapshot + Layer 2 outputs)
Layer 4 (serial):   Portfolio Manager synthesis (this context window)
Layer 5 (serial):   Decision output + log_trade_decision
```

Rules for DAG execution:
- Layer 0: 4 MCP calls in ONE message (true parallel)
- Layer 1: 8 MCP calls in ONE message (true parallel)
- Layer 2: 3 Agent calls in ONE message (true parallel); pass identical data to all three
- Layer 3: 3 Agent calls in ONE message (true parallel); pass Layer 2 outputs + Layer 1 data
- Layer 4+5: synthesize in main context; do NOT spawn more agents

## Stage 0 — Memory & Profile (call all 4 in parallel)

1. `mcp__aifolimizer__get_profile` — account types, capital, cash available
2. `mcp__aifolimizer__get_ticker_decision_history` with `ticker=TICKER, max_decisions=5` — past decisions, outcomes, reflections
3. `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` — portfolio-level win/loss patterns
4. `mcp__aifolimizer__recall_preferences` with `query="TICKER investment"` — stored investor preferences for this name

**Memory injection rule**: If Stage 0 returns past decisions for this ticker, prepend a short summary to ALL Layer 2 agent prompts:
> "Past decisions on [TICKER]: [date] [action] → [outcome] ([pnl]%). Reflection: [text]. Do NOT repeat failed thesis unless new data overrides it."

If cross-ticker lessons contain patterns relevant to this ticker (same sector, same setup), inject them too.

## Stage 1 — Data collection (call all 8 in parallel)

1. `mcp__aifolimizer__get_portfolio` — confirm ticker is held + cost basis, weight, total return
2. `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` — P/E, EPS, growth, dividend, analyst target
3. `mcp__aifolimizer__get_technicals` with `symbols=[ticker]` — SMA, RSI, MACD, Bollinger Bands, trend, ATR
4. `mcp__aifolimizer__get_news_headlines` with `ticker=ticker` — latest headlines
5. `mcp__aifolimizer__get_macro_snapshot` — rates, CPI, CAD/USD context
6. `mcp__aifolimizer__get_positioning_signals` with `symbols=[ticker]` — crowding score, institutional ownership, short interest
7. `mcp__aifolimizer__get_stocktwits_sentiment` with `ticker=ticker` — real-time retail labeled sentiment
8. `mcp__aifolimizer__get_community_sentiment` with `ticker=ticker` — Reddit community signal

## Stage 2 — Adversarial sub-agents (spawn ALL THREE in parallel)

Pass each agent: full Layer 1 data snapshot + memory context from Stage 0.

**Bull Agent prompt:**
> [MEMORY CONTEXT IF ANY]
> You are a senior equity analyst at a long-only growth fund. Given the following data for [TICKER], construct the strongest possible 12-month bull case. No hedging. Be specific about catalysts, price targets, and entry rationale. Data: [paste full fundamentals + technicals + news + macro + stocktwits + reddit]

**Bear Agent prompt:**
> [MEMORY CONTEXT IF ANY]
> You are a short-seller at a quantitative hedge fund. Given the following data for [TICKER], construct the strongest possible bear case for the next 12 months. No hedging. Be specific about failure modes, downside targets, and what would trigger a sell. Data: [paste full fundamentals + technicals + news + macro + stocktwits + reddit]

**Consensus Agent prompt (crowding + retail sentiment lens):**
> [MEMORY CONTEXT IF ANY]
> You are a positioning analyst at a multi-strategy hedge fund. Given the positioning and sentiment data for [TICKER] (institutional crowding, short interest, StockTwits bull/bear count, Reddit community score, analyst coverage, headline velocity), determine: (1) Is this name consensus-crowded by AI-driven retail + quant flows? (2) What does the labeled StockTwits sentiment say — is retail positioned ahead of or behind the move? (3) What is the marginal buyer thesis — who is left to buy? (4) What's the contrarian view that current price ignores? (5) If consensus is wrong, what's the unwind path? No hedging. Data: [paste positioning_signals + stocktwits + reddit + fundamentals + news]

## Stage 3 — Three-tier risk debate (spawn ALL THREE in parallel)

Receives: full Layer 1 data + Bull/Bear/Consensus outputs from Stage 2.

**Aggressive Risk Analyst prompt:**
> You are an aggressive risk analyst at a growth hedge fund. Your mandate: maximize upside capture, accept higher volatility. Given the bull/bear debate outputs and portfolio data for [TICKER], argue for the most aggressive position size and entry that is still within Kelly criterion bounds. State: (1) recommended size as % of portfolio (max Kelly), (2) why the bull thesis justifies the risk, (3) what would change your mind. Data: [paste portfolio weights + technicals + positioning + bull/bear outputs]

**Neutral Risk Analyst prompt:**
> You are a risk-neutral portfolio analyst. Your mandate: balance upside with drawdown protection. Given the bull/bear debate and portfolio data for [TICKER], argue for a moderate position size that respects both the opportunity and the downside. State: (1) recommended size as % of portfolio (half-Kelly), (2) how you weight the bull vs bear thesis, (3) what guardrails you'd set. Data: [paste portfolio weights + technicals + positioning + bull/bear outputs]

**Conservative Risk Analyst prompt:**
> You are a conservative risk manager at a pension-influenced multi-asset fund. Your mandate: capital preservation first, participation second. Given the bull/bear debate and portfolio data for [TICKER], argue for the smallest position size that captures meaningful upside without meaningful drawdown. State: (1) recommended size as % of portfolio (quarter-Kelly or flat), (2) what specific risks make you cautious, (3) VETO conditions (state VETO if: existing weight >8%, beta >2.0 in fear regime, no viable stop, crowding_score ≥85). Data: [paste portfolio weights + technicals + positioning + bull/bear outputs]

## Stage 4 — Portfolio Manager synthesis

Portfolio Manager (main context window) synthesizes all six agent outputs:

**Decision framework:**
1. Read Bull, Bear, Consensus outputs → assign scenario probabilities (default 35/40/25; adjust to evidence)
2. Read Aggressive, Neutral, Conservative risk outputs → if Conservative issues VETO, state VETO and exit
3. If no VETO: size = median of three recommended sizes, capped at Conservative's max
4. Compute probability-weighted EV: (bull_target × bull_prob) + (base_target × 0.40) + (bear_target × bear_prob)
5. Cross-check: does Stage 0 memory contain a recent failed thesis on this ticker? If yes, require new data to override it

**Scenario table:**

| Scenario | Probability | Price Target | Key Driver |
|----------|------------|--------------|------------|
| Bull     | 35%        | $X           | [catalyst] |
| Base     | 40%        | $X           | [trend]    |
| Bear     | 25%        | $X           | [risk]     |

## Stage 5 — Decision output + log

Format final output:

---
**[TICKER] — Adversarial Research Summary**

**Decision summary (read this first):**
[2-3 sentences: recommended action, conviction level, primary reason]

**Past decision context:** [If Stage 0 found prior decisions: "Last decision [date]: [action] → [outcome]. [reflection]. This analysis [confirms/overrides] that view because [reason]." If no prior: "First analysis of this ticker."]

**Probability-weighted EV:** $X vs current price $X → implied upside/downside: X%

**Bull case (X%):** [3 bullets]
**Bear case (X%):** [3 bullets]
**Base case (40%):** [2 bullets]
**Sentiment read:** [StockTwits bull/bear count + community_score; Reddit community_score; divergence note if retail vs institutional positioning differs]
**Consensus / crowding:** [crowding score + label; marginal-buyer thesis; "edge already priced" or "contrarian opportunity"]

**Risk debate verdict:** [Aggressive: X% / Neutral: X% / Conservative: X% — final size: X% (~$Y). VETO reason if applicable.]

**Entry zone:** $X–$X
**Stop-loss:** $X (invalidates bull thesis if breached)
**12-month target:** $X

**Canadian context:**
- Optimal account: TFSA / RRSP / Non-Reg
- Tax note: [relevant based on account types from get_profile]

**Confidence rating:** Strong Buy / Buy / Neutral / Sell / Strong Sell
---

**After outputting:** Call `mcp__aifolimizer__log_trade_decision` with the final action, conviction, entry_price, target_price, stop_price, thesis_summary (1-2 sentences), skill_used="adversarial-research". This enables Phase B/C outcome tracking for future analyses.

## Rules

- Layer 0 memory calls are MANDATORY — never skip. A prior stop-out on this ticker that repeats the same thesis is a signal failure.
- Always run Stage 2 in parallel (3 Agent calls in one message)
- Always run Stage 3 in parallel (3 Agent calls in one message) — do NOT merge with Stage 2
- Never invent data — if MCP returns empty for field, note "data unavailable"
- Full output under 800 words
- Reference user's actual cost basis and current portfolio weight in decision framing
- For Canadian tickers (.TO suffix): use TSX context, note CAD/USD impact if applicable
- StockTwits and Reddit divergence is signal: retail bullish + institutional short = crowded long at risk; retail bearish + institutional long = contrarian setup

## Gotchas

- Bull/Bear/Consensus agents MUST see same data snapshot — collect Layer 1 first, pass identical data. Asymmetric inputs invalidate comparison.
- Spawn Layer 2 agents in ONE message (3 calls); spawn Layer 3 agents in ONE separate message (3 calls). Sequential spawning is not parallel.
- Sub-agents MUST NOT hedge — if either returns "on the other hand..." reasoning, prompt failed. Reject and re-prompt.
- Probability weights (35/40/25) are default — adjust to evidence; do NOT force template when data clearly leans one way.
- `get_macro_snapshot` cached 12h — for rate-decision-week analyses, WebSearch before relying on it.
- Stop-loss must invalidate BULL thesis specifically — tie to thesis breakpoint (e.g. "below 200-SMA breaks uptrend assumption"), not generic % drop.
- Confidence rating must reflect data completeness — if 2+ MCP fields null, max rating is Neutral.
- Consensus agent should NOT default to bearish — crowded long can keep working. Job is surface marginal-buyer thesis. Reject output if it just restates Bear case.
- Conservative analyst VETO is binding — Portfolio Manager cannot override a VETO. If VETO issued, state reason and do not size a position.
- StockTwits `.TO` tickers automatically strip suffix — no manual handling needed.
- `log_trade_decision` is NOT optional — call it after every completed analysis. Skipping breaks the Phase B/C feedback loop.
- When `crowding_score >= 70` AND `Bull case` dominant, downgrade confidence one notch (consensus risk on late entries).
- When `crowding_score <= 30` AND `Bull case` dominant AND data complete, contrarian setup — flag explicitly.
