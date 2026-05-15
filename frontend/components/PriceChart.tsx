"use client";

import { useEffect, useState, useMemo, memo } from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { wsGetPriceHistory, PriceHistory } from "@/lib/api";

const PERIODS = ["1mo", "3mo", "6mo", "1y"] as const;
type Period = (typeof PERIODS)[number];

interface Props {
  symbol: string;
  sessionId: string;
}

interface ChartPoint {
  date: string;
  close: number;
  sma50: number | null;
}

function PriceChart({ symbol, sessionId }: Props) {
  const [period, setPeriod] = useState<Period>("1y");
  const [data, setData] = useState<ChartPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol || !sessionId) return;
    setLoading(true);
    setError(null);
    wsGetPriceHistory(sessionId, symbol, period)
      .then((h: PriceHistory) => {
        const points: ChartPoint[] = h.dates.map((d, i) => ({
          date: d,
          close: h.close[i],
          sma50: h.sma_50[i],
        }));
        setData(points);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [symbol, sessionId, period]);

  const formatPrice = useMemo(
    () => (v: number) =>
      v.toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 2 }),
    []
  );

  const domain = useMemo(() => {
    if (!data.length) return ["auto", "auto"];
    const closes = data.map((d) => d.close);
    return [Math.min(...closes) * 0.97, Math.max(...closes) * 1.03];
  }, [data]);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="text-sm font-semibold text-white font-mono">{symbol}</span>
          <span className="text-xs text-slate-500 ml-2">price · SMA50</span>
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

      {loading && (
        <div className="h-48 flex items-center justify-center text-slate-600 text-sm">
          Loading…
        </div>
      )}
      {error && (
        <div className="h-48 flex items-center justify-center text-rose-400 text-sm">
          {error}
        </div>
      )}
      {!loading && !error && data.length > 0 && (
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "#64748b" }}
              tickFormatter={(v: string) => v.slice(5)}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={domain as [number, number]}
              tick={{ fontSize: 10, fill: "#64748b" }}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`}
              width={48}
            />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
              labelStyle={{ color: "#94a3b8", fontSize: 11 }}
              formatter={(value, name) => [
                typeof value === "number" ? formatPrice(value) : String(value),
                name === "close" ? "Close" : "SMA 50",
              ]}
            />
            <Line
              type="monotone"
              dataKey="close"
              stroke="#6366f1"
              dot={false}
              strokeWidth={1.5}
            />
            <Line
              type="monotone"
              dataKey="sma50"
              stroke="#f59e0b"
              dot={false}
              strokeWidth={1}
              strokeDasharray="4 2"
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default memo(PriceChart);
