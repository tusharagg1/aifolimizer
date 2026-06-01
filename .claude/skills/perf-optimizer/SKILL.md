---
name: perf-optimizer
description: Profile and optimize backend hotpaths (FastAPI handlers, MCP tools, services, caching layers) for latency, memory, and throughput. Use when the user asks "why is X slow?", "optimize Y", "this endpoint is slow", "reduce memory", "speed up the MCP server", or names a specific tool/route and complains about performance. Refuses to optimize code that's already fast enough or where the rewrite is not measurably better than the original.
requires_profile: false
---

# Performance Optimizer

Senior performance engineer mode. Backend hotpath focus - FastAPI routes (`app/api/ws.py`), MCP tools (`mcp_server.py`), service modules (`app/services/*`), cache layers (L1 dict + L2 diskcache). **Hard rule: replace existing code only when measurably better. No speculative rewrites.**

## Scope

In-scope:
- FastAPI handlers and MCP tools - request latency, payload size, serialization cost
- Service-layer functions - yfinance/FRED/CoinGecko fetches, technicals computation, portfolio enrichment
- Caching - L1 (in-process dict) and L2 (diskcache) hit rates, TTL tuning, key design
- Concurrency - `asyncio.gather` batching, blocking-call detection, thread/process pool usage
- Data structures - pandas vs dict, repeated `iterrows`, dict copies, list comprehensions vs generators

Out-of-scope (refuse):
- Frontend perf (project is backend-only as of bac7241)
- Algorithmic rewrites of financial logic (technical indicators, risk math) - correctness first, not perf
- Infra changes (deploy, k8s, containers) - single-user local app

## How to run

### Stage 0 - Identify hotpath (measure, never guess)

1. Ask user: which path is slow? (endpoint, MCP tool name, script, batch job). Refuse to "audit everything for perf" - too broad, low signal.
2. Confirm reproduction: how to trigger, expected latency, observed latency. If user has no measurement, ask them to time it first.
3. Use graph tools to map the hotpath:
   - `semantic_search_nodes` for entry function
   - `query_graph` with pattern=`callees_of` to follow downstream calls
   - `get_impact_radius` to scope changed-code blast radius
4. Read the actual files only after graph reveals the call tree. Do NOT pre-read files.

### Stage 1 - Profile (real numbers, not vibes)

For Python hotpath, instrument with one of:
- `time.perf_counter()` around suspected slow blocks (cheapest, surgical)
- `cProfile` for full function profile: `python -m cProfile -o out.prof script.py` then `snakeviz out.prof`
- `tracemalloc` for memory: `tracemalloc.start(); ... ; tracemalloc.get_traced_memory()`
- `py-spy top --pid <pid>` for live sampling against running uvicorn (no code change)

Report **observed numbers** before proposing changes:
```
Baseline:
- p50 latency: X ms
- p95 latency: Y ms
- peak RSS: Z MB
- hot frames: [top 3 from profile]
```

If you cannot measure (no repro, no profiler output), STOP and ask user for baseline. Do not propose optimizations against imagined slowness.

### Stage 2 - Diagnose

Map observed cost to root cause. Common patterns in this codebase:
- **Serial network calls** that could be `asyncio.gather` - yfinance batch, multi-ticker fetches
- **Cache misses** on L1+L2 - wrong key, too-short TTL, key includes timestamp
- **Blocking sync calls inside async** - `requests.get` in async route, `time.sleep`
- **Repeated work per request** - recomputing same DataFrame, re-parsing same JSON
- **Wide payloads** - returning full DataFrame instead of needed columns, no pagination
- **Pandas churn** - `.iterrows()`, `.apply()` where vectorized op exists, repeated `.copy()`
- **PII filter overhead** - verify it's the hot frame before optimizing (correctness-critical, don't shave)

### Stage 3 - Propose fix (only if measurably better)

For each proposed change, deliver:
```
Hotspot: <file:line>
Observation: <baseline number>
Root cause: <one sentence>
Proposed change: <one sentence>
Expected gain: <estimated ms or MB, with reasoning>
Risk: <correctness/cache/concurrency risk, or "none">
```

