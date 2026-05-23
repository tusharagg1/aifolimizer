"use client";

import { memo } from "react";
import type { DiscoveryPick } from "@/lib/api";

interface DiscoveryCardProps {
  pick: DiscoveryPick;
}

function actionBadgeClass(action: string): string {
  switch (action.toUpperCase()) {
    case "BUY":
    case "ADD":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
    case "WATCH":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40";
    default:
      return "bg-slate-700 text-slate-300 border-slate-600";
  }
}

function DiscoveryCard({ pick }: DiscoveryCardProps) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-lg font-semibold text-slate-100">
            {pick.symbol}
          </h4>
          {pick.sector && (
            <p className="text-xs text-slate-500">{pick.sector}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          <span
            className={`px-2 py-0.5 rounded text-sm font-bold border ${actionBadgeClass(pick.action)}`}
          >
            {pick.action}
            {pick.conviction && (
              <span className="ml-1 text-xs opacity-75 font-normal">
                ({pick.conviction.toUpperCase()})
              </span>
            )}
          </span>
          <span className="text-2xl font-bold text-slate-200 tabular-nums">
            {pick.score.toFixed(1)}
            <span className="text-sm text-slate-500"> /10</span>
          </span>
        </div>
      </div>

      {(pick.current_price || pick.stop_loss || pick.take_profit) && (
        <div className="grid grid-cols-3 gap-2 text-xs">
          {pick.current_price !== null && (
            <div>
              <p className="text-slate-500">Entry</p>
              <p className="text-slate-200 tabular-nums">
                ${pick.current_price.toFixed(2)}
              </p>
            </div>
          )}
          {pick.stop_loss !== null && (
            <div>
              <p className="text-slate-500">Stop</p>
              <p className="text-rose-300 tabular-nums">
                ${pick.stop_loss.toFixed(2)}
              </p>
            </div>
          )}
          {pick.take_profit !== null && (
            <div>
              <p className="text-slate-500">Target</p>
              <p className="text-emerald-300 tabular-nums">
                ${pick.take_profit.toFixed(2)}
              </p>
            </div>
          )}
        </div>
      )}

      {(pick.kelly_pct || pick.risk_reward) && (
        <div className="flex items-center gap-2 text-xs">
          {(pick.kelly_pct ?? 0) > 0 && (
            <span className="px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
              Kelly {pick.kelly_pct?.toFixed(1)}%
            </span>
          )}
          {pick.risk_reward !== null && (
            <span className="text-slate-400">
              R:R {pick.risk_reward.toFixed(1)}
            </span>
          )}
          {pick.win_prob !== null && (
            <span className="text-slate-400">
              p(win) {(pick.win_prob * 100).toFixed(0)}%
            </span>
          )}
        </div>
      )}

      {pick.reasons.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-400">
          {pick.reasons.map((r, i) => (
            <li key={i}>· {r}</li>
          ))}
        </ul>
      )}

      {pick.warning && (
        <p className="text-xs text-amber-400">⚠ {pick.warning}</p>
      )}
    </div>
  );
}

interface DiscoveryPanelProps {
  picks: DiscoveryPick[];
}

function DiscoveryPanelImpl({ picks }: DiscoveryPanelProps) {
  if (!picks || picks.length === 0) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6 text-center text-sm text-slate-500">
        No discovery picks yet. Nightly scan runs after market close.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <h3 className="text-lg font-semibold text-slate-100">
        Discovery: top {picks.length}
        <span className="ml-2 text-xs font-normal text-slate-500">
          S&amp;P 500 + TSX 60 + watchlist · ranked by integrated 5-signal score
        </span>
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {picks.map((p) => (
          <DiscoveryCard key={p.symbol} pick={p} />
        ))}
      </div>
    </div>
  );
}

export const DiscoveryPanel = memo(DiscoveryPanelImpl);
DiscoveryPanel.displayName = "DiscoveryPanel";
