"use client";

import { memo } from "react";
import { MacroSnapshot } from "@/lib/api";

const REGIME_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  bull_low_fear:  { label: "Bull · Low Fear",  color: "text-emerald-400", bg: "bg-emerald-500/10 border-emerald-500/25" },
  bull_high_fear: { label: "Bull · High Fear", color: "text-amber-400",   bg: "bg-amber-500/10 border-amber-500/25" },
  bear_high_fear: { label: "Bear · High Fear", color: "text-rose-400",    bg: "bg-rose-500/10 border-rose-500/25" },
  bear_low_fear:  { label: "Bear · Low Fear",  color: "text-orange-400",  bg: "bg-orange-500/10 border-orange-500/25" },
};

function FG_COLOR(score: number) {
  if (score >= 75) return "text-rose-400";
  if (score >= 55) return "text-amber-400";
  if (score <= 25) return "text-emerald-400";
  if (score <= 45) return "text-sky-400";
  return "text-slate-300";
}

interface Props {
  data: MacroSnapshot | null;
  loading: boolean;
}

function MacroWidget({ data, loading }: Props) {
  if (loading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 h-full">
        <p className="text-xs text-slate-500 mb-3">Macro</p>
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-5 bg-slate-800/60 rounded animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 h-full flex items-center justify-center">
        <p className="text-xs text-slate-600">No macro data</p>
      </div>
    );
  }

  const regime = data.market_regime || "bull_low_fear";
  const regimeCfg = REGIME_CONFIG[regime] ?? REGIME_CONFIG["bull_low_fear"];
  const fred = data.fred ?? {};

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 h-full space-y-3">
      <p className="text-xs text-slate-500">Macro</p>

      {/* Regime badge */}
      <div className={`border rounded-lg px-3 py-2 ${regimeCfg.bg}`}>
        <p className={`text-xs font-semibold ${regimeCfg.color}`}>{regimeCfg.label}</p>
        <p className="text-[10px] text-slate-400 mt-0.5 leading-snug">{data.regime_signal}</p>
      </div>

      {/* Market indicators */}
      <div className="space-y-1.5">
        {data.vix !== null && data.vix !== undefined && (
          <MacroRow
            label="VIX"
            value={data.vix.toFixed(1)}
            sub={data.vix_signal ?? ""}
            highlight={data.vix > 25 ? "rose" : data.vix < 15 ? "amber" : undefined}
          />
        )}
        {data.spy_vs_sma200_pct !== null && data.spy_vs_sma200_pct !== undefined && (
          <MacroRow
            label="SPY vs SMA200"
            value={`${data.spy_vs_sma200_pct >= 0 ? "+" : ""}${data.spy_vs_sma200_pct.toFixed(1)}%`}
            sub={data.spy_regime ?? ""}
            highlight={data.spy_regime === "bear" ? "rose" : "emerald"}
          />
        )}
        {data.fear_greed_score !== null && data.fear_greed_score !== undefined && (
          <MacroRow
            label="Fear & Greed"
            value={`${data.fear_greed_score.toFixed(0)}`}
            sub={data.fear_greed_rating ?? ""}
            customColor={FG_COLOR(data.fear_greed_score)}
          />
        )}
      </div>

      {/* FRED rates */}
      {(fred.fed_funds || fred.ten_year_yield || fred.cad_usd || fred.boc_overnight) && (
        <div className="pt-1 border-t border-slate-800 space-y-1.5">
          {fred.fed_funds && (
            <MacroRow label="Fed Funds" value={`${fred.fed_funds.value.toFixed(2)}%`} />
          )}
          {fred.ten_year_yield && (
            <MacroRow label="10Y Yield" value={`${fred.ten_year_yield.value.toFixed(2)}%`} />
          )}
          {fred.boc_overnight && (
            <MacroRow label="BoC Rate" value={`${fred.boc_overnight.value.toFixed(2)}%`} />
          )}
          {fred.cad_usd && (
            <MacroRow label="CAD/USD" value={fred.cad_usd.value.toFixed(4)} />
          )}
        </div>
      )}
    </div>
  );
}

export default memo(MacroWidget);

function MacroRow({
  label,
  value,
  sub,
  highlight,
  customColor,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: "rose" | "emerald" | "amber";
  customColor?: string;
}) {
  const valueColor = customColor ?? (
    highlight === "rose" ? "text-rose-400" :
    highlight === "emerald" ? "text-emerald-400" :
    highlight === "amber" ? "text-amber-400" :
    "text-white"
  );

  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-slate-500">{label}</span>
      <div className="text-right">
        <span className={`text-xs font-medium ${valueColor}`}>{value}</span>
        {sub && <span className="text-[10px] text-slate-600 ml-1.5">{sub}</span>}
      </div>
    </div>
  );
}
