"use client";

import { memo } from "react";
import { BenchmarkResult } from "@/lib/api";

const PERIOD_ORDER = ["1mo", "3mo", "6mo", "1y", "3y"];
const BENCHMARK_ORDER = ["XEQT.TO", "SPY", "QQQ", "VFV.TO", "^GSPTSE"];

function pct(v: number | null | undefined) {
  if (v == null) return "—";
  const color = v >= 0 ? "text-emerald-400" : "text-rose-400";
  return <span className={color}>{v >= 0 ? "+" : ""}{v.toFixed(1)}%</span>;
}

function alphaBadge(v: number | null | undefined) {
  if (v == null) return null;
  const color = v >= 0 ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400";
  return (
    <span className={`text-[10px] px-1 py-0.5 rounded font-medium ${color}`}>
      {v >= 0 ? "+" : ""}{v.toFixed(1)}α
    </span>
  );
}

interface Props {
  data: BenchmarkResult | null;
  loading: boolean;
}

function BenchmarkWidget({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-3">Portfolio vs Benchmarks</h2>
        <div className="h-32 bg-slate-800/40 rounded-lg animate-pulse" />
      </div>
    );
  }

  if (!data?.periods) return null;

  const periods = PERIOD_ORDER.filter(p => data.periods[p]);
  const activeBenchmarks = BENCHMARK_ORDER.filter(b =>
    periods.some(p => data.periods[p]?.benchmarks[b] != null)
  );

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-white">Are You Beating the Market?</h2>
        <p className="text-[10px] text-slate-500 mt-0.5">Compares your portfolio return against index funds. If alpha is consistently negative, a simple XEQT or SPY ETF would have outperformed you with less effort.</p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-500 border-b border-slate-800">
              <th className="text-left pb-2 font-medium w-28">Period</th>
              <th className="text-right pb-2 font-medium text-white">Your Portfolio</th>
              {activeBenchmarks.map(b => (
                <th key={b} className="text-right pb-2 font-medium pl-3">
                  {data.benchmarks_meta[b]?.split(" ")[0] ?? b}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.map(period => {
              const row = data.periods[period];
              return (
                <tr key={period} className="border-b border-slate-800/50 last:border-0">
                  <td className="py-1.5 text-slate-400">{row.label}</td>
                  <td className="py-1.5 text-right font-semibold">
                    {pct(row.portfolio_return)}
                  </td>
                  {activeBenchmarks.map(b => (
                    <td key={b} className="py-1.5 text-right pl-3">
                      <div className="flex items-center justify-end gap-1">
                        {pct(row.benchmarks[b])}
                        {alphaBadge(row.alpha?.[b])}
                      </div>
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-slate-600">
        α = your return minus the benchmark. Green = you beat it. Red = the index beat you.
      </p>
    </div>
  );
}

export default memo(BenchmarkWidget);
