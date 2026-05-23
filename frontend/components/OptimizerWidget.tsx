"use client";

import { memo } from "react";
import { OptimizerResult } from "@/lib/api";

const ACTION_COLOR: Record<string, string> = {
  INCREASE: "text-emerald-400",
  ADD:      "text-emerald-400",
  DECREASE: "text-rose-400",
  TRIM:     "text-amber-400",
};

interface Props {
  data: OptimizerResult | null;
  loading: boolean;
}

function OptimizerWidget({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-3">Rebalancing Suggestions</h2>
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-8 bg-slate-800/40 rounded animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) return null;
  if (data.error) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <h2 className="text-sm font-semibold text-white mb-2">Efficient Frontier Optimizer</h2>
        <p className="text-xs text-slate-500">{data.error}</p>
      </div>
    );
  }

  const methodLabel = data.method === "black_litterman"
    ? "Black-Litterman (analyst views blended)"
    : "Mean Historical Return";

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-4">
      {/* Header metrics */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">Rebalancing Suggestions</h2>
          <p className="text-[10px] text-slate-500 mt-0.5">Math-optimal weights to maximize return per unit of risk (Sharpe ratio). Shows what to increase, decrease, or trim.</p>
        </div>
        <div className="flex gap-4 text-xs">
          <div className="text-right">
            <p className="text-slate-500">Expected Return</p>
            <p className="text-emerald-400 font-semibold">+{data.expected_annual_return_pct}%</p>
          </div>
          <div className="text-right">
            <p className="text-slate-500">Expected Vol</p>
            <p className="text-amber-400 font-semibold">{data.expected_annual_volatility_pct}%</p>
          </div>
          <div className="text-right">
            <p className="text-slate-500">Sharpe</p>
            <p className={`font-semibold ${data.sharpe_ratio >= 1 ? "text-emerald-400" : data.sharpe_ratio >= 0.5 ? "text-amber-400" : "text-rose-400"}`}>
              {data.sharpe_ratio}
            </p>
          </div>
        </div>
      </div>

      {/* Rebalancing actions */}
      {data.changes.length > 0 && (
        <div>
          <p className="text-[11px] text-slate-500 mb-2">Rebalancing actions (max-Sharpe)</p>
          <div className="space-y-1.5">
            {data.changes.map(c => (
              <div key={c.symbol} className="flex items-center gap-2">
                <span className="font-mono text-xs text-white w-20 shrink-0">{c.symbol}</span>
                <div className="flex-1 relative h-2 bg-slate-800 rounded-full overflow-hidden">
                  {/* Current weight bar */}
                  <div
                    className="absolute left-0 top-0 h-full bg-slate-600 rounded-full"
                    style={{ width: `${Math.min(100, c.current_weight * 2)}%` }}
                  />
                  {/* Optimal weight bar */}
                  <div
                    className={`absolute left-0 top-0 h-full rounded-full opacity-70 ${
                      c.change > 0 ? "bg-emerald-500" : "bg-rose-500"
                    }`}
                    style={{ width: `${Math.min(100, c.optimal_weight * 2)}%` }}
                  />
                </div>
                <span className="text-[10px] text-slate-500 w-10 text-right shrink-0">
                  {c.current_weight.toFixed(1)}%
                </span>
                <span className="text-[10px] text-slate-600">→</span>
                <span className={`text-[10px] font-semibold w-10 shrink-0 ${ACTION_COLOR[c.action]}`}>
                  {c.optimal_weight.toFixed(1)}%
                </span>
                <span className={`text-[10px] font-medium w-14 text-right shrink-0 ${ACTION_COLOR[c.action]}`}>
                  {c.action}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.changes.length === 0 && (
        <p className="text-xs text-emerald-400">Portfolio is near-optimal — no significant rebalancing needed.</p>
      )}

      <p className="text-[10px] text-slate-600">
        Method: {methodLabel} · RF rate: {data.risk_free_rate_pct}%
        {data.missing_symbols.length > 0 && ` · Excluded: ${data.missing_symbols.join(", ")}`}
      </p>
    </div>
  );
}

export default memo(OptimizerWidget);
