"use client";

import { memo } from "react";
import type { LiveKPIs } from "@/lib/api";

interface KPITileProps {
  label: string;
  value: string;
  hint?: string;
  /** Color tier: good / neutral / bad. */
  tier?: "good" | "neutral" | "bad";
}

function KPITile({ label, value, hint, tier = "neutral" }: KPITileProps) {
  const tierClass = {
    good: "text-emerald-300",
    neutral: "text-slate-200",
    bad: "text-rose-300",
  }[tier];
  return (
    <div className="rounded border border-slate-800 bg-slate-900/60 p-3">
      <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${tierClass}`}>{value}</p>
      {hint && <p className="text-xs text-slate-500 mt-1">{hint}</p>}
    </div>
  );
}

function tierFromPF(pf: number): "good" | "neutral" | "bad" {
  if (pf >= 1.5) return "good";
  if (pf < 1.0) return "bad";
  return "neutral";
}

function tierFromSharpe(s: number): "good" | "neutral" | "bad" {
  if (s >= 1.5) return "good";
  if (s < 0.5) return "bad";
  return "neutral";
}

function tierFromDD(dd: number): "good" | "neutral" | "bad" {
  // dd is negative (e.g. -12.5)
  if (dd >= -10) return "good";
  if (dd <= -25) return "bad";
  return "neutral";
}

function tierFromEV(ev: number): "good" | "neutral" | "bad" {
  if (ev > 0.5) return "good";
  if (ev < 0) return "bad";
  return "neutral";
}

interface LiveKPIPanelProps {
  kpis: LiveKPIs;
}

function LiveKPIPanelImpl({ kpis }: LiveKPIPanelProps) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-lg font-semibold text-slate-100">
          Live KPIs · trailing {kpis.window_days}d
        </h3>
        <span className="text-xs text-slate-500">
          {kpis.n_trades} closed trade{kpis.n_trades === 1 ? "" : "s"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <KPITile
          label="Profit Factor"
          value={
            Number.isFinite(kpis.profit_factor)
              ? kpis.profit_factor.toFixed(2)
              : "∞"
          }
          hint="Σwin / Σloss · target >1.5"
          tier={tierFromPF(kpis.profit_factor)}
        />
        <KPITile
          label="Sharpe"
          value={kpis.sharpe.toFixed(2)}
          hint="annualized · target >1.0"
          tier={tierFromSharpe(kpis.sharpe)}
        />
        <KPITile
          label="Max DD"
          value={`${kpis.max_drawdown_pct.toFixed(1)}%`}
          hint="peak-to-trough · keep >-20%"
          tier={tierFromDD(kpis.max_drawdown_pct)}
        />
        <KPITile
          label="Expectancy"
          value={`${kpis.expectancy_pct.toFixed(2)}%`}
          hint="avg return per trade"
          tier={tierFromEV(kpis.expectancy_pct)}
        />
        <KPITile
          label="Sortino"
          value={kpis.sortino.toFixed(2)}
          hint="downside-only Sharpe"
        />
        <KPITile
          label="Hit Rate"
          value={`${(kpis.hit_rate * 100).toFixed(0)}%`}
          hint={`avg win ${kpis.avg_win_pct.toFixed(1)}% · avg loss ${kpis.avg_loss_pct.toFixed(1)}%`}
        />
        <KPITile
          label="Tx Cost"
          value={`${kpis.after_cost_drag_bps.toFixed(1)} bps`}
          hint="round-trip"
        />
        <KPITile
          label="Trades"
          value={String(kpis.n_trades)}
          hint="closed in window"
        />
      </div>

      {Object.keys(kpis.regime_breakdown).length > 0 && (
        <div>
          <p className="text-xs text-slate-500 mb-2 uppercase tracking-wider">
            Regime breakdown
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-slate-800">
                  <th className="px-2 py-1.5 text-left">Composite</th>
                  <th className="px-2 py-1.5 text-right">PF</th>
                  <th className="px-2 py-1.5 text-right">Expectancy</th>
                  <th className="px-2 py-1.5 text-right">N</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(kpis.regime_breakdown).map(
                  ([composite, r]) => (
                    <tr
                      key={composite}
                      className="border-b border-slate-800/60"
                    >
                      <td className="px-2 py-1.5 text-slate-300 font-mono">
                        {composite}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {Number.isFinite(r.pf) ? r.pf.toFixed(2) : "∞"}
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">
                        {r.expectancy_pct.toFixed(2)}%
                      </td>
                      <td className="px-2 py-1.5 text-right text-slate-500 tabular-nums">
                        {r.n}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export const LiveKPIPanel = memo(LiveKPIPanelImpl);
LiveKPIPanel.displayName = "LiveKPIPanel";
