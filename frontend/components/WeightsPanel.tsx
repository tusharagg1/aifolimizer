"use client";

import { memo } from "react";
import type { WeightsResponse, WeightsRow } from "@/lib/api";

interface WeightCellProps {
  label: string;
  value: number;
}

function WeightCell({ label, value }: WeightCellProps) {
  // Visualize 0..1.5 weight as bar width.
  const widthPct = Math.min(100, (value / 1.5) * 100);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="text-slate-200 font-mono tabular-nums">
          {value.toFixed(2)}
        </span>
      </div>
      <div className="h-1.5 rounded bg-slate-800">
        <div
          className="h-full rounded bg-indigo-500/70"
          style={{ width: `${widthPct}%` }}
        />
      </div>
    </div>
  );
}

interface HistoryRowProps {
  row: WeightsRow;
}

function HistoryRow({ row }: HistoryRowProps) {
  const ts = row.ts ? new Date(row.ts).toLocaleString() : "—";
  return (
    <tr className="border-b border-slate-800/60">
      <td className="px-2 py-1.5 text-slate-300 font-mono">v{row.version}</td>
      <td className="px-2 py-1.5 text-slate-500 text-xs">{ts}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">
        {row.w_tech.toFixed(2)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums">
        {row.w_fund.toFixed(2)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums">
        {row.w_macro.toFixed(2)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums">
        {row.w_sentiment.toFixed(2)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-indigo-300">
        {row.w_skill.toFixed(2)}
      </td>
      <td className="px-2 py-1.5 text-slate-500 text-xs">{row.reason ?? "—"}</td>
    </tr>
  );
}

interface WeightsPanelProps {
  data: WeightsResponse;
}

function WeightsPanelImpl({ data }: WeightsPanelProps) {
  const c = data.current;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-lg font-semibold text-slate-100">Signal Weights</h3>
        <span className="text-xs text-slate-500">
          v{c.version}
          {c.objective ? ` · ${c.objective}` : ""}
        </span>
      </div>

      <div className="grid grid-cols-5 gap-3">
        <WeightCell label="Tech" value={c.w_tech} />
        <WeightCell label="Fund" value={c.w_fund} />
        <WeightCell label="Macro" value={c.w_macro} />
        <WeightCell label="Sent" value={c.w_sentiment} />
        <WeightCell label="Skill" value={c.w_skill} />
      </div>

      {data.history.length > 0 && (
        <div>
          <p className="text-xs text-slate-500 mb-2">
            History (last {data.history.length} versions)
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-slate-800">
                  <th className="px-2 py-1.5 text-left">Ver</th>
                  <th className="px-2 py-1.5 text-left">When</th>
                  <th className="px-2 py-1.5 text-right">Tech</th>
                  <th className="px-2 py-1.5 text-right">Fund</th>
                  <th className="px-2 py-1.5 text-right">Macro</th>
                  <th className="px-2 py-1.5 text-right">Sent</th>
                  <th className="px-2 py-1.5 text-right">Skill</th>
                  <th className="px-2 py-1.5 text-left">Reason</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((row) => (
                  <HistoryRow key={row.version ?? 0} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export const WeightsPanel = memo(WeightsPanelImpl);
WeightsPanel.displayName = "WeightsPanel";
