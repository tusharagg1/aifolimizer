"use client";

import { memo, useState } from "react";
import { Recommendation } from "@/lib/api";

type Action = "BUY" | "HOLD" | "WATCH" | "SELL" | "ADD" | "TRIM" | "NO_EDGE";

const ACTION_CONFIG: Record<Action, {
  bg: string; border: string; badge: string; text: string; dot: string;
}> = {
  BUY: {
    bg: "bg-emerald-500/8",
    border: "border-emerald-500/25",
    badge: "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30",
    text: "text-emerald-400",
    dot: "bg-emerald-500",
  },
  ADD: {
    bg: "bg-emerald-500/5",
    border: "border-emerald-500/20",
    badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/25",
    text: "text-emerald-300",
    dot: "bg-emerald-400",
  },
  HOLD: {
    bg: "bg-slate-800/30",
    border: "border-slate-700/50",
    badge: "bg-slate-700 text-slate-300 border border-slate-600",
    text: "text-slate-300",
    dot: "bg-slate-500",
  },
  WATCH: {
    bg: "bg-amber-500/8",
    border: "border-amber-500/25",
    badge: "bg-amber-500/20 text-amber-400 border border-amber-500/30",
    text: "text-amber-400",
    dot: "bg-amber-500",
  },
  TRIM: {
    bg: "bg-orange-500/8",
    border: "border-orange-500/25",
    badge: "bg-orange-500/20 text-orange-400 border border-orange-500/30",
    text: "text-orange-400",
    dot: "bg-orange-500",
  },
  SELL: {
    bg: "bg-rose-500/8",
    border: "border-rose-500/25",
    badge: "bg-rose-500/20 text-rose-400 border border-rose-500/30",
    text: "text-rose-400",
    dot: "bg-rose-500",
  },
  NO_EDGE: {
    bg: "bg-slate-900/40",
    border: "border-slate-800/60",
    badge: "bg-slate-800 text-slate-400 border border-slate-700",
    text: "text-slate-400",
    dot: "bg-slate-600",
  },
};

