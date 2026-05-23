"use client";

import { useEffect, useState } from "react";
import type { WatchlistEntry } from "@/lib/api";
import {
  addWatchlistV2,
  fetchWatchlistV2,
  removeWatchlistV2,
} from "@/lib/api";

interface WatchlistManagerProps {
  sessionId: string;
}

export function WatchlistManager({ sessionId }: WatchlistManagerProps) {
  const [items, setItems] = useState<WatchlistEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [note, setNote] = useState("");

  async function refresh() {
    try {
      setLoading(true);
      const r = await fetchWatchlistV2(sessionId);
      setItems(r.watchlist);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (sessionId) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  async function handleAdd() {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    await addWatchlistV2(sessionId, sym, note.trim() || undefined);
    setSymbol("");
    setNote("");
    await refresh();
  }

  async function handleRemove(sym: string) {
    await removeWatchlistV2(sessionId, sym);
    await refresh();
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-3">
      <h3 className="text-lg font-semibold text-slate-100">
        Watchlist
      </h3>

      <div className="flex items-center gap-2">
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Ticker (e.g. NVDA, SHOP.TO)"
          className="flex-1 px-2 py-1.5 rounded bg-slate-800 border border-slate-700 text-slate-200 text-sm focus:outline-none focus:border-indigo-500"
        />
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional)"
          className="flex-1 px-2 py-1.5 rounded bg-slate-800 border border-slate-700 text-slate-200 text-sm focus:outline-none focus:border-indigo-500"
        />
        <button
          type="button"
          onClick={handleAdd}
          className="px-3 py-1.5 rounded bg-indigo-500/20 text-indigo-200 border border-indigo-500/40 text-sm hover:bg-indigo-500/30"
        >
          Add
        </button>
      </div>

      {loading ? (
        <p className="text-xs text-slate-500">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-slate-500">
          Watchlist empty. Add tickers to include them in the nightly
          discovery scan beyond the S&P 500 + TSX 60 baseline universe.
        </p>
      ) : (
        <ul className="space-y-1">
          {items.map((w) => (
            <li
              key={w.symbol}
              className="flex items-center justify-between px-2 py-1.5 rounded bg-slate-800/60 border border-slate-700"
            >
              <div className="flex items-center gap-2">
                <span className="font-mono font-semibold text-slate-200">
                  {w.symbol}
                </span>
                {w.note && (
                  <span className="text-xs text-slate-500">— {w.note}</span>
                )}
              </div>
              <button
                type="button"
                onClick={() => handleRemove(w.symbol)}
                className="text-xs text-rose-400 hover:text-rose-300"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
