"use client";

import { useState, useCallback } from "react";
import {
  WatchlistItem,
  WatchlistRecommendation,
  wsAddToWatchlist,
  wsRemoveFromWatchlist,
} from "@/lib/api";

type WatchAction = "BUY" | "WATCH" | "PASS";

const ACTION_CONFIG: Record<WatchAction, {
  bg: string; border: string; badge: string; text: string;
}> = {
  BUY: {
    bg: "bg-emerald-500/8",
    border: "border-emerald-500/25",
    badge: "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30",
    text: "text-emerald-400",
  },
  WATCH: {
    bg: "bg-amber-500/8",
    border: "border-amber-500/25",
    badge: "bg-amber-500/20 text-amber-400 border border-amber-500/30",
    text: "text-amber-400",
  },
  PASS: {
    bg: "bg-slate-800/30",
    border: "border-slate-700/50",
    badge: "bg-slate-700 text-slate-400 border border-slate-600",
    text: "text-slate-400",
  },
};

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

function fmtPrice(n: number | null | undefined, currency: string) {
  if (n == null) return "—";
  const sym = currency === "CAD" ? "CA$" : "$";
  return `${sym}${n.toFixed(2)}`;
}

interface RecCardProps {
  rec: WatchlistRecommendation;
  sessionId: string;
  onRemove: (symbol: string) => void;
}

