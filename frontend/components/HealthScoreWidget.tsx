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

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <p className="text-xs text-slate-500 mb-2">Portfolio Health</p>
      <div className="flex items-center gap-3 mb-3">
        <span
          className={`text-2xl font-bold px-3 py-1 rounded-lg border ${gradeStyle}`}
        >
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
      <div className="grid grid-cols-5 gap-1 border-t border-slate-800 pt-2">
        {Object.entries(data.breakdown).map(([key, val]) => (
          <div key={key} className="text-center">
            <p className="text-xs font-medium text-white">{val}</p>
            <p className="text-[10px] text-slate-500 leading-tight">
              {BREAKDOWN_LABELS[key] ?? key}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default memo(HealthScoreWidget);