const REGIME_LABELS: Record<string, { label: string; color: string }> = {
  bull_low_fear:  { label: "Bull · Low Fear",  color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" },
  bull_high_fear: { label: "Bull · High Fear", color: "text-amber-400 bg-amber-500/10 border-amber-500/20" },
  bear_high_fear: { label: "Bear · High Fear", color: "text-rose-400 bg-rose-500/10 border-rose-500/20" },
  bear_low_fear:  { label: "Bear · Low Fear",  color: "text-orange-400 bg-orange-500/10 border-orange-500/20" },
};

interface Props {
  data: Recommendation[] | null;
  loading: boolean;
  narratives?: Record<string, string | null>;
  narrativesLoading?: boolean;
  narrativeProviders?: string[];
}

function RecommendationsPanel({
  data,
  loading,
  narratives,
  narrativesLoading,
  narrativeProviders,
}: Props) {
  if (loading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-white">Recommendations</h2>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-36 bg-slate-800/40 rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!data || data.length === 0) return null;

  const groups: Record<Action, Recommendation[]> = {
    SELL: data.filter(r => r.action === "SELL"),
    TRIM: data.filter(r => r.action === "TRIM"),
    BUY:  data.filter(r => r.action === "BUY"),
    ADD:  data.filter(r => r.action === "ADD"),
    WATCH: data.filter(r => r.action === "WATCH"),
    HOLD: data.filter(r => r.action === "HOLD"),
    NO_EDGE: data.filter(r => r.action === "NO_EDGE"),
  };

  const regime = data[0]?.market_regime ?? "";
  const regimeInfo = REGIME_LABELS[regime];

  const counts = (["SELL", "TRIM", "BUY", "ADD", "WATCH", "HOLD", "NO_EDGE"] as Action[])
    .filter(a => groups[a].length > 0)
    .map(a => ({ action: a, count: groups[a].length }));

  const providerLabel = narrativeProviders?.length
    ? `AI via ${narrativeProviders[0]}`
    : null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-white">Recommendations</h2>
          {narrativesLoading && (
            <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded-full animate-pulse">
              AI loading…
            </span>
          )}
          {providerLabel && !narrativesLoading && (
            <span className="text-[10px] text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-1.5 py-0.5 rounded-full">
              {providerLabel}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {regimeInfo && (
            <span className={`text-xs px-2 py-0.5 rounded-full border ${regimeInfo.color}`}>
              {regimeInfo.label}
            </span>
          )}
          <div className="flex items-center gap-2">
            {counts.map(({ action, count }) => (
              <span
                key={action}
                className={`text-xs font-semibold ${ACTION_CONFIG[action].text}`}
              >
                {count} {action}
              </span>
            ))}
          </div>
        </div>
      </div>

      {(["SELL", "BUY", "WATCH", "HOLD"] as Action[]).map(action =>
        groups[action].length > 0 ? (
          <RecommendationGroup
            key={action}
            action={action}
            items={groups[action]}
            narratives={narratives}
            narrativesLoading={narrativesLoading}
          />
        ) : null
      )}
    </div>
  );
}

export default memo(RecommendationsPanel);

const COMPACT_LIMIT = 4;

function RecommendationGroup({
  action,
  items,
  narratives,
  narrativesLoading,
}: {
  action: Action;
  items: Recommendation[];
  narratives?: Record<string, string | null>;
  narrativesLoading?: boolean;
}) {
  const cfg = ACTION_CONFIG[action];
  const [expanded, setExpanded] = useState(false);
  const showAll = expanded || items.length <= COMPACT_LIMIT;
  const visible = showAll ? items : items.slice(0, COMPACT_LIMIT);
  const hiddenCount = items.length - COMPACT_LIMIT;

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
        <p className={`text-xs font-semibold tracking-wide ${cfg.text}`}>{action}</p>
        <span className="text-xs text-slate-600">{items.length}</span>
        {items.length > COMPACT_LIMIT && (
          <button
            onClick={() => setExpanded(e => !e)}
            className="ml-auto text-[10px] text-slate-500 hover:text-slate-300 transition-colors"
          >
            {expanded ? "▲ collapse" : `▼ show all ${items.length}`}
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
        {visible.map(rec => (
          <RecCard
            key={rec.symbol}
            rec={rec}
            cfg={cfg}
            narrative={narratives?.[rec.symbol]}
            narrativeLoading={narrativesLoading && !narratives?.[rec.symbol]}
          />
        ))}
      </div>
      {!expanded && hiddenCount > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-2 w-full text-xs text-slate-500 hover:text-slate-300 transition-colors py-1.5 border border-slate-800 hover:border-slate-700 rounded-lg text-center"
        >
          +{hiddenCount} more {action.toLowerCase()}
        </button>
      )}
    </div>
  );
}

function RecCard({
  rec,
  cfg,
  narrative,
  narrativeLoading,
}: {
  rec: Recommendation;
  cfg: typeof ACTION_CONFIG["BUY"];
  narrative?: string | null;
  narrativeLoading?: boolean;
}) {
  const upside = rec.analyst_upside_pct;
  const scoreBar = Math.round((rec.score / 10) * 100);

  return (
    <div className={`${cfg.bg} border ${cfg.border} rounded-lg p-3 space-y-2`}>
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="font-mono text-sm font-semibold text-white leading-tight">
            {rec.symbol}
          </p>
          <p className="text-xs text-slate-500 truncate" title={rec.name}>
            {rec.name}
          </p>
        </div>
        <div className="text-right ml-2 shrink-0">
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${cfg.badge}`}>
            {rec.action}
          </span>
          <p className={`text-xs mt-0.5 font-medium ${cfg.text}`}>
            {rec.score}/10
          </p>
        </div>
      </div>

      {/* Score bar */}
      <div className="h-1 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            rec.action === "BUY"   ? "bg-emerald-500" :
            rec.action === "SELL"  ? "bg-rose-500" :
            rec.action === "WATCH" ? "bg-amber-500" : "bg-slate-500"
          }`}
          style={{ width: `${scoreBar}%` }}
        />
      </div>

      {/* AI narrative */}
      {narrativeLoading ? (
        <div className="h-8 bg-slate-800/60 rounded animate-pulse" />
      ) : narrative ? (
        <p className="text-[11px] text-slate-300 leading-relaxed border-l-2 border-indigo-500/40 pl-2 italic">
          {narrative}
        </p>
      ) : null}

      {/* Trade levels — stop / target / R:R / Kelly */}
      {(rec.stop_loss || rec.take_profit || rec.risk_reward) && (() => {
        const cur = rec.currency || (rec.symbol.endsWith(".TO") || rec.symbol.endsWith(".V") ? "CAD" : "USD");
        const isSell = rec.action === "SELL";
        const cp = rec.current_price;
        const stopPct = cp && rec.stop_loss
          ? isSell
            ? +((rec.stop_loss - cp) / cp * 100).toFixed(1)
            : +((cp - rec.stop_loss) / cp * 100).toFixed(1)
          : null;
        const tgtPct = cp && rec.take_profit
          ? isSell
            ? +((cp - rec.take_profit) / cp * 100).toFixed(1)
            : +((rec.take_profit - cp) / cp * 100).toFixed(1)
          : null;
        return (
          <div className="space-y-0.5 pt-0.5 border-t border-slate-800">
            <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px]">
              {rec.stop_loss && (
                <span className="text-rose-400">
                  {isSell ? "Exit if ↑" : "Stop ↓"}{" "}
                  <span className="font-mono">{cur} {rec.stop_loss.toFixed(2)}</span>
                  {stopPct !== null && <span className="text-rose-300/60 ml-1">({stopPct}% away)</span>}
                  {rec.stop_type && <span className="text-slate-500 ml-1">· {rec.stop_type}</span>}
                </span>
              )}
              {rec.take_profit && (
                <span className="text-emerald-400">
                  {isSell ? "Downside ↓" : "Target ↑"}{" "}
                  <span className="font-mono">{cur} {rec.take_profit.toFixed(2)}</span>
                  {tgtPct !== null && <span className="text-emerald-300/60 ml-1">(+{tgtPct}%)</span>}
                </span>
              )}
              {rec.risk_reward && (
                <span className={`${rec.risk_reward >= 2 ? "text-emerald-400" : rec.risk_reward >= 1 ? "text-amber-400" : "text-rose-400"}`}>
                  R:R <span className="font-semibold">{rec.risk_reward}:1</span>
                </span>
              )}
              {rec.kelly_pct != null && rec.action === "BUY" && (
                <span className="text-indigo-400">
                  Kelly <span className="font-semibold">{rec.kelly_pct}%</span> of position
                </span>
              )}
            </div>
            {cp && rec.action !== "HOLD" && (
              <p className="text-[10px] text-slate-400">
                {isSell
                  ? `Current ${cur} ${cp.toFixed(2)} — sell here, cover at target, stop if reclaims ${rec.stop_type ?? "stop"}`
                  : rec.entry_timing === "wait_pullback"
                    ? `Current ${cur} ${cp.toFixed(2)} — RSI extended, wait for dip toward stop zone before entry`
                    : `Current ${cur} ${cp.toFixed(2)} — entry zone acceptable, size via Kelly, stop at ${rec.stop_type ?? "stop"}`
                }
              </p>
            )}
          </div>
        );
      })()}

      {/* Hedge flag */}
      {rec.hedge_flag && rec.hedge_reason && (
        <div className="text-[10px] px-2 py-1 rounded font-medium bg-purple-500/10 text-purple-300 border border-purple-500/25">
          🛡 {rec.hedge_reason}
        </div>
      )}

      {/* Earnings risk */}
      {rec.earnings_risk && rec.days_to_earnings != null && (
        <div className={`text-[10px] px-2 py-1 rounded font-medium flex items-center gap-1.5 ${
          rec.earnings_risk === "imminent"
            ? "bg-rose-500/15 text-rose-400 border border-rose-500/25"
            : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
        }`}>
          <span>{rec.earnings_risk === "imminent" ? "🔴" : "🟡"}</span>
          <span>
            Earnings in {rec.days_to_earnings}d
            {rec.expected_move_pct != null && ` · ±${rec.expected_move_pct}% implied`}
          </span>
        </div>
      )}

      {/* EV + max loss preflight */}
      {(rec.ev_dollars != null || rec.max_loss_dollars != null) && rec.action !== "HOLD" && (() => {
        const cur = rec.currency || (rec.symbol.endsWith(".TO") || rec.symbol.endsWith(".V") ? "CAD" : "USD");
        return (
          <div className="flex items-center justify-between gap-2 text-xs">
            {rec.ev_dollars != null && (
              <span className={`font-semibold ${rec.ev_dollars >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                EV {rec.ev_dollars >= 0 ? "+" : ""}{cur} {rec.ev_dollars.toFixed(0)}
              </span>
            )}
            {rec.max_loss_dollars != null && (
              <span className="text-rose-300/80">
                Max loss <span className="font-mono">-{cur} {rec.max_loss_dollars.toFixed(0)}</span>
              </span>
            )}
          </div>
        );
      })()}

      {/* Analyst upside */}
      {upside !== null && upside !== undefined && (
        <p className={`text-xs font-medium ${upside >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
          {upside >= 0 ? "▲" : "▼"} {Math.abs(upside)}% analyst target
        </p>
      )}

      {/* Top rule-based reasons */}
      <ul className="space-y-1">
        {rec.reasons.slice(0, 3).map((reason, i) => (
          <li key={i} className="text-[11px] text-slate-400 leading-snug">
            · {reason}
          </li>
        ))}
      </ul>

      {/* Meta row: stage / RSI / weight / confidence */}
      <div className="flex items-center gap-2 pt-0.5 flex-wrap">
        {rec.stage !== null && rec.stage !== undefined && (
          <span className="text-[10px] text-slate-600 bg-slate-800 px-1.5 py-0.5 rounded">
            S{rec.stage}
          </span>
        )}
        {rec.rsi !== null && rec.rsi !== undefined && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
            rec.rsi > 70 ? "text-rose-400 bg-rose-500/10" :
            rec.rsi < 30 ? "text-emerald-400 bg-emerald-500/10" :
            "text-slate-600 bg-slate-800"
          }`}>
            RSI {rec.rsi.toFixed(0)}
          </span>
        )}
        {rec.weight > 0 && (
          <span className="text-[10px] text-slate-600">
            {rec.weight.toFixed(1)}%
          </span>
        )}
        {rec.confidence && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
            rec.confidence === "high"   ? "text-emerald-400 bg-emerald-500/10" :
            rec.confidence === "low"    ? "text-rose-400 bg-rose-500/10" :
                                          "text-amber-400 bg-amber-500/10"
          }`}>
            {rec.confidence} conf
          </span>
        )}
        {rec.llm_demoted && (
          <span className="text-[10px] text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded">
            AI→WATCH
          </span>
        )}
      </div>
    </div>
  );
}
