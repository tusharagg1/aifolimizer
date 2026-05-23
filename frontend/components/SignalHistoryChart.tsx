"use client";

import { memo, useMemo } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import type { SignalHistoryResponse } from "@/lib/api";

interface SignalHistoryChartProps {
  data: SignalHistoryResponse;
  showSubSignals?: boolean;
}

interface ChartPoint {
  ts: string;
  score: number | null;
  tech: number | null;
  fund: number | null;
  macro: number | null;
  sentiment: number | null;
  skill: number | null;
}

function SignalHistoryChartImpl({
  data,
  showSubSignals = false,
}: SignalHistoryChartProps) {
  const points = useMemo<ChartPoint[]>(() => {
    return data.points.map((p) => ({
      ts: p.ts ? new Date(p.ts).toLocaleDateString() : "",
      score: p.score,
      tech: p.tech,
      fund: p.fund,
      macro: p.macro,
      sentiment: p.sentiment,
      skill: p.skill,
    }));
  }, [data.points]);

  if (points.length === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6 text-center text-sm text-slate-500">
        No signal history for <span className="font-mono">{data.symbol}</span>{" "}
        in the last {data.days} days.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-lg font-semibold text-slate-100">
          {data.symbol} signal · last {data.days}d
        </h3>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={points}
            margin={{ top: 5, right: 8, bottom: 5, left: -16 }}
          >
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis
              dataKey="ts"
              tick={{ fontSize: 10, fill: "#64748b" }}
              minTickGap={20}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "#64748b" }}
              domain={[0, 10]}
            />
            <Tooltip
              contentStyle={{
                background: "#0f172a",
                border: "1px solid #334155",
                fontSize: "12px",
              }}
            />
            <Legend wrapperStyle={{ fontSize: "11px" }} />
            <Line
              type="monotone"
              dataKey="score"
              stroke="#a78bfa"
              strokeWidth={2}
              dot={false}
              name="Integrated"
            />
            {showSubSignals && (
              <>
                <Line type="monotone" dataKey="tech" stroke="#22c55e"
                  dot={false} strokeOpacity={0.6} />
                <Line type="monotone" dataKey="fund" stroke="#3b82f6"
                  dot={false} strokeOpacity={0.6} />
                <Line type="monotone" dataKey="macro" stroke="#f59e0b"
                  dot={false} strokeOpacity={0.6} />
                <Line type="monotone" dataKey="sentiment" stroke="#06b6d4"
                  dot={false} strokeOpacity={0.6} />
                <Line type="monotone" dataKey="skill" stroke="#ec4899"
                  dot={false} strokeOpacity={0.6} />
              </>
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export const SignalHistoryChart = memo(SignalHistoryChartImpl);
SignalHistoryChart.displayName = "SignalHistoryChart";
