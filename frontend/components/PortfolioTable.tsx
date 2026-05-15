"use client";

import { memo } from "react";
import { Position } from "@/lib/api";

interface Props {
  positions: Position[];
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

const TableRow = memo(function TableRow({
  position,
  isSelected,
  onSelect,
}: {
  position: Position;
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
    </tr>
  );
});

function PortfolioTable({ positions, onSelectTicker, selectedTicker }: Props) {
  if (!positions.length) return <p className="text-slate-400 text-sm">No positions found.</p>;

  return (
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
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-700/50">
          {positions.map((p) => (
            <TableRow
              key={p.symbol}
              position={p}
              isSelected={selectedTicker === p.symbol}
              onSelect={onSelectTicker || (() => {})}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(PortfolioTable);
