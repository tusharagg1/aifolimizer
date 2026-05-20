"use client";

import { useEffect, useState, useMemo, memo } from "react";
import {
  ComposedChart,
  Line,
  ReferenceLine,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from "recharts";
import { wsGetPriceHistory, wsGetPatterns, PriceHistory, ChartPattern } from "@/lib/api";

const PERIODS = ["1mo", "3mo", "6mo", "1y", "2y"] as const;
type Period = (typeof PERIODS)[number];

interface Props {
  symbol: string;
  sessionId: string;
  currency?: string;
}

interface ChartPoint {
  date: string;
  close: number;
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  weeklySma20: number | null;
}

const OVERLAY_KEYS = ["sma20", "sma50", "sma200", "weeklySma20"] as const;
type OverlayKey = (typeof OVERLAY_KEYS)[number];

const OVERLAY_META: Record<OverlayKey, { label: string; color: string; dash?: string }> = {
  sma20:      { label: "SMA 20",       color: "#22d3ee" },
  sma50:      { label: "SMA 50",       color: "#f59e0b", dash: "4 2" },
  sma200:     { label: "SMA 200",      color: "#f43f5e", dash: "6 3" },
  weeklySma20:{ label: "Weekly SMA 20",color: "#a78bfa", dash: "2 4" },
};

function patternColor(p: ChartPattern) {
  return p.confirmed ? (p.bearish ? "#f43f5e" : "#22c55e") : "#94a3b8";
}

function patternLabel(p: ChartPattern) {
  const names: Record<string, string> = {
    double_top: "DT",
    double_bottom: "DB",
    head_and_shoulders: "H&S",
    inverse_head_and_shoulders: "IH&S",
  };
  return names[p.pattern] ?? p.pattern;
}

function PriceChart({ symbol, sessionId, currency }: Props) {
  const [period, setPeriod] = useState<Period>("1y");
  const [data, setData] = useState<ChartPoint[]>([]);
  const [patterns, setPatterns] = useState<ChartPattern[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeOverlays, setActiveOverlays] = useState<Set<OverlayKey>>(
    new Set(["sma50"])
  );

  useEffect(() => {
    if (!symbol || !sessionId) return;
    // Abort prior in-flight fetches when symbol/period switches rapidly.
    // Without this, slower stale responses can clobber fresher state.
    const ctrl = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);

    Promise.all([
      wsGetPriceHistory(sessionId, symbol, period, ctrl.signal),
      wsGetPatterns(sessionId, symbol, period, ctrl.signal),
    ])
      .then(([h, p]: [PriceHistory, import("@/lib/api").PatternResult]) => {
        if (ctrl.signal.aborted) return;
        setData(
          h.dates.map((d, i) => ({
            date: d,
            close: h.close[i],
            sma20: h.sma_20[i],
            sma50: h.sma_50[i],
            sma200: h.sma_200[i],
            weeklySma20: h.weekly_sma_20[i],
          }))
        );
        setPatterns(p.patterns ?? []);
      })
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        setError(e.message);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [symbol, sessionId, period]);

  const cur = currency || (symbol.endsWith(".TO") || symbol.endsWith(".V") ? "CAD" : "USD");
  const formatPrice = useMemo(
    () => (v: number) =>
      v.toLocaleString("en-CA", { style: "currency", currency: cur, maximumFractionDigits: 2 }),
    [cur]
  );

  const domain = useMemo(() => {
    if (!data.length) return ["auto", "auto"] as const;
    const closes = data.map((d) => d.close);
    return [Math.min(...closes) * 0.97, Math.max(...closes) * 1.03] as const;
  }, [data]);

  const toggleOverlay = (key: OverlayKey) => {
    setActiveOverlays((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  // Map pattern neckline dates to chart x-axis for ReferenceLine
  const patternLines = useMemo(() => {
    if (!data.length || !patterns.length) return [];
    const dateSet = new Set(data.map((d) => d.date));
    return patterns.map((p) => {
      // Pick the most recent key date for the vertical marker
      const candidates = [
        p.peak2_date, p.trough2_date, p.right_shoulder_date,
        p.peak1_date, p.trough1_date, p.left_shoulder_date,
      ].filter((d): d is string => !!d && dateSet.has(d));
      return { pattern: p, date: candidates[0] ?? null };
    }).filter((l) => l.date !== null);
  }, [data, patterns]);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <span className="text-sm font-semibold text-white font-mono">{symbol}</span>
          {patterns.length > 0 && (
            <span className="ml-2 text-xs text-amber-400">
              {patterns.length} pattern{patterns.length > 1 ? "s" : ""} detected
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-2 py-0.5 text-xs rounded transition-colors ${
                period === p
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-slate-300 hover:bg-slate-800"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Overlay toggles */}
      <div className="flex gap-2 flex-wrap mb-3">
        {OVERLAY_KEYS.map((key) => {
          const meta = OVERLAY_META[key];
          const on = activeOverlays.has(key);
          return (
            <button
              key={key}
              onClick={() => toggleOverlay(key)}
              className={`flex items-center gap-1 px-2 py-0.5 text-[10px] rounded border transition-colors ${
                on
                  ? "border-transparent text-slate-900"
                  : "border-slate-700 text-slate-500 bg-transparent"
              }`}
              style={on ? { backgroundColor: meta.color } : {}}
            >
              <span
                className="inline-block w-3 h-0.5 rounded"
                style={{ backgroundColor: on ? "#0f172a" : meta.color }}
              />
              {meta.label}
            </button>
          );
        })}
      </div>

      {/* Pattern badges */}
      {patterns.length > 0 && (
        <div className="flex gap-2 flex-wrap mb-3">
          {patterns.map((p, i) => (
            <div
              key={i}
              className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] border"
              style={{
                borderColor: patternColor(p),
                color: patternColor(p),
                backgroundColor: `${patternColor(p)}18`,
              }}
              title={p.description}
            >
              <span className="font-bold">{patternLabel(p)}</span>
              <span>{p.confirmed ? "✓ confirmed" : "forming"}</span>
              <span className="text-slate-500">neck ${p.neckline.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}

      {loading && (
        <div className="h-52 flex items-center justify-center text-slate-600 text-sm">
          Loading…
        </div>
      )}
      {error && (
        <div className="h-52 flex items-center justify-center text-rose-400 text-sm">
          {error}
        </div>
      )}
      {!loading && !error && data.length > 0 && (
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "#64748b" }}
              tickFormatter={(v: string) => v.slice(5)}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={domain}
              tick={{ fontSize: 10, fill: "#64748b" }}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`}
              width={48}
            />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
              labelStyle={{ color: "#94a3b8", fontSize: 11 }}
              formatter={(value, name) => {
                const labels: Record<string, string> = {
                  close: "Close",
                  sma20: "SMA 20",
                  sma50: "SMA 50",
                  sma200: "SMA 200",
                  weeklySma20: "Weekly SMA 20",
                };
                return [
                  typeof value === "number" ? formatPrice(value) : String(value),
                  labels[name as string] ?? String(name),
                ];
              }}
            />
            <Legend
              wrapperStyle={{ fontSize: 10, color: "#64748b" }}
              formatter={(value) => {
                const labels: Record<string, string> = {
                  close: "Price",
                  sma20: "SMA 20",
                  sma50: "SMA 50",
                  sma200: "SMA 200",
                  weeklySma20: "Weekly SMA 20",
                };
                return labels[value] ?? value;
              }}
            />

            {/* Pattern neckline reference lines */}
            {patternLines.map(({ pattern: p, date }, i) => (
              <ReferenceLine
                key={`pat-v-${i}`}
                x={date!}
                stroke={patternColor(p)}
                strokeDasharray="3 3"
                strokeWidth={1}
                label={{ value: patternLabel(p), position: "top", fontSize: 9, fill: patternColor(p) }}
              />
            ))}
            {patterns.map((p, i) => (
              <ReferenceLine
                key={`pat-h-${i}`}
                y={p.neckline}
                stroke={patternColor(p)}
                strokeDasharray="4 2"
                strokeWidth={1}
                strokeOpacity={0.6}
              />
            ))}

            <Line
              type="monotone"
              dataKey="close"
              stroke="#6366f1"
              dot={false}
              strokeWidth={1.5}
            />
            {OVERLAY_KEYS.filter((k) => activeOverlays.has(k)).map((key) => {
              const meta = OVERLAY_META[key];
              return (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={meta.color}
                  dot={false}
                  strokeWidth={1}
                  strokeDasharray={meta.dash}
                  connectNulls
                />
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default memo(PriceChart);