**Refusal conditions - drop the proposed change:**
- Expected gain < 10% of total latency AND change touches 3+ files → not worth complexity cost
- Change adds dependency just to shave ms → reject unless gain is order-of-magnitude
- Optimization removes readability without measured payoff → caveman rule (technical substance stays; perf only when real)
- Cannot articulate WHY the new code is faster → reject; don't cargo-cult

### Stage 4 - Implement + verify

1. Make the change surgical (touch only the hotpath).
2. Re-run the SAME measurement from Stage 1. Report:
   ```
   Result:
   - p50: X → X' ms (Δ -N%)
   - p95: Y → Y' ms (Δ -N%)
   - peak RSS: Z → Z' MB
   ```
3. If gain < projected by >2x, ROLL BACK. The model was wrong; explain why before trying again.
4. If correctness uncertain (e.g. changed cache key), run smoke: `python -c "from app.services.X import Y; print(Y(...))"` against real input.

### Stage 5 - Append lesson

After completed optimization, append one line to `.claude/context/lessons.md`:
> Perf: <hotspot> was slow because <root cause>. Fixed by <change>. Gain: <number>.

## Rules

- **Measure before, measure after.** No optimization ships without a number.
- **Refuse fake wins.** If the rewrite is "cleaner" but not measurably faster, reject it. User asked for perf, not aesthetics.
- **Keep correctness.** Technical indicators, risk math, PII filter - never alter behavior for perf without explicit user OK.
- **One hotspot per session.** Don't fan out to "while I'm here" optimizations. Surgical only (project CLAUDE.md rule).
- **Cache changes are dangerous.** Wrong key = stale data leaks. New TTL = freshness contract change. Flag explicitly.
- **Async correctness > async speed.** `asyncio.gather` on stateful calls (token refresh, write paths) can race. Verify each gathered call is read-only.
- **No new abstractions for perf.** Don't introduce a "PerfCache" wrapper class when `functools.lru_cache` suffices.
- Output total: under 600 words excluding code blocks.

## Gotchas

- yfinance has internal rate limits - parallelizing past ~5 concurrent requests triggers throttling and gets SLOWER, not faster. Test before fanning out.
- L1 dict cache is per-process - MCP and FastAPI have separate L1s. Only L2 (diskcache) is shared. Don't "fix" L1 cache miss by making L1 cross-process; use L2.
- `pii_filter.py` runs on every MCP response - if profiler shows it hot, the answer is usually "cache the filtered output upstream", not "make the filter faster". Filter is correctness-critical.
- `pandas` import alone is ~200ms cold - if startup is the complaint, lazy-import inside functions instead of module top.
- `yfinance.Ticker().history()` cache lives inside yfinance - adding our own L1 around it can double-cache and waste RAM. Profile before wrapping.
- FastAPI response model validation (`pydantic`) can be 30%+ of latency on wide payloads. If hot, consider `response_model=None` for internal endpoints, NOT external.
- Disk cache (`diskcache`) is sqlite-backed - concurrent writes from MCP + FastAPI + RQ worker can lock. If profiler shows `_sqlite3` time, that's the cause; sharded cache or in-memory L1 boost.
- `asyncio.gather` swallows partial failures unless `return_exceptions=True`. If used to "fix" serial calls, audit error handling.
- Memory "leaks" in long-lived uvicorn are usually unbounded dict caches (no LRU eviction). Check L1 dicts have size cap before blaming Python GC.
- Profiler overhead is real - `cProfile` slows code 2-5x. The relative shape of the profile is valid; absolute numbers are not. Re-measure without profiler before reporting wins.
- `time.perf_counter()` deltas on operations <1ms are noisy. Loop 1000x and divide, or use `timeit`.
- "Optimizing" a once-per-day batch job that takes 30s is usually wasted effort. Confirm the path is hot in the user's actual workflow before touching it.
