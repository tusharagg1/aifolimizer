"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  wsLogin,
  wsVerifyOtp,
  wsRestoreSession,
  wsGetPortfolio,
  wsGetHealthScore,
  wsGetRecommendations,
  wsGetCrowding,
  wsGetWatchlist,
  wsAddToWatchlist,
  wsRemoveFromWatchlist,
  wsGetWatchlistRecommendations,
  wsGetPriceHistory,
  wsGetPortfolioCommentary,
  wsGetNarratives,
} from "@/lib/api";
import type {
  UserProfile,
  Position,
  PortfolioSummary,
  HealthScore,
  Recommendation,
  CrowdingMap,
  WatchlistItem,
  WatchlistRecommendation,
  PriceHistory,
  SignalChange,
  PortfolioCommentary,
} from "@/lib/api";

// ─── Constants ────────────────────────────────────────────────────────────────

type Action = "BUY" | "ADD" | "WATCH" | "HOLD" | "TRIM" | "SELL" | "NO_EDGE" | "PASS";

const SIGNAL_CFG: Record<Action, { bg: string; text: string; border: string }> = {
  BUY:     { bg: "bg-emerald-900/50", text: "text-emerald-300", border: "border-emerald-600/40" },
  ADD:     { bg: "bg-emerald-900/50", text: "text-emerald-300", border: "border-emerald-600/40" },
  WATCH:   { bg: "bg-amber-900/50",   text: "text-amber-300",   border: "border-amber-600/40" },
  HOLD:    { bg: "bg-slate-800/50",   text: "text-slate-400",   border: "border-slate-600/40" },
  TRIM:    { bg: "bg-orange-900/50",  text: "text-orange-300",  border: "border-orange-600/40" },
  SELL:    { bg: "bg-rose-900/50",    text: "text-rose-300",    border: "border-rose-600/40" },
  NO_EDGE: { bg: "bg-slate-800/30",   text: "text-slate-500",   border: "border-slate-700/40" },
  PASS:    { bg: "bg-slate-800/30",   text: "text-slate-500",   border: "border-slate-700/40" },
};

const ASSET_COLORS: Record<string, string> = {
  equity: "#6366f1", etf: "#3b82f6", crypto: "#a855f7",
  bond: "#10b981", commodity: "#f59e0b", cash: "#64748b",
};

const CHART_PERIODS = ["1mo", "3mo", "6mo", "1y", "2y", "5y"];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtCAD(n: number) {
  return new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 2 }).format(n);
}
function fmtUSD(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(n);
}
function fmtPct(n: number, sign = true) {
  const s = n.toFixed(2) + "%";
  return sign && n > 0 ? "+" + s : s;
}
function fmtNative(n: number, currency: string) {
  return currency === "USD" ? fmtUSD(n) : fmtCAD(n);
}
function fmtQty(n: number) {
  return n % 1 === 0 ? n.toString() : n.toFixed(4).replace(/\.?0+$/, "");
}
function crowdingCls(score: number) {
  return score <= 30 ? "text-emerald-400" : score <= 60 ? "text-amber-400" : "text-rose-400";
}

// ─── StatCard ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, cls, title }: {
  label: string; value: string; cls: string; title?: string;
}) {
  return (
    <div
      className="bg-slate-900 border border-slate-800 rounded-xl px-3 py-2.5"
      title={title}
    >
      <div className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">{label}</div>
      <div className={`font-mono text-sm font-semibold ${cls}`}>{value}</div>
    </div>
  );
}

// ─── PriceChart ───────────────────────────────────────────────────────────────

