"use client";

import { memo } from "react";
import { HealthScore } from "@/lib/api";

const GRADE_STYLE: Record<string, string> = {
  A: "text-emerald-400 border-emerald-400/40 bg-emerald-400/10",
  B: "text-blue-400 border-blue-400/40 bg-blue-400/10",
  C: "text-amber-400 border-amber-400/40 bg-amber-400/10",
  D: "text-orange-400 border-orange-400/40 bg-orange-400/10",
  F: "text-rose-400 border-rose-400/40 bg-rose-400/10",
};

const BREAKDOWN_LABELS: Record<string, string> = {
  diversification: "Diversity",
  concentration: "Concentr.",
  performance: "Return",
  cash_efficiency: "Cash",
  asset_class_diversity: "Classes",
};

const BREAKDOWN_MAX: Record<string, number> = {
  diversification: 30,
  concentration: 30,
  performance: 20,
  cash_efficiency: 10,
  asset_class_diversity: 10,
};

function getImprovements(data: HealthScore): string[] {
  const items: string[] = [];
  const { breakdown, inputs } = data;
  if (breakdown.diversification < 20)
    items.push(`Only ${inputs.position_count} positions — add more to spread risk (target 10+)`);
  if (breakdown.concentration < 20)
    items.push(`Largest position is ${inputs.max_single_weight_pct}% of portfolio — trim toward 20% or below`);
  if (breakdown.cash_efficiency < 7)
    items.push(`${inputs.cash_pct.toFixed(1)}% sitting idle in cash — deploy or park in TFSA HYSA`);
  if (breakdown.asset_class_diversity < 7)
    items.push(`Only ${inputs.asset_classes.length} asset class${inputs.asset_classes.length === 1 ? "" : "es"} — add bonds or crypto for balance`);
  if (breakdown.performance < 12)
    items.push(`Total return ${inputs.total_return_pct.toFixed(1)}% — review underperformers for reallocation`);
  return items;
}

interface Props {
  data: HealthScore | null;
  loading: boolean;
}

function HealthScoreWidget({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <p className="text-xs text-slate-500 mb-2">Portfolio Health</p>
        <div className="h-10 w-24 bg-slate-800 rounded animate-pulse" />
      </div>
    );
  }

  if (!data || data.grade === "N/A") return null;

  const gradeStyle = GRADE_STYLE[data.grade] ?? GRADE_STYLE.C;
  const improvements = getImprovements(data);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
      <div>
        <p className="text-xs text-slate-500">Portfolio Health</p>
        <p className="text-[10px] text-slate-600 mt-0.5">Scores your portfolio on diversification, concentration, returns, cash drag, and asset variety out of 100.</p>
      </div>

      <div className="flex items-center gap-3">
        <span className={`text-2xl font-bold px-3 py-1 rounded-lg border ${gradeStyle}`}>
          {data.grade}
        </span>
        <div>
          <p className="text-white font-semibold text-sm">
            {data.score}
            <span className="text-slate-500 font-normal"> / 100</span>
          </p>
          <p className="text-xs text-slate-400">{data.verdict}</p>
        </div>
      </div>

      <div className="space-y-1.5 border-t border-slate-800 pt-2">
        {Object.entries(data.breakdown).map(([key, val]) => {
          const max = BREAKDOWN_MAX[key] ?? 10;
          const pct = Math.round((val / max) * 100);
          const barColor = pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-rose-500";
          return (
            <div key={key} className="flex items-center gap-2">
              <span className="text-[10px] text-slate-500 w-16 shrink-0">{BREAKDOWN_LABELS[key] ?? key}</span>
              <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
              </div>
              <span className="text-[10px] text-slate-400 w-8 text-right shrink-0">{val}/{max}</span>
            </div>
          );
        })}
      </div>

      <div className="border-t border-slate-800 pt-2">
        {improvements.length > 0 ? (
          <>
            <p className="text-[10px] uppercase tracking-wide text-amber-400 mb-1.5">What to improve</p>
            <ul className="space-y-1">
              {improvements.map((item, i) => (
                <li key={i} className="text-[11px] text-slate-300 flex items-start gap-1.5">
                  <span className="text-amber-400 shrink-0">→</span>
                  {item}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-[11px] text-emerald-400">No major improvements needed.</p>
        )}
      </div>
    </div>
  );
}

export default memo(HealthScoreWidget);
