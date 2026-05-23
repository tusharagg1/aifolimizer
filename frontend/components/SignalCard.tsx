"use client";

import { memo } from "react";
import type { IntegratedSignal } from "@/lib/api";

type Action = "BUY" | "HOLD" | "WATCH" | "SELL" | "ADD" | "TRIM" | "NO_EDGE";

const ACTION_BADGE: Record<string, string> = {
  BUY: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  ADD: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  HOLD: "bg-slate-700 text-slate-300 border-slate-600",
  WATCH: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  TRIM: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  SELL: "bg-rose-500/20 text-rose-400 border-rose-500/30",
  NO_EDGE: "bg-slate-800 text-slate-400 border-slate-700",
};

function actionBadge(action: string): string {
  return (
    ACTION_BADGE[action.toUpperCase() as Action] ?? ACTION_BADGE.NO_EDGE
  );
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

interface BarProps {
  label: string;
  value: number | null;
  /** Visual mapping range — used to normalize value into [-1, +1]. */
  scale: number;
}

function SubSignalBar({ label, value, scale }: BarProps) {
  const v = value ?? 0;
  const norm = clamp(v / scale, -1, 1); // -1..+1
  const widthPct = Math.abs(norm) * 50;
  const positive = norm >= 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-16 text-slate-400">{label}</span>
      <div className="relative flex-1 h-2 rounded bg-slate-800">
        <div className="absolute left-1/2 top-0 h-full w-px bg-slate-600" />
        <div
          className={`absolute top-0 h-full rounded ${
            positive ? "bg-emerald-500/60" : "bg-rose-500/60"
          }`}
          style={{
            left: positive ? "50%" : `${50 - widthPct}%`,
            width: `${widthPct}%`,
          }}
        />
      </div>
      <span className="w-10 text-right text-slate-500 tabular-nums">
        {value !== null ? value.toFixed(1) : "—"}
      </span>
    </div>
  );
}

interface SkillEvidenceChipsProps {
  evidence: Record<string, number> | null;
}

function SkillEvidenceChips({ evidence }: SkillEvidenceChipsProps) {
  if (!evidence) return null;
  const entries = Object.entries(evidence).filter(([, v]) => v !== 0);
  if (entries.length === 0) {
    return (
      <p className="text-xs text-slate-500">No skill votes for this symbol.</p>
    );
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([key, val]) => (
        <span
          key={key}
          className={`px-2 py-0.5 rounded text-xs border ${
            val > 0
              ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
              : "bg-rose-500/10 text-rose-300 border-rose-500/20"
          }`}
          title={`${key}: ${val > 0 ? "+1" : "-1"}`}
        >
          {key.replace(/_/g, " ")} {val > 0 ? "↑" : "↓"}
        </span>
      ))}
    </div>
  );
}

interface SignalCardProps {
  signal: IntegratedSignal;
}

function SignalCardImpl({ signal }: SignalCardProps) {
  const score = signal.score ?? 0;
  const skillConsensus = signal.sub_signals.skill_consensus ?? 0;
  const skillConfidence = signal.sub_signals.skill_confidence ?? 0;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-100">{signal.symbol}</h3>
          <p className="text-xs text-slate-500">
            {signal.ts ? new Date(signal.ts).toLocaleString() : "—"}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span
            className={`px-2 py-1 rounded text-sm font-bold border ${actionBadge(
              signal.action,
            )}`}
          >
            {signal.action}
            {signal.conviction && (
              <span className="ml-1 text-xs font-normal opacity-75">
                ({signal.conviction.toUpperCase()})
              </span>
            )}
          </span>
          <span className="text-2xl font-bold text-slate-200 tabular-nums">
            {score.toFixed(1)}
            <span className="text-sm text-slate-500"> /10</span>
          </span>
        </div>
      </div>

      <div className="space-y-1.5">
        <SubSignalBar label="Tech" value={signal.sub_signals.tech} scale={4} />
        <SubSignalBar label="Fund" value={signal.sub_signals.fund} scale={3} />
        <SubSignalBar label="Macro" value={signal.sub_signals.macro} scale={2} />
        <SubSignalBar
          label="Sent"
          value={signal.sub_signals.sentiment}
          scale={1}
        />
        <SubSignalBar
          label={`Skill (${(skillConfidence * 100).toFixed(0)}%)`}
          value={skillConsensus}
          scale={4}
        />
      </div>

      {(signal.kelly_pct ?? 0) > 0 && (
        <div className="flex items-center gap-3 text-xs">
          <span className="text-slate-500">Size:</span>
          <span className="px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
            Kelly {signal.kelly_pct?.toFixed(1)}%
          </span>
          {signal.win_prob !== null && (
            <span className="text-slate-400">
              p(win) {(signal.win_prob * 100).toFixed(0)}%
            </span>
          )}
          {signal.risk_reward !== null && (
            <span className="text-slate-400">
              R:R {signal.risk_reward?.toFixed(1)}
            </span>
          )}
        </div>
      )}

      <div>
        <p className="text-xs text-slate-500 mb-1">Skill evidence</p>
        <SkillEvidenceChips evidence={signal.skill_evidence} />
      </div>
    </div>
  );
}

export const SignalCard = memo(SignalCardImpl);
SignalCard.displayName = "SignalCard";
