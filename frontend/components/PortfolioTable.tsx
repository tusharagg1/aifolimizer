"use client";

import { memo, useState } from "react";
import { Position, CrowdingMap, CrowdingSignal } from "@/lib/api";

interface Props {
  positions: Position[];
  crowding?: CrowdingMap;
  onSelectTicker?: (symbol: string) => void;
  selectedTicker?: string | null;
}

function pct(val: number) {
  const sign = val >= 0 ? "+" : "";
  return `${sign}${val.toFixed(2)}%`;
}

function formatMoney(val: number, cur: string = "CAD") {
  return val.toLocaleString("en-CA", { style: "currency", currency: cur });
}

function CrowdingCell({ data }: { data?: CrowdingSignal }) {
  if (!data || data.crowding_score == null) {
    return <span className="text-xs text-slate-600">—</span>;
  }
  const label = data.crowding_label;
  const score = data.crowding_score;
  const style =
    label === "consensus"
      ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
      : label === "contrarian"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : "bg-slate-700/40 text-slate-300 border-slate-600/40";
  const title =
    `Crowding score ${score.toFixed(0)}/100 (${label}). ` +
    `Inst ${data.institutional_ownership_pct ?? "?"}% · ` +
    `Short ${data.short_pct_float ?? "?"}% · ` +
    `Analysts ${data.analyst_count ?? "?"} · ` +
    `News 7d ${data.headlines_7d}/30d ${data.headlines_30d}`;
  return (
    <span
      title={title}
      className={`text-[10px] px-2 py-0.5 rounded-full border ${style} font-medium`}
    >
      {label} · {score.toFixed(0)}
    </span>
  );
}

const TableRow = memo(function TableRow({
  position,
  crowding,
  isSelected,
  onSelect,
}: {
  position: Position;
  crowding?: CrowdingSignal;
  isSelected: boolean;
  onSelect: (symbol: string) => void;
}) {
  return (
    <tr
      className={`transition-colors cursor-pointer ${
        isSelected
          ? "bg-indigo-950/60 border-l-2 border-indigo-500"
          : "bg-slate-900 hover:bg-slate-800/60"
      }`}
      onClick={() => onSelect(position.symbol)}
    >
      <td className="px-4 py-3 font-semibold text-white">{position.symbol}</td>
      <td className="px-4 py-3 text-slate-300 max-w-[160px] truncate">{position.name}</td>
      <td className="px-4 py-3 text-right text-slate-300">{position.quantity}</td>
      <td className="px-4 py-3 text-right text-slate-300">
        {formatMoney(position.book_cost_cad)}
      </td>
      <td className="px-4 py-3 text-right text-white font-medium">
        {formatMoney(position.market_value_cad)}
      </td>
      <td className={`px-4 py-3 text-right font-medium ${position.day_change_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
        {pct(position.day_change_pct)}
      </td>
      <td className={`px-4 py-3 text-right font-medium ${position.total_return_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
        {pct(position.total_return_pct)}
      </td>
      <td className="px-4 py-3 text-right text-slate-400">{position.weight.toFixed(1)}%</td>
      <td className="px-4 py-3">
        <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-300 capitalize">
          {position.asset_class}
        </span>
      </td>
      <td className="px-4 py-3">
        <CrowdingCell data={crowding} />
      </td>
    </tr>
  );
});

const ROW_LIMIT = 8;

function PortfolioTable({ positions, crowding, onSelectTicker, selectedTicker }: Props) {
  const [showAll, setShowAll] = useState(false);
  if (!positions.length) return <p className="text-slate-400 text-sm">No positions found.</p>;

  const visible = showAll ? positions : positions.slice(0, ROW_LIMIT);
  const hiddenCount = positions.length - ROW_LIMIT;

  return (
    <>
      <div className="overflow-x-auto rounded-xl border border-slate-700">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-800 text-slate-400 text-left">
              <th className="px-4 py-3 font-medium">Symbol</th>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium text-right">Qty</th>
              <th className="px-4 py-3 font-medium text-right">Book Cost (CAD)</th>
              <th className="px-4 py-3 font-medium text-right">Mkt Value (CAD)</th>
              <th className="px-4 py-3 font-medium text-right">Day %</th>
              <th className="px-4 py-3 font-medium text-right">Total %</th>
              <th className="px-4 py-3 font-medium text-right">Weight</th>
              <th className="px-4 py-3 font-medium">Class</th>
              <th className="px-4 py-3 font-medium" title="Crowding score 0-100 (consensus ≥70, contrarian ≤30). Late entries on consensus names have negative expected alpha per 2025 Goldman/BlackRock research.">Crowding</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {visible.map((p) => (
              <TableRow
                key={p.symbol}
                position={p}
                crowding={crowding?.[p.symbol]}
                isSelected={selectedTicker === p.symbol}
                onSelect={onSelectTicker || (() => {})}
              />
            ))}
          </tbody>
        </table>
      </div>
      {!showAll && hiddenCount > 0 && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-2 w-full text-xs text-slate-500 hover:text-slate-300 transition-colors py-1.5 border border-slate-800 hover:border-slate-700 rounded-lg"
        >
          Show {hiddenCount} more positions
        </button>
      )}
      {showAll && positions.length > ROW_LIMIT && (
        <button
          onClick={() => setShowAll(false)}
          className="mt-2 w-full text-xs text-slate-500 hover:text-slate-300 transition-colors py-1.5 border border-slate-800 hover:border-slate-700 rounded-lg"
        >
          ▲ collapse
        </button>
      )}
    </>
  );
}

export default memo(PortfolioTable);
