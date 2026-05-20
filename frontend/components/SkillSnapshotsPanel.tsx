"use client";

import { memo, useMemo, useState } from "react";
import { SkillSnapshot } from "@/lib/api";
import { useSkillSnapshots } from "@/hooks/useSkillSnapshots";

const STATUS_CONFIG: Record<string, { dot: string; label: string }> = {
  ok: { dot: "bg-emerald-500", label: "OK" },
  error: { dot: "bg-rose-500", label: "Error" },
};

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

interface RowProps {
  snap: SkillSnapshot;
  expanded: boolean;
  onToggle: () => void;
  onRefresh: () => void;
}

const SkillRow = memo(function SkillRow({ snap, expanded, onToggle, onRefresh }: RowProps) {
  const status = STATUS_CONFIG[snap.status] ?? { dot: "bg-amber-500", label: snap.status };
  const fresh = snap.fresh;
  const alertCount = snap.alerts?.length ?? 0;
  const actionableCount = snap.actionable?.length ?? 0;

  return (
    <div className="border border-slate-800/60 bg-slate-900/30 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-slate-800/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className={`h-2 w-2 rounded-full ${status.dot}`} />
          <span className="text-sm font-medium text-slate-200">{snap.skill}</span>
          {snap.confidence_source === "experimental" && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
              experimental
            </span>
          )}
          {!fresh && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
              stale
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-400">
          {alertCount > 0 && (
            <span className="text-amber-400">{alertCount} alert{alertCount === 1 ? "" : "s"}</span>
          )}
          {actionableCount > 0 && (
            <span>{actionableCount} item{actionableCount === 1 ? "" : "s"}</span>
          )}
          <span>{formatRelative(snap.computed_at)}</span>
          <span className="text-slate-500">{expanded ? "▾" : "▸"}</span>
        </div>
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-2 border-t border-slate-800/60 text-xs space-y-3">
          {snap.error && (
            <div className="text-rose-400">Error: {snap.error}</div>
          )}
          {snap.alerts?.length > 0 && (
            <div>
              <div className="text-slate-400 uppercase tracking-wide text-[10px] mb-1">Alerts</div>
              <ul className="space-y-1">
                {snap.alerts.slice(0, 5).map((a, i) => (
                  <li key={i} className="text-amber-300">
                    · {typeof a.message === "string" ? a.message : JSON.stringify(a)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {snap.summary && Object.keys(snap.summary).length > 0 && (
            <div>
              <div className="text-slate-400 uppercase tracking-wide text-[10px] mb-1">Summary</div>
              <pre className="bg-slate-950/60 p-2 rounded text-slate-300 overflow-x-auto max-h-64">
                {JSON.stringify(snap.summary, null, 2)}
              </pre>
            </div>
          )}
          <div className="flex justify-end">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRefresh();
              }}
              className="text-xs px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200"
            >
              Refresh now
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

function SkillSnapshotsPanel() {
  const { snapshots, scheduler, loading, error, refresh } = useSkillSnapshots();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const sorted = useMemo(() => {
    const order = [
      "daily-briefing", "portfolio-health", "stock-analysis",
      "cash-deployment", "risk-assessment", "macro-impact",
      "sector-rotation", "earnings-analyzer", "dividend-strategy",
      "tax-loss-review",
    ];
    return [...snapshots].sort((a, b) => {
      const ia = order.indexOf(a.skill);
      const ib = order.indexOf(b.skill);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
  }, [snapshots]);

  const toggle = (skill: string) => {
    const next = new Set(expanded);
    if (next.has(skill)) next.delete(skill);
    else next.add(skill);
    setExpanded(next);
  };

  return (
    <div className="border border-slate-800/60 bg-slate-900/20 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Codified Skill Snapshots</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Background-computed every {scheduler?.is_market_hours ? "15 min (market hours)" : "60 min (off-hours)"}.
            {scheduler?.last_run_ts && (
              <> Last tick: {formatRelative(new Date(scheduler.last_run_ts * 1000).toISOString())}.</>
            )}
          </p>
        </div>
        <button
          onClick={() => refresh()}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 disabled:opacity-50"
        >
          {loading ? "Refreshing…" : "Refresh all"}
        </button>
      </div>

      {error && (
        <div className="mb-3 text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded px-3 py-2">
          {error}
        </div>
      )}

      {sorted.length === 0 ? (
        <div className="text-xs text-slate-500 py-6 text-center">
          No snapshots yet. Scheduler runs after first login.
        </div>
      ) : (
        <div className="space-y-2">
          {sorted.map((snap) => (
            <SkillRow
              key={snap.skill}
              snap={snap}
              expanded={expanded.has(snap.skill)}
              onToggle={() => toggle(snap.skill)}
              onRefresh={() => refresh(snap.skill)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default memo(SkillSnapshotsPanel);