function PriceChart({
  sessionId, symbol, period, onPeriodChange,
}: {
  sessionId: string;
  symbol: string;
  period: string;
  onPeriodChange: (p: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null);
  const [histData, setHistData] = useState<PriceHistory | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    wsGetPriceHistory(sessionId, symbol, period)
      .then((d) => { if (!cancelled) setHistData(d); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sessionId, symbol, period]);

  useEffect(() => {
    if (!containerRef.current || !histData) return;
    let cancelled = false;
    let ro: ResizeObserver | null = null;

    import("lightweight-charts").then(({ createChart, ColorType, AreaSeries, LineSeries }) => {
      if (cancelled || !containerRef.current) return;
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 240,
        layout: {
          background: { type: ColorType.Solid, color: "#0f172a" },
          textColor: "#94a3b8",
        },
        grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
        crosshair: { mode: 1 },
        rightPriceScale: { borderColor: "#334155" },
        timeScale: { borderColor: "#334155", timeVisible: false },
        handleScroll: true,
        handleScale: true,
      });
      chartRef.current = chart;

      const closeData = histData.dates
        .map((d, i) => ({ time: d, value: histData.close[i] }))
        .filter((p) => p.value != null && !isNaN(p.value));

      if (closeData.length > 0) {
        const area = chart.addSeries(AreaSeries, {
          lineColor: "#6366f1",
          topColor: "rgba(99,102,241,0.3)",
          bottomColor: "rgba(99,102,241,0.02)",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        area.setData(closeData as any);
      }

      const addSma = (values: (number | null)[], color: string) => {
        const d = values
          .map((v, i) => ({ time: histData.dates[i], value: v }))
          .filter((p) => p.value != null);
        if (d.length === 0) return;
        const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        s.setData(d as any);
      };
      addSma(histData.sma_20, "#f59e0b");
      addSma(histData.sma_50, "#22d3ee");
      addSma(histData.sma_200, "#f43f5e");

      chart.timeScale().fitContent();

      if (!cancelled && containerRef.current) {
        ro = new ResizeObserver(() => {
          if (containerRef.current && chartRef.current) {
            chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
          }
        });
        ro.observe(containerRef.current);
      }
    });

    return () => {
      cancelled = true;
      ro?.disconnect();
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    };
  }, [histData]);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono font-semibold text-indigo-300 text-sm">{symbol}</span>
        <div className="flex gap-1">
          {CHART_PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => onPeriodChange(p)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${period === p ? "bg-indigo-600 text-white" : "text-slate-500 hover:text-slate-300"}`}
            >{p}</button>
          ))}
        </div>
      </div>
      <div className="flex gap-3 text-[10px] text-slate-500">
        <span className="flex items-center gap-1"><span className="w-3 border-t-2 border-indigo-400 inline-block" />Price</span>
        <span className="flex items-center gap-1"><span className="w-3 border-t border-amber-400 inline-block" />SMA20</span>
        <span className="flex items-center gap-1"><span className="w-3 border-t border-cyan-400 inline-block" />SMA50</span>
        <span className="flex items-center gap-1"><span className="w-3 border-t border-rose-500 inline-block" />SMA200</span>
      </div>
      {loading
        ? <div className="h-60 flex items-center justify-center text-slate-600 text-xs animate-pulse">Loading chart…</div>
        : <div ref={containerRef} className="w-full" />}
    </div>
  );
}

// ─── RecDetail ────────────────────────────────────────────────────────────────

function RecDetail({
  rec, narrative,
}: {
  rec: Recommendation | WatchlistRecommendation;
  narrative?: string | null;
}) {
  const cfg = SIGNAL_CFG[rec.action as Action] ?? SIGNAL_CFG.HOLD;
  const confCls = rec.confidence === "high" ? "text-emerald-400"
    : rec.confidence === "medium" ? "text-amber-400" : "text-rose-400";

  return (
    <div className={`rounded-xl border ${cfg.border} ${cfg.bg} p-3 space-y-2 text-xs`}>
      <div className="flex items-center justify-between flex-wrap gap-1">
        <div className="flex items-center gap-2">
          <span className={`font-bold text-base ${cfg.text}`}>{rec.action}</span>
          <span className={confCls}>{rec.confidence}</span>
          <span className="text-slate-500">{rec.score}/100</span>
        </div>
        <div className="text-slate-400 font-mono text-[11px]">{rec.symbol}</div>
      </div>

      {narrative && (
        <p className="text-[11px] text-slate-300 italic leading-relaxed pb-1 border-b border-slate-700/50">
          {narrative}
        </p>
      )}

      <div className="w-full bg-slate-800 rounded-full h-1">
        <div
          className={`h-1 rounded-full ${rec.score >= 70 ? "bg-emerald-500" : rec.score >= 50 ? "bg-amber-500" : "bg-rose-500"}`}
          style={{ width: `${rec.score}%` }}
        />
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        <div>
          <div className="text-slate-600 text-[10px]">Entry</div>
          <div className={`font-mono font-semibold text-[11px] ${cfg.text}`}>
            {rec.current_price != null ? fmtNative(rec.current_price, rec.currency) : "—"}
            {rec.entry_timing === "wait_pullback" && <span className="text-amber-400 font-normal ml-1">wait dip</span>}
          </div>
        </div>
        <div>
          <div className="text-slate-600 text-[10px]">Target</div>
          <div className="font-mono font-semibold text-[11px] text-emerald-400">
            {rec.take_profit != null ? fmtNative(rec.take_profit, rec.currency) : "—"}
          </div>
        </div>
        <div>
          <div className="text-slate-600 text-[10px]">Stop</div>
          <div className="font-mono font-semibold text-[11px] text-rose-400">
            {rec.stop_loss != null ? fmtNative(rec.stop_loss, rec.currency) : "—"}
          </div>
        </div>
        <div>
          <div className="text-slate-600 text-[10px]">R:R</div>
          <div className="font-mono font-semibold text-[11px] text-slate-200">
            {rec.risk_reward != null ? `1:${rec.risk_reward.toFixed(1)}` : "—"}
          </div>
        </div>
      </div>

      {(rec.ev_dollars != null || rec.kelly_pct != null) && (
        <div className="flex flex-wrap gap-3 text-[10px]">
          {rec.ev_dollars != null && (
            <span>EV <span className={`font-mono font-semibold ${rec.ev_dollars >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              {rec.ev_dollars >= 0 ? "+" : ""}{fmtCAD(rec.ev_dollars)}
            </span></span>
          )}
          {rec.kelly_pct != null && (
            <span className="text-slate-500">Kelly <span className="font-mono text-slate-300">{rec.kelly_pct.toFixed(1)}%</span></span>
          )}
          {rec.win_prob != null && (
            <span className="text-slate-500">Win <span className="font-mono text-slate-300">{(rec.win_prob * 100).toFixed(0)}%</span></span>
          )}
        </div>
      )}

      {rec.analyst_target != null && (
        <div className="text-[10px] text-slate-500">
          Analyst <span className="text-slate-300 font-mono">{fmtNative(rec.analyst_target, rec.currency)}</span>
          {rec.analyst_upside_pct != null && (
            <span className={`ml-1 ${rec.analyst_upside_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              ({fmtPct(rec.analyst_upside_pct)})
            </span>
          )}
        </div>
      )}

      {rec.hedge_flag && rec.hedge_reason && (
        <div className="bg-rose-950/50 border border-rose-700/30 rounded px-2 py-1 text-[10px] text-rose-300">
          ⚠ {rec.hedge_reason}
        </div>
      )}

      {rec.days_to_earnings != null && rec.days_to_earnings <= 14 && (
        <div className="bg-amber-900/30 border border-amber-700/30 rounded px-2 py-1 text-[10px] text-amber-300">
          Earnings in {rec.days_to_earnings}d
          {rec.expected_move_pct != null && ` · ±${rec.expected_move_pct.toFixed(1)}%`}
        </div>
      )}

      {rec.reasons.length > 0 && (
        <ul className="space-y-0.5">
          {rec.reasons.slice(0, 5).map((r, i) => (
            <li key={i} className="flex gap-1.5 text-slate-400 text-[11px]">
              <span className="text-slate-600 shrink-0">·</span><span>{r}</span>
            </li>
          ))}
        </ul>
      )}

      {rec.flags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {rec.flags.map((f, i) => (
            <span key={i} className="bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded text-[10px]">{f}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── CommentaryPanel ─────────────────────────────────────────────────────────

function CommentaryPanel({ data, loading }: {
  data: PortfolioCommentary | null;
  loading: boolean;
}) {
  if (loading && !data) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
        <span className="text-xs font-semibold text-slate-400">AI Commentary</span>
        <div className="text-xs text-slate-500 italic">Generating…</div>
      </div>
    );
  }
  if (!data || data.error || !data.commentary) {
    const reason =
      data?.error === "no_llm_keys" ? "No LLM provider configured"
      : data?.error ? `Error: ${data.error}` : "Awaiting portfolio data";
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
        <span className="text-xs font-semibold text-slate-400">AI Commentary</span>
        <div className="text-xs text-slate-600 italic">{reason}</div>
      </div>
    );
  }
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-400">AI Commentary</span>
        {data.provider && (
          <span className="text-[10px] text-slate-600">{data.provider}</span>
        )}
      </div>
      <p className="text-xs text-slate-300 leading-relaxed">{data.commentary}</p>
      {data.actions.length > 0 && (
        <ul className="space-y-1 mt-2 pt-2 border-t border-slate-800">
          {data.actions.map((a, i) => (
            <li key={i} className="text-[11px] text-slate-400 flex gap-1.5">
              <span className="text-indigo-400">▸</span>
              <span>{a}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


// ─── HealthCompact ────────────────────────────────────────────────────────────

function HealthCompact({ health }: { health: HealthScore }) {
  const gradeCls = health.grade === "A" ? "text-emerald-400" : health.grade === "B" ? "text-blue-400"
    : health.grade === "C" ? "text-amber-400" : health.grade === "D" ? "text-orange-400" : "text-rose-400";
  const dims = [
    { label: "Diversification", val: health.breakdown.diversification,      max: 30 },
    { label: "Concentration",   val: health.breakdown.concentration,         max: 30 },
    { label: "Performance",     val: health.breakdown.performance,           max: 20 },
    { label: "Cash Efficiency", val: health.breakdown.cash_efficiency,       max: 10 },
    { label: "Asset Mix",       val: health.breakdown.asset_class_diversity, max: 10 },
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-400">Portfolio Health</span>
        <div className="flex items-baseline gap-1.5">
          <span className={`text-xl font-black ${gradeCls}`}>{health.grade}</span>
          <span className="text-slate-500 text-xs">{health.score}/100</span>
        </div>
      </div>
      <div className="space-y-1.5">
        {dims.map((d) => {
          const pct = (d.val / d.max) * 100;
          const bar = pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-rose-500";
          return (
            <div key={d.label}>
              <div className="flex justify-between text-[10px] text-slate-500 mb-0.5">
                <span>{d.label}</span><span>{d.val}/{d.max}</span>
              </div>
              <div className="h-1 bg-slate-800 rounded-full">
                <div className={`h-1 rounded-full ${bar}`} style={{ width: `${pct}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── AllocationDonut ──────────────────────────────────────────────────────────

function AllocationDonut({ positions, cashCAD }: { positions: Position[]; cashCAD: number }) {
  const grouped: Record<string, number> = {};
  for (const p of positions) grouped[p.asset_class] = (grouped[p.asset_class] || 0) + p.market_value_cad;
  if (cashCAD > 0) grouped["cash"] = (grouped["cash"] || 0) + cashCAD;
  const total = Object.values(grouped).reduce((s, v) => s + v, 0);
  const data = Object.entries(grouped)
    .map(([name, value]) => ({ name, value: parseFloat(((value / total) * 100).toFixed(1)) }))
    .sort((a, b) => b.value - a.value);

  const R = 42; const r = 22; const cx = 45; const cy = 45;
  let angle = -Math.PI / 2;
  const slices = data.map((d) => {
    const sweep = (d.value / 100) * 2 * Math.PI;
    const x1 = cx + R * Math.cos(angle); const y1 = cy + R * Math.sin(angle);
    const x2 = cx + R * Math.cos(angle + sweep); const y2 = cy + R * Math.sin(angle + sweep);
    const ix1 = cx + r * Math.cos(angle); const iy1 = cy + r * Math.sin(angle);
    const ix2 = cx + r * Math.cos(angle + sweep); const iy2 = cy + r * Math.sin(angle + sweep);
    const large = sweep > Math.PI ? 1 : 0;
    const path = `M${x1},${y1} A${R},${R} 0 ${large},1 ${x2},${y2} L${ix2},${iy2} A${r},${r} 0 ${large},0 ${ix1},${iy1} Z`;
    angle += sweep;
    return { ...d, path };
  });

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2">
      <span className="text-xs font-semibold text-slate-400">Allocation</span>
      <div className="flex items-center gap-2">
        <svg width={90} height={90} viewBox="0 0 90 90" className="shrink-0">
          {slices.map((s) => (
            <path key={s.name} d={s.path} fill={ASSET_COLORS[s.name] ?? "#475569"}>
              <title>{s.name}: {s.value}%</title>
            </path>
          ))}
        </svg>
        <div className="space-y-0.5 flex-1 min-w-0">
          {data.map((d) => (
            <div key={d.name} className="flex items-center gap-1.5 text-[10px]">
              <div className="w-2 h-2 rounded-full shrink-0" style={{ background: ASSET_COLORS[d.name] ?? "#475569" }} />
              <span className="text-slate-400 capitalize truncate">{d.name}</span>
              <span className="text-slate-300 font-mono ml-auto">{d.value}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── WatchlistPanel (left sidebar) ────────────────────────────────────────────

function WatchlistPanel({
  sessionId,
  watchlist,
  watchlistRecs,
  selectedSymbol,
  onSelect,
  onRefresh,
}: {
  sessionId: string;
  watchlist: WatchlistItem[];
  watchlistRecs: Record<string, WatchlistRecommendation>;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRefresh: () => void;
}) {
  const [addInput, setAddInput] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function handleAdd() {
    const sym = addInput.trim().toUpperCase();
    if (!sym) return;
    setAdding(true); setAddError(null);
    try {
      await wsAddToWatchlist(sessionId, sym, "");
      setAddInput(""); onRefresh();
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed");
    } finally { setAdding(false); }
  }

  async function handleRemove(e: React.MouseEvent, symbol: string) {
    e.stopPropagation();
    try {
      await wsRemoveFromWatchlist(sessionId, symbol);
      onRefresh();
    } catch { /* non-fatal */ }
  }

  // Sort watchlist by rec score desc
  const sorted = [...watchlist].sort((a, b) => {
    const sa = watchlistRecs[a.symbol]?.score ?? -1;
    const sb = watchlistRecs[b.symbol]?.score ?? -1;
    return sb - sa;
  });

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden flex flex-col">
      <div className="px-2.5 py-2 border-b border-slate-800">
        <div className="text-xs font-semibold text-slate-300 mb-1.5">Watchlist</div>
        <div className="flex gap-1">
          <input
            value={addInput}
            onChange={(e) => setAddInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="Add ticker…"
            className="flex-1 min-w-0 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[11px] text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500"
          />
          <button
            onClick={handleAdd}
            disabled={adding || !addInput.trim()}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white px-2 py-1 rounded text-[11px] font-semibold shrink-0"
          >{adding ? "…" : "+"}</button>
        </div>
        {addError && <p className="text-rose-400 text-[10px] mt-1">{addError}</p>}
      </div>

      <div className="flex-1 overflow-y-auto divide-y divide-slate-800/50">
        {sorted.length === 0 && (
          <div className="px-3 py-6 text-center text-slate-600 text-[11px]">Empty — add tickers above</div>
        )}
        {sorted.map((item) => {
          const rec = watchlistRecs[item.symbol];
          const cfg = rec ? (SIGNAL_CFG[rec.action as Action] ?? SIGNAL_CFG.HOLD) : null;
          const isSelected = selectedSymbol === item.symbol;
          return (
            <div
              key={item.symbol}
              onClick={() => onSelect(item.symbol)}
              className={`px-2.5 py-2 cursor-pointer hover:bg-slate-800/40 transition-colors ${isSelected ? "bg-slate-800/60 border-l-2 border-indigo-500" : "border-l-2 border-transparent"}`}
            >
              <div className="flex items-center justify-between gap-1">
                <span className="font-mono font-semibold text-indigo-300 text-xs">{item.symbol}</span>
                <div className="flex items-center gap-1">
                  {cfg && rec && (
                    <span className={`px-1 py-0.5 rounded text-[9px] font-bold border ${cfg.bg} ${cfg.text} ${cfg.border}`}>{rec.action}</span>
                  )}
                  <button
                    onClick={(e) => handleRemove(e, item.symbol)}
                    className="text-slate-700 hover:text-rose-400 text-sm leading-none transition-colors"
                  >×</button>
                </div>
              </div>
              {rec && (
                <div className="flex items-center justify-between mt-0.5 text-[10px]">
                  <span className="font-mono text-slate-400">
                    {rec.current_price != null ? fmtNative(rec.current_price, rec.currency) : "—"}
                  </span>
                  <div className="flex gap-2">
                    {rec.take_profit != null && (
                      <span className="text-emerald-500 font-mono">{fmtNative(rec.take_profit, rec.currency)}</span>
                    )}
                    {rec.stop_loss != null && (
                      <span className="text-rose-500 font-mono">{fmtNative(rec.stop_loss, rec.currency)}</span>
                    )}
                  </div>
                </div>
              )}
              {!rec && <div className="text-[9px] text-slate-700 mt-0.5 animate-pulse">Loading…</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── HoldingsTable ────────────────────────────────────────────────────────────

type SortKey = keyof Position | "crowding" | "signal";

function SortTh({
  k, label, right, sortKey, sortDir, onToggle,
}: {
  k: SortKey;
  label: string;
  right?: boolean;
  sortKey: SortKey;
  sortDir: 1 | -1;
  onToggle: (k: SortKey) => void;
}) {
  const active = sortKey === k;
  return (
    <th onClick={() => onToggle(k)}
      className={`px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wide cursor-pointer select-none whitespace-nowrap ${right ? "text-right" : "text-left"} ${active ? "text-indigo-400" : "text-slate-500 hover:text-slate-300"}`}
    >{label}{active ? (sortDir === -1 ? " ↓" : " ↑") : ""}</th>
  );
}

function HoldingsTable({
  positions, recMap, crowdingMap, recsLoading, selectedSymbol, onSelect,
}: {
  positions: Position[];
  recMap: Record<string, Recommendation>;
  crowdingMap: CrowdingMap;
  recsLoading: boolean;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("weight");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(k); setSortDir(-1); }
  }

  const sorted = [...positions].sort((a, b) => {
    if (sortKey === "crowding") {
      return ((crowdingMap[a.symbol]?.crowding_score ?? -1) - (crowdingMap[b.symbol]?.crowding_score ?? -1)) * sortDir;
    }
    if (sortKey === "signal") {
      return ((recMap[a.symbol]?.score ?? -1) - (recMap[b.symbol]?.score ?? -1)) * sortDir;
    }
    const av = a[sortKey as keyof Position], bv = b[sortKey as keyof Position];
    if (typeof av !== "number" || typeof bv !== "number") return 0;
    return (av - bv) * sortDir;
  });

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-3 py-2 border-b border-slate-800 flex items-center gap-2">
        <span className="text-xs font-semibold text-slate-300">Holdings</span>
        <span className="text-[10px] text-slate-600">— click row for chart + signal</span>
        {recsLoading && <span className="ml-auto text-[10px] text-slate-500 animate-pulse">Loading signals…</span>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead className="bg-slate-950/60">
            <tr>
              <SortTh k="symbol" label="Symbol" sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <th className="px-2 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wide text-slate-500">Name</th>
              <SortTh k="quantity" label="Qty" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="book_cost" label="Cost" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="market_value" label="Value" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="day_change_pct" label="Day%" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="total_return_pct" label="Return%" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="weight" label="Wt%" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="asset_class" label="Class" sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="crowding" label="Crowd" right sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
              <SortTh k="signal" label="Signal" sortKey={sortKey} sortDir={sortDir} onToggle={toggleSort} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((pos) => {
              const rec = recMap[pos.symbol];
              const cs = crowdingMap[pos.symbol];
              const cfg = rec ? (SIGNAL_CFG[rec.action as Action] ?? SIGNAL_CFG.HOLD) : null;
              const isSelected = selectedSymbol === pos.symbol;
              return (
                <tr
                  key={pos.symbol}
                  onClick={() => onSelect(pos.symbol)}
                  className={`border-t border-slate-800/50 cursor-pointer hover:bg-slate-800/30 transition-colors ${isSelected ? "bg-slate-800/50 border-l-2 border-indigo-500" : "border-l-2 border-transparent"}`}
                >
                  <td className="px-2 py-1.5 font-mono font-semibold text-indigo-300 whitespace-nowrap">{pos.symbol}</td>
                  <td className="px-2 py-1.5 text-slate-400 max-w-35 truncate">{pos.name}</td>
                  <td className="px-2 py-1.5 font-mono text-slate-300 text-right whitespace-nowrap">{fmtQty(pos.quantity)}</td>
                  <td className="px-2 py-1.5 font-mono text-slate-500 text-right whitespace-nowrap">{fmtNative(pos.book_cost, pos.currency)}</td>
                  <td className="px-2 py-1.5 font-mono text-slate-200 font-medium text-right whitespace-nowrap">{fmtNative(pos.market_value, pos.currency)}</td>
                  <td className={`px-2 py-1.5 font-mono text-right whitespace-nowrap ${pos.day_change_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {fmtPct(pos.day_change_pct)}
                  </td>
                  <td className={`px-2 py-1.5 font-mono text-right whitespace-nowrap ${pos.total_return_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {fmtPct(pos.total_return_pct)}
                  </td>
                  <td className="px-2 py-1.5 font-mono text-slate-400 text-right whitespace-nowrap">{pos.weight.toFixed(1)}%</td>
                  <td className="px-2 py-1.5 text-slate-500 capitalize whitespace-nowrap">{pos.asset_class}</td>
                  <td className="px-2 py-1.5 text-right whitespace-nowrap">
                    {cs?.crowding_score !== undefined
                      ? <span className={`font-mono font-semibold ${crowdingCls(cs.crowding_score)}`}>{cs.crowding_score}</span>
                      : <span className="text-slate-700">—</span>}
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {recsLoading && !rec
                      ? <span className="text-slate-700 animate-pulse text-[11px]">···</span>
                      : rec && cfg
                        ? <div className="flex items-center gap-1.5">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${cfg.bg} ${cfg.text} ${cfg.border}`}>{rec.action}</span>
                            <span className="text-slate-600 text-[10px]">{rec.confidence}</span>
                          </div>
                        : <span className="text-slate-700">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── LoginForm ────────────────────────────────────────────────────────────────

function LoginForm({ onLogin }: { onLogin: (sid: string, profile: UserProfile) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needsOtp, setNeedsOtp] = useState(false);
  const [pendingSid, setPendingSid] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin() {
    setError(null); setLoading(true);
    try {
      const res = await wsLogin(email, password);
      if (res.needs_otp) { setNeedsOtp(true); setPendingSid(res.session_id); }
      else if (res.profile) onLogin(res.session_id, res.profile);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Login failed"); }
    finally { setLoading(false); }
  }

  async function handleOtp() {
    setError(null); setLoading(true);
    try {
      const res = await wsVerifyOtp(pendingSid, otp);
      onLogin(res.session_id, res.profile);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "OTP failed"); }
    finally { setLoading(false); }
  }

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 w-full max-w-sm space-y-3">
        <div className="mb-2">
          <h1 className="text-slate-100 font-bold text-lg">aifolimizer</h1>
          <p className="text-slate-500 text-xs">Wealthsimple portfolio intelligence</p>
        </div>
        {!needsOtp ? (
          <>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500" />
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()} placeholder="Password"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500" />
            <button onClick={handleLogin} disabled={loading || !email || !password}
              className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-lg py-2 text-sm font-semibold">
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </>
        ) : (
          <>
            <p className="text-slate-400 text-xs">Enter the OTP sent to your device.</p>
            <input type="text" value={otp} onChange={(e) => setOtp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleOtp()} placeholder="123456"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 tracking-widest text-center font-mono" />
            <button onClick={handleOtp} disabled={loading || !otp}
              className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-lg py-2 text-sm font-semibold">
              {loading ? "Verifying…" : "Verify OTP"}
            </button>
          </>
        )}
        {error && <p className="text-rose-400 text-xs text-center">{error}</p>}
      </div>
    </div>
  );
}

// ─── TopMovers ────────────────────────────────────────────────────────────────

function TopMovers({
  positions, onSelect,
}: { positions: Position[]; onSelect: (s: string) => void }) {
  if (positions.length === 0) return null;
  const movers = [...positions]
    .filter((p) => typeof p.day_change_pct === "number")
    .sort((a, b) => Math.abs(b.day_change_pct) - Math.abs(a.day_change_pct))
    .slice(0, 8);
  if (movers.length === 0) return null;
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-2.5 py-2 border-b border-slate-800">
        <div className="text-xs font-semibold text-slate-300">Top Movers (Day)</div>
      </div>
      <div className="divide-y divide-slate-800/50">
        {movers.map((p) => {
          const up = p.day_change_pct >= 0;
          return (
            <div
              key={p.symbol}
              onClick={() => onSelect(p.symbol)}
              className="px-2.5 py-1.5 flex items-center justify-between gap-2 cursor-pointer hover:bg-slate-800/40 transition-colors"
            >
              <span className="font-mono text-xs text-indigo-300 truncate">{p.symbol}</span>
              <span className={`font-mono text-[11px] ${up ? "text-emerald-400" : "text-rose-400"}`}>
                {fmtPct(p.day_change_pct)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── SignalChangesList ────────────────────────────────────────────────────────

function SignalChangesList({
  changes, onSelect,
}: { changes: SignalChange[]; onSelect: (s: string) => void }) {
  if (changes.length === 0) return null;
  const shown = changes.slice(0, 6);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-2.5 py-2 border-b border-slate-800">
        <div className="text-xs font-semibold text-slate-300">Signal Changes</div>
      </div>
      <div className="divide-y divide-slate-800/50">
        {shown.map((c) => {
          const toCfg = SIGNAL_CFG[c.to_action as Action] ?? SIGNAL_CFG.HOLD;
          return (
            <div
              key={c.symbol}
              onClick={() => onSelect(c.symbol)}
              className="px-2.5 py-1.5 cursor-pointer hover:bg-slate-800/40 transition-colors"
            >
              <div className="flex items-center justify-between gap-1">
                <span className="font-mono text-xs text-indigo-300">{c.symbol}</span>
                <div className="flex items-center gap-1 text-[10px]">
                  <span className="text-slate-600">{c.from_action}</span>
                  <span className="text-slate-700">→</span>
                  <span className={`px-1 py-0.5 rounded font-bold border ${toCfg.bg} ${toCfg.text} ${toCfg.border}`}>
                    {c.to_action}
                  </span>
                </div>
              </div>
              {c.top_reason && (
                <div className="text-[10px] text-slate-500 mt-0.5 truncate">{c.top_reason}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── DashboardPage ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [activeAccount, setActiveAccount] = useState<string>("");
  const [authLoading, setAuthLoading] = useState(true);

  const [positions, setPositions] = useState<Position[]>([]);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [health, setHealth] = useState<HealthScore | null>(null);
  const [portfolioError, setPortfolioError] = useState<string | null>(null);
  const [portfolioLoading, setPortfolioLoading] = useState(false);

  const [recMap, setRecMap] = useState<Record<string, Recommendation>>({});
  const [crowdingMap, setCrowdingMap] = useState<CrowdingMap>({});
  const [recsLoading, setRecsLoading] = useState(false);
  const [signalChanges, setSignalChanges] = useState<SignalChange[]>([]);

  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [watchlistRecs, setWatchlistRecs] = useState<Record<string, WatchlistRecommendation>>({});

  const [commentary, setCommentary] = useState<PortfolioCommentary | null>(null);
  const [commentaryLoading, setCommentaryLoading] = useState(false);
  const [narrativeMap, setNarrativeMap] = useState<Record<string, string | null>>({});

  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [chartPeriod, setChartPeriod] = useState("1y");

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const storedSid = sessionStorage.getItem("ws_session_id");
    const storedProfile = sessionStorage.getItem("ws_profile");
    if (storedSid && storedProfile) {
      try {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSessionId(storedSid);
        setProfile(JSON.parse(storedProfile));
        setAuthLoading(false);
        return;
      } catch { /* fall through to backend restore */ }
    }
    wsRestoreSession()
      .then((res) => {
        if (res.restored && res.session_id && res.profile) {
          sessionStorage.setItem("ws_session_id", res.session_id);
          sessionStorage.setItem("ws_profile", JSON.stringify(res.profile));
          setSessionId(res.session_id);
          setProfile(res.profile);
        }
      })
      .catch(() => {})
      .finally(() => setAuthLoading(false));
  }, []);

  function handleLogin(sid: string, prof: UserProfile) {
    sessionStorage.setItem("ws_session_id", sid);
    sessionStorage.setItem("ws_profile", JSON.stringify(prof));
    setSessionId(sid); setProfile(prof); setAuthLoading(false);
  }

  function handleExpiredSession() {
    setSessionId(null); setProfile(null);
  }

  const loadPortfolio = useCallback(async (sid: string, account: string) => {
    setPortfolioLoading(true); setPortfolioError(null);
    try {
      const [portfolio, hs] = await Promise.all([wsGetPortfolio(sid, account), wsGetHealthScore(sid)]);
      setPositions(portfolio.positions);
      setSummary(portfolio.summary);
      setHealth(hs);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load portfolio";
      if (!sessionStorage.getItem("ws_session_id")) { handleExpiredSession(); return; }
      setPortfolioError(msg);
    } finally { setPortfolioLoading(false); }
  }, []);

  const loadRecs = useCallback(async (sid: string) => {
    setRecsLoading(true);
    try {
      const [recsRes, crowding] = await Promise.all([wsGetRecommendations(sid), wsGetCrowding(sid, 25)]);
      const map: Record<string, Recommendation> = {};
      for (const r of recsRes.recommendations) map[r.symbol] = r;
      setRecMap(map); setCrowdingMap(crowding);
      setSignalChanges(recsRes.signal_changes ?? []);
    } catch { /* non-fatal */ } finally { setRecsLoading(false); }
  }, []);

  const loadWatchlist = useCallback(async (sid: string) => {
    try {
      const [items, recsRes] = await Promise.all([wsGetWatchlist(sid), wsGetWatchlistRecommendations(sid)]);
      setWatchlist(items);
      const map: Record<string, WatchlistRecommendation> = {};
      for (const r of recsRes.recommendations) map[r.symbol] = r;
      setWatchlistRecs(map);
    } catch { /* non-fatal */ }
  }, []);

  const loadCommentary = useCallback(async (sid: string) => {
    setCommentaryLoading(true);
    try {
      const data = await wsGetPortfolioCommentary(sid);
      setCommentary(data);
    } catch { /* non-fatal */ } finally { setCommentaryLoading(false); }
  }, []);

  const loadNarratives = useCallback(async (sid: string) => {
    try {
      const res = await wsGetNarratives(sid);
      setNarrativeMap(res.narratives || {});
    } catch { /* non-fatal */ }
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPortfolio(sessionId, activeAccount);
    loadRecs(sessionId);
    loadWatchlist(sessionId);
    loadCommentary(sessionId);
    loadNarratives(sessionId);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => loadPortfolio(sessionId, activeAccount), 30_000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [sessionId, activeAccount, loadPortfolio, loadRecs, loadWatchlist, loadCommentary, loadNarratives]);

  if (authLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <span className="text-slate-500 text-sm animate-pulse">Checking session…</span>
      </div>
    );
  }
  if (!sessionId || !profile) return <LoginForm onLogin={handleLogin} />;

  const dayPct = summary && summary.total_value > 0 ? (summary.day_change_cad / summary.total_value) * 100 : 0;
  const selectedRec = selectedSymbol
    ? (recMap[selectedSymbol] ?? watchlistRecs[selectedSymbol] ?? null)
    : null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 flex flex-col">
      {/* ── Topbar ── */}
      <div className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-10 shrink-0">
        <div className="max-w-screen-2xl mx-auto px-3 h-10 flex items-center gap-3">
          <span className="font-bold text-sm text-slate-100 shrink-0">aifolimizer</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setActiveAccount("")}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${activeAccount === "" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"}`}>
              All
            </button>
            {profile.account_types.map((t) => (
              <button key={t} onClick={() => setActiveAccount(t)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${activeAccount === t ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"}`}>
                {t}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-3">
            {portfolioLoading && <span className="text-[10px] text-slate-500 animate-pulse">Refreshing…</span>}
            <a href="/agents" className="text-slate-400 hover:text-slate-200 text-xs">Agents →</a>
            <button onClick={() => { loadPortfolio(sessionId, activeAccount); loadRecs(sessionId); }}
              className="text-slate-500 hover:text-slate-300 text-xs">↻ Refresh</button>
          </div>
        </div>
      </div>

      <div className="max-w-[1920px] 2xl:max-w-none mx-auto px-3 py-3 w-full flex flex-col gap-3 flex-1">
        {portfolioError && (
          <div className="bg-rose-950/40 border border-rose-700/40 rounded-lg px-3 py-2 text-xs text-rose-300">{portfolioError}</div>
        )}

        {/* ── Summary strip ── */}
        {summary && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 gap-2 shrink-0">
            <StatCard label="Portfolio (CAD)" value={fmtCAD(summary.total_value)} cls="text-slate-100 font-bold" />
            <StatCard label="Day Change"
              value={`${summary.day_change_cad >= 0 ? "+" : ""}${fmtCAD(summary.day_change_cad)} (${fmtPct(dayPct)})`}
              cls={summary.day_change_cad >= 0 ? "text-emerald-400" : "text-rose-400"} />
            <StatCard
              label="Account Return"
              title="(Current NLV − lifetime net deposits) ÷ deposits. Includes cash interest + realized gains + dividends. Matches WS app top-line return."
              value={
                summary.account_return_pct != null && summary.net_deposits_cad
                  ? fmtPct(summary.account_return_pct)
                  : "—"
              }
              cls={
                (summary.account_return_pct ?? 0) >= 0
                  ? "text-emerald-400" : "text-rose-400"
              }
            />
            <StatCard
              label="Equity Return"
              title="Unrealized PnL on held positions ÷ equity book cost. Excludes cash interest. Negative is normal in high-cash accounts where equity sleeve is small."
              value={fmtPct(summary.total_return_pct)}
              cls={summary.total_return_pct >= 0 ? "text-emerald-400" : "text-rose-400"}
            />
            <StatCard label="Cash (CAD)" value={fmtCAD(summary.cash_available)} cls="text-slate-300" />
            {summary.cash_available_usd != null && summary.cash_available_usd > 0 && (
              <StatCard label="Cash (USD)" value={fmtUSD(summary.cash_available_usd)} cls="text-slate-300" />
            )}
            {health && (
              <StatCard label="Health" value={`${health.grade}  ${health.score}/100`}
                cls={health.grade === "A" ? "text-emerald-400 font-bold" : health.grade === "B" ? "text-blue-400 font-bold" : health.grade === "C" ? "text-amber-400 font-bold" : "text-rose-400 font-bold"} />
            )}
          </div>
        )}

        {/* ── 3-column main ── */}
        <div className="flex gap-3 flex-1 min-h-0">
          {/* Left — Watchlist + Top Movers (capped heights, no empty stretch) */}
          <div className="hidden xl:flex flex-col w-64 2xl:w-80 shrink-0 gap-3 overflow-y-auto">
            <div className="flex flex-col max-h-[55vh]">
              <WatchlistPanel
                sessionId={sessionId}
                watchlist={watchlist}
                watchlistRecs={watchlistRecs}
                selectedSymbol={selectedSymbol}
                onSelect={(s) => { setSelectedSymbol(s); setChartPeriod("1y"); }}
                onRefresh={() => loadWatchlist(sessionId)}
              />
            </div>
            <TopMovers
              positions={positions}
              onSelect={(s) => { setSelectedSymbol(s); setChartPeriod("1y"); }}
            />
          </div>

          {/* Center — Holdings + Chart */}
          <div className="flex-1 min-w-0 flex flex-col gap-3">
            <HoldingsTable
              positions={positions}
              recMap={recMap}
              crowdingMap={crowdingMap}
              recsLoading={recsLoading}
              selectedSymbol={selectedSymbol}
              onSelect={(s) => { setSelectedSymbol(s === selectedSymbol ? null : s); setChartPeriod("1y"); }}
            />
            {selectedSymbol && (
              <PriceChart
                sessionId={sessionId}
                symbol={selectedSymbol}
                period={chartPeriod}
                onPeriodChange={setChartPeriod}
              />
            )}
            {/* Watchlist visible below chart on smaller screens */}
            <div className="xl:hidden">
              <WatchlistPanel
                sessionId={sessionId}
                watchlist={watchlist}
                watchlistRecs={watchlistRecs}
                selectedSymbol={selectedSymbol}
                onSelect={(s) => { setSelectedSymbol(s); setChartPeriod("1y"); }}
                onRefresh={() => loadWatchlist(sessionId)}
              />
            </div>
          </div>

          {/* Right — Health + Alloc + RecDetail + SignalChanges */}
          <div className="hidden lg:flex flex-col w-72 2xl:w-96 shrink-0 gap-3 overflow-y-auto">
            {health && <HealthCompact health={health} />}
            <CommentaryPanel data={commentary} loading={commentaryLoading} />
            {positions.length > 0 && summary && (
              <AllocationDonut positions={positions} cashCAD={summary.cash_available} />
            )}
            {selectedRec ? (
              <RecDetail
                rec={selectedRec}
                narrative={selectedSymbol ? narrativeMap[selectedSymbol] : null}
              />
            ) : (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-3 py-6 text-center text-slate-600 text-xs">
                Click any holding or watchlist item to see the AI signal analysis
              </div>
            )}
            <SignalChangesList
              changes={signalChanges}
              onSelect={(s) => { setSelectedSymbol(s); setChartPeriod("1y"); }}
            />
          </div>
        </div>

        {/* Health + Alloc visible on smaller screens (below the main grid) */}
        <div className="lg:hidden flex flex-col gap-3">
          {health && <HealthCompact health={health} />}
          <CommentaryPanel data={commentary} loading={commentaryLoading} />
          {positions.length > 0 && summary && (
            <AllocationDonut positions={positions} cashCAD={summary.cash_available} />
          )}
          {selectedRec && (
            <RecDetail
              rec={selectedRec}
              narrative={selectedSymbol ? narrativeMap[selectedSymbol] : null}
            />
          )}
          <SignalChangesList
            changes={signalChanges}
            onSelect={(s) => { setSelectedSymbol(s); setChartPeriod("1y"); }}
          />
        </div>
      </div>
    </div>
  );
}
