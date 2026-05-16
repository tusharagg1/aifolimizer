# aifolimizer — Lessons

Append-only. Each entry: short rule + source incident. Read at session startup.

---

## Python

- **Local-variable scoping is whole-function.** If `name = ...` appears anywhere in a function body, every earlier reference to `name` is treated as a local read → `UnboundLocalError`. Always scan the whole function for assignments before referencing a name. *(Source: `recommendations.py:407` referenced `em` before line 419 assigned it — crashed `_score_position` on every position, emptied the recs panel.)*

- **Cache failure modes.** When wrapping an external call in a per-symbol/per-key cache, decide: cache empties on failure (fast recovery but data gap stays for TTL) or skip caching on failure (retry on next call). Pick deliberately. *(Source: `_ticker_meta` 5-min cache stores empty meta if yfinance fails — would mask outages for full 5 min.)*

- **Locks need a double-check.** When using `asyncio.Lock` to dedupe concurrent fetches, re-check the cache inside the lock — another waiter may have populated it while you waited. *(Source: `_get_portfolio` lock pattern.)*

## yfinance

- **Batch when you can.** `yf.download([syms], group_by="ticker", threads=True)` is one HTTP regardless of symbol count. Serial per-symbol loops are the easy bottleneck.

- **`group_by="ticker"` returns MultiIndex even for 1 symbol.** Slicer code must handle both single-level and MultiIndex columns. *(Source: technicals batch refactor — single-symbol path returned `'Close'` KeyError until `_slice_symbol` handled both.)*

- **`yf.Ticker(sym).info` is 1 HTTP call.** `.calendar` and `.dividends` are each another. Three HTTP calls per symbol if you touch all three.

## Frontend (React / Next.js)

- **AllocationChart-style sums must use the `_cad` suffix.** Mixing native `market_value` (USD or CAD) with a CAD total under-sizes USD slices by ~1.38x. *(Source: pre-existing bug in `AllocationChart.tsx:20`.)*

- **`useEffect(() => () => abort, [])` interacts with strict mode.** Empty-deps cleanup runs at dev strict-mode unmount and aborts in-flight fetches. Verify second mount re-fires loaders.

- **`React.memo` only helps if parent passes stable prop refs.** Arrays/objects rebuilt on every render bypass memo. For props from `setState(apiResponse)`, the ref changes per fetch — memo skips re-renders when no fetch happened (e.g. countdown ticks).

- **SetState in effect body trips React 19 lint.** Use derived `now` state ticked by interval, compute remaining inline. *(Source: `CountdownLabel.tsx` first version.)*

## Workflow

- **Compile-clean ≠ working.** Import-check + tsc + lint pass without exercising the code. Always call the changed function with realistic input before declaring done. *(Source: 9-step perf pass shipped clean lints; bug hunt later exposed `em` UnboundLocalError that would have been caught by one mock-portfolio invocation.)*

- **Pre-existing bugs surface during refactors.** When fresh integration tests fire (cache cleared, services restarted), latent bugs that hid behind cached responses come out. Budget time for them.

- **Skip skeletons on background refresh.** Setting `loading=true` only when state is null prevents flicker on auto-refresh while still showing skeleton on first load. Don't apply blanket — account-switch needs the skeleton.
