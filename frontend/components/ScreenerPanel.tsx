"use client";

import { useState, useCallback } from "react";
import { wsGetScreener, ScreenerResult } from "@/lib/api";

type Universe = "tsx" | "spx" | "full";

interface Props {
  sessionId: string;
  onSelectTicker?: (symbol: string) => void;
}

const SCORE_COLOR = (s: number | null) => {
  if (!s) return "text-slate-500";
  if (s >= 0.75) return "text-emerald-400";
  if (s >= 0.55) return "text-amber-400";
  return "text-slate-400";
};

const SIGNAL_BADGE: Record<string, string> = {
  overbought: "text-rose-400 bg-rose-500/10",
  oversold: "text-emerald-400 bg-emerald-500/10",
  neutral: "text-slate-500 bg-slate-800",
  strong: "text-emerald-400 bg-emerald-500/10",
  moderate: "text-amber-400 bg-amber-500/10",
  weak: "text-slate-500 bg-slate-800",
  rising: "text-emerald-400 bg-emerald-500/10",
  falling: "text-rose-400 bg-rose-500/10",
};

function badge(label: string | null) {
  if (!label) return null;
  const cls = SIGNAL_BADGE[label] ?? "text-slate-400 bg-slate-800";
  return (
    <span className={`px-1.5 py-0.5 text-[10px] rounded ${cls}`}>{label}</span>
  );
}

export default function ScreenerPanel({ sessionId, onSelectTicker }: Props) {
  const [open, setOpen] = useState(false);
  const [universe, setUniverse] = useState<Universe>("full");
  const [results, setResults] = useState<ScreenerResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ranAt, setRanAt] = useState<string | null>(null);

  const runScan = useCallback(async (u: Universe = universe) => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await wsGetScreener(sessionId, u, 30);
      setResults(res.results);
      setRanAt(new Date().toLocaleTimeString());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Screener failed");
    } finally {
      setLoading(false);
    }
  }, [sessionId, universe]);

  const handleUniverse = (u: Universe) => {
    setUniverse(u);
    if (open && results.length > 0) runScan(u);
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      {/* Header / toggle */}
      <button
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next && results.length === 0) runScan();
        }}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-white hover:bg-slate-800/40 transition-colors"
      >
        <span className="flex items-center gap-2">
          Screener
          {results.length > 0 && !loading && (
            <span className="text-[10px] text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded-full">
              {results.length} setups
            </span>
          )}
        </span>
        <span className="text-slate-500 text-xs">
          {open ? "▲ collapse" : "▼ expand"} · stage-2 TSX + S&amp;P 500
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 space-y-3">
          {/* Controls */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-1">
              {(["full", "tsx", "spx"] as Universe[]).map((u) => (
                <button
                  key={u}
                  onClick={() => handleUniverse(u)}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    universe === u
                      ? "bg-indigo-600 text-white"
                      : "bg-slate-800 text-slate-400 hover:text-white"
                  }`}
                >
                  {u.toUpperCase()}
                </button>
              ))}
            </div>
            <button
              onClick={() => runScan()}
              disabled={loading}
              className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 text-white transition-colors"
            >
              {loading ? "Scanning…" : "Scan"}
            </button>
            {ranAt && !loading && (
              <span className="text-[10px] text-slate-600">last scan {ranAt}</span>
            )}
          </div>

          {error && (
            <p className="text-xs text-rose-400">{error}</p>
          )}

          {loading && (
            <div className="space-y-2">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="h-10 rounded bg-slate-800 animate-pulse" />
              ))}
              <p className="text-[10px] text-slate-600">
                Fetching ~70 tickers — takes 10-20s on first scan…
              </p>
            </div>
          )}

          {!loading && results.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-600 text-[10px] border-b border-slate-800">
                    <th className="text-left py-1 pr-3">Symbol</th>
                    <th className="text-right pr-3">Score</th>
                    <th className="text-right pr-3">Price</th>
                    <th className="text-right pr-3">Minervini</th>
                    <th className="text-right pr-3">RSI</th>
                    <th className="text-left pr-3">RSI Signal</th>
                    <th className="text-right pr-3">ADX</th>
                    <th className="text-left pr-3">ADX</th>
                    <th className="text-left pr-3">OBV</th>
                    <th className="text-right pr-3">Vol×</th>
                    <th className="text-right pr-3">From 52w hi</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {results.map((r) => (
                    <tr
                      key={r.symbol}
                      className="hover:bg-slate-800/40 cursor-pointer transition-colors"
                      onClick={() => onSelectTicker?.(r.symbol)}
                    >
                      <td className="py-1.5 pr-3">
                        <span className="font-mono font-semibold text-indigo-400">
                          {r.symbol}
                        </span>
                      </td>
                      <td className={`text-right pr-3 font-semibold ${SCORE_COLOR(r.technical_score)}`}>
                        {r.technical_score != null ? (r.technical_score * 100).toFixed(0) : "—"}
                      </td>
                      <td className="text-right pr-3 text-slate-300">
                        {r.current_price != null ? `$${r.current_price.toFixed(2)}` : "—"}
                      </td>
                      <td className="text-right pr-3 text-slate-400">
                        {r.minervini_score ?? "—"}/7
                      </td>
                      <td className="text-right pr-3 text-slate-300">
                        {r.rsi_14 != null ? r.rsi_14.toFixed(1) : "—"}
                      </td>
                      <td className="pr-3">{badge(r.rsi_signal)}</td>
                      <td className="text-right pr-3 text-slate-300">
                        {r.adx_14 != null ? r.adx_14.toFixed(1) : "—"}
                      </td>
                      <td className="pr-3">{badge(r.adx_signal)}</td>
                      <td className="pr-3">{badge(r.obv_trend)}</td>
                      <td className="text-right pr-3 text-slate-400">
                        {r.volume_score != null ? `${r.volume_score.toFixed(1)}×` : "—"}
                      </td>
                      <td className={`text-right pr-3 ${
                        r.pct_from_52w_high != null && r.pct_from_52w_high > -10
                          ? "text-emerald-400"
                          : "text-slate-500"
                      }`}>
                        {r.pct_from_52w_high != null
                          ? `${r.pct_from_52w_high.toFixed(1)}%`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!loading && !error && results.length === 0 && ranAt && (
            <p className="text-xs text-slate-500">
              No stage-2 setups found in current universe. Try a different filter.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