function RecCard({ rec, sessionId, onRemove }: RecCardProps) {
  const [removing, setRemoving] = useState(false);
  const cfg = ACTION_CONFIG[rec.action as WatchAction] ?? ACTION_CONFIG.WATCH;
  const currency = rec.currency ?? (
    rec.symbol.endsWith(".TO") || rec.symbol.endsWith(".V") ? "CAD" : "USD"
  );

  async function handleRemove() {
    setRemoving(true);
    try {
      await wsRemoveFromWatchlist(sessionId, rec.symbol);
      onRemove(rec.symbol);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className={`rounded-lg border p-3 space-y-2 ${cfg.bg} ${cfg.border}`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-1">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono text-sm font-bold text-white truncate">
              {rec.symbol}
            </span>
            <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cfg.badge}`}>
              {rec.action}
            </span>
          </div>
          {rec.name && rec.name !== rec.symbol && (
            <p className="text-[10px] text-slate-500 truncate mt-0.5">{rec.name}</p>
          )}
        </div>
        <button
          onClick={handleRemove}
          disabled={removing}
          className="text-[10px] text-slate-600 hover:text-rose-400 transition-colors shrink-0 mt-0.5"
          title="Remove from watchlist"
        >
          {removing ? "…" : "✕"}
        </button>
      </div>

      {/* Score bar */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1 bg-slate-700/60 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${
              rec.action === "BUY" ? "bg-emerald-500" :
              rec.action === "WATCH" ? "bg-amber-500" : "bg-slate-500"
            }`}
            style={{ width: `${(rec.score / 10) * 100}%` }}
          />
        </div>
        <span className="text-[10px] text-slate-400 shrink-0">{rec.score}/10</span>
      </div>

      {/* Price + levels */}
      {rec.current_price != null && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
          <div>
            <span className="text-slate-500">Price </span>
            <span className="text-white font-mono">
              {fmtPrice(rec.current_price, currency)}
            </span>
          </div>
          {rec.stop_loss != null && (
            <div>
              <span className="text-slate-500">Stop </span>
              <span className="text-rose-400 font-mono">
                {fmtPrice(rec.stop_loss, currency)}
                {rec.stop_type && (
                  <span className="text-slate-600 ml-0.5">({rec.stop_type})</span>
                )}
              </span>
            </div>
          )}
          {rec.take_profit != null && (
            <div>
              <span className="text-slate-500">Target </span>
              <span className="text-emerald-400 font-mono">
                {fmtPrice(rec.take_profit, currency)}
              </span>
            </div>
          )}
          {rec.risk_reward != null && (
            <div>
              <span className="text-slate-500">R:R </span>
              <span className="text-white font-mono">{fmt(rec.risk_reward, 1)}×</span>
            </div>
          )}
          {rec.kelly_pct != null && rec.action === "BUY" && (
            <div>
              <span className="text-slate-500">Kelly </span>
              <span className="text-indigo-400 font-mono">{fmt(rec.kelly_pct, 1)}%</span>
            </div>
          )}
          {rec.analyst_upside_pct != null && (
            <div>
              <span className="text-slate-500">Analyst </span>
              <span className={`font-mono ${rec.analyst_upside_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {rec.analyst_upside_pct >= 0 ? "+" : ""}{fmt(rec.analyst_upside_pct, 0)}%
              </span>
            </div>
          )}
        </div>
      )}

      {/* Earnings risk */}
      {rec.earnings_risk && rec.days_to_earnings != null && (
        <div className={`text-[10px] px-1.5 py-0.5 rounded border inline-block ${
          rec.earnings_risk === "imminent"
            ? "text-rose-400 bg-rose-500/10 border-rose-500/20"
            : "text-amber-400 bg-amber-500/10 border-amber-500/20"
        }`}>
          Earnings {rec.days_to_earnings}d
          {rec.expected_move_pct != null && ` · ±${fmt(rec.expected_move_pct, 1)}%`}
        </div>
      )}

      {/* Entry timing */}
      {rec.entry_timing === "wait_pullback" && rec.action === "BUY" && (
        <div className="text-[10px] text-amber-400">
          Wait for pullback — RSI extended
        </div>
      )}

      {/* Top reasons */}
      {rec.reasons && rec.reasons.length > 0 && (
        <ul className="space-y-0.5">
          {rec.reasons.slice(0, 3).map((r, i) => (
            <li key={i} className="text-[10px] text-slate-400 leading-tight">
              · {r}
            </li>
          ))}
        </ul>
      )}

      {/* Notes */}
      {rec.notes && (
        <p className="text-[10px] text-slate-500 italic border-t border-slate-700/40 pt-1">
          {rec.notes}
        </p>
      )}

      {/* Meta */}
      <div className="flex items-center gap-2 flex-wrap pt-0.5">
        {rec.stage != null && (
          <span className="text-[9px] text-slate-500">S{rec.stage}</span>
        )}
        {rec.rsi != null && (
          <span className="text-[9px] text-slate-500">RSI {fmt(rec.rsi, 0)}</span>
        )}
        <span className={`text-[9px] px-1 py-0.5 rounded border ${
          rec.confidence === "high"
            ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20"
            : rec.confidence === "medium"
            ? "text-amber-400 bg-amber-500/10 border-amber-500/20"
            : "text-slate-400 bg-slate-700 border-slate-600"
        }`}>
          {rec.confidence}
        </span>
      </div>
    </div>
  );
}

interface Props {
  sessionId: string;
  items: WatchlistItem[];
  recommendations: WatchlistRecommendation[] | null;
  loading: boolean;
  onItemsChange: (items: WatchlistItem[]) => void;
}

export default function WatchlistPanel({
  sessionId,
  items,
  recommendations,
  loading,
  onItemsChange,
}: Props) {
  const [open, setOpen] = useState(true);
  const [input, setInput] = useState("");
  const [notes, setNotes] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const handleAdd = useCallback(async () => {
    const sym = input.trim().toUpperCase();
    if (!sym) return;
    setAdding(true);
    setAddError(null);
    try {
      const updated = await wsAddToWatchlist(sessionId, sym, notes.trim());
      onItemsChange(updated);
      setInput("");
      setNotes("");
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed to add");
    } finally {
      setAdding(false);
    }
  }, [sessionId, input, notes, onItemsChange]);

  const handleRemove = useCallback((symbol: string) => {
    onItemsChange(items.filter(i => i.symbol !== symbol));
  }, [items, onItemsChange]);

  const groups: Record<WatchAction, WatchlistRecommendation[]> = {
    BUY:   (recommendations ?? []).filter(r => r.action === "BUY"),
    WATCH: (recommendations ?? []).filter(r => r.action === "WATCH"),
    PASS:  (recommendations ?? []).filter(r => r.action === "PASS"),
  };

  const hasRecs = recommendations && recommendations.length > 0;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-white">Watchlist</h2>
          {items.length > 0 && (
            <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded-full">
              {items.length} ticker{items.length !== 1 ? "s" : ""}
            </span>
          )}
          {loading && (
            <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded-full animate-pulse">
              analyzing…
            </span>
          )}
        </div>
        <button
          onClick={() => setOpen(o => !o)}
          className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          {open ? "▲ collapse" : "▼ expand"}
        </button>
      </div>

      {open && (
        <div className="px-4 pb-4 space-y-4">
          {/* Add ticker input */}
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={e => setInput(e.target.value.toUpperCase())}
                onKeyDown={e => e.key === "Enter" && handleAdd()}
                placeholder="Ticker (e.g. AAPL, SHOP.TO)"
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 font-mono"
              />
              <button
                onClick={handleAdd}
                disabled={adding || !input.trim()}
                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm rounded-lg transition-colors"
              >
                {adding ? "…" : "Add"}
              </button>
            </div>
            <input
              type="text"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Notes (optional)"
              className="w-full bg-slate-800/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-600"
            />
            {addError && (
              <p className="text-[10px] text-rose-400">{addError}</p>
            )}
          </div>

          {/* Empty state */}
          {items.length === 0 && (
            <p className="text-sm text-slate-500 text-center py-4">
              Add tickers to monitor entry signals
            </p>
          )}

          {/* Items without recs yet (loading) */}
          {items.length > 0 && loading && !hasRecs && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              {items.map(item => (
                <div key={item.symbol} className="h-32 bg-slate-800/40 rounded-lg animate-pulse" />
              ))}
            </div>
          )}

          {/* Recommendation groups */}
          {hasRecs && (
            <div className="space-y-4">
              {(["BUY", "WATCH", "PASS"] as WatchAction[]).map(action => {
                const group = groups[action];
                if (group.length === 0) return null;
                const cfg = ACTION_CONFIG[action];
                return (
                  <div key={action}>
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-xs font-semibold ${cfg.text}`}>{action}</span>
                      <span className="text-[10px] text-slate-600">{group.length}</span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                      {group.map(rec => (
                        <RecCard
                          key={rec.symbol}
                          rec={rec}
                          sessionId={sessionId}
                          onRemove={handleRemove}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Watchlist without recs — show plain chip list */}
          {items.length > 0 && !loading && !hasRecs && (
            <div className="flex flex-wrap gap-2">
              {items.map(item => (
                <div
                  key={item.symbol}
                  className="flex items-center gap-1.5 bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1"
                >
                  <span className="font-mono text-xs text-white">{item.symbol}</span>
                  <button
                    onClick={async () => {
                      await wsRemoveFromWatchlist(sessionId, item.symbol);
                      handleRemove(item.symbol);
                    }}
                    className="text-[10px] text-slate-600 hover:text-rose-400 transition-colors"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
