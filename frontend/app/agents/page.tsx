"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  listAgents,
  getAgentDetail,
  runAgent,
  setAgentEnabled,
  snoozeAgent,
  unsnoozeAgent,
  getAgentsSummary,
  agentsWhoami,
  type AgentInfo,
  type AgentDetail,
  type AgentsSummary,
} from "@/lib/api";

const TRIGGER_CFG: Record<string, { bg: string; text: string; label: string }> = {
  cron: { bg: "bg-blue-900/40", text: "text-blue-300", label: "scheduled" },
  event: { bg: "bg-purple-900/40", text: "text-purple-300", label: "event" },
  manual: { bg: "bg-slate-800/40", text: "text-slate-400", label: "manual" },
};

const CATEGORY_CFG: Record<string, { color: string; label: string }> = {
  behavioral: { color: "border-rose-600/40", label: "Behavioral" },
  trading: { color: "border-amber-600/40", label: "Trading" },
  portfolio: { color: "border-emerald-600/40", label: "Portfolio" },
  research: { color: "border-indigo-600/40", label: "Research" },
  general: { color: "border-slate-600/40", label: "General" },
};

function fmtRelTime(ts: number | null): string {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function fmtSchedule(cron: string | null): string {
  if (!cron) return "—";
  const desc: Record<string, string> = {
    "0 11 * * 1-5": "weekdays 7am ET",
    "0 23 * * 0": "Sunday 7pm ET",
    "0 13 1 * *": "1st of month",
    "0 13 1,15 * *": "1st + 15th",
    "0 13 1 1,4,7,10 *": "quarterly",
    "0 13 15 11,12 *": "Nov 15 + Dec 15",
    "0 22 * * 5": "Friday 6pm ET",
    "0 4 * * *": "nightly 4am",
    "0 3 * * *": "nightly 3am",
  };
  return desc[cron] ?? cron;
}

function StatusPill({ enabled, snoozedUntil, lastStatus }: { enabled: boolean; snoozedUntil: number | null; lastStatus: string | null }) {
  if (!enabled) return <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">disabled</span>;
  if (snoozedUntil && snoozedUntil > Date.now() / 1000) {
    return <span className="text-xs px-2 py-0.5 rounded bg-amber-900/50 text-amber-300">snoozed</span>;
  }
  if (lastStatus === "error") return <span className="text-xs px-2 py-0.5 rounded bg-rose-900/50 text-rose-300">last: error</span>;
  if (lastStatus === "ok") return <span className="text-xs px-2 py-0.5 rounded bg-emerald-900/50 text-emerald-300">active</span>;
  return <span className="text-xs px-2 py-0.5 rounded bg-blue-900/50 text-blue-300">active</span>;
}

function AgentCard({ a, onClick }: { a: AgentInfo; onClick: () => void }) {
  const catCfg = CATEGORY_CFG[a.category] ?? CATEGORY_CFG.general;
  const triggerCfg = TRIGGER_CFG[a.trigger];
  return (
    <button
      onClick={onClick}
      className={`text-left rounded-lg border ${catCfg.color} bg-slate-900/40 hover:bg-slate-800/60 transition p-4 flex flex-col gap-2 min-h-[140px]`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-mono text-sm text-slate-100 leading-tight">{a.name}</div>
        <StatusPill enabled={a.enabled} snoozedUntil={a.snoozed_until_ts} lastStatus={a.last_run_status} />
      </div>
      <div className="text-xs text-slate-400 leading-snug line-clamp-2">{a.description}</div>
      {a.last_snapshot?.summary && (() => {
        const sm = a.last_snapshot.summary as Record<string, unknown>;
        const v = (sm.verdict ?? sm.action ?? sm.recommendation ?? sm.health
          ?? sm.risk_level ?? sm.regime ?? "") as string;
        if (!v) return null;
        const c = verdictColor(String(v));
        return (
          <div className={`inline-flex self-start text-xs font-mono px-2 py-0.5 rounded ${c.bg} ${c.text}`}>
            {String(v).toUpperCase()}
          </div>
        );
      })()}
      <div className="mt-auto flex items-center gap-2 text-xs">
        <span className={`px-1.5 py-0.5 rounded ${triggerCfg.bg} ${triggerCfg.text}`}>
          {triggerCfg.label}
        </span>
        <span className="text-slate-500">
          {a.trigger === "cron" ? fmtSchedule(a.schedule) : a.event_types.slice(0, 1).join(", ") || "—"}
        </span>
        <span className="ml-auto text-slate-500">last: {fmtRelTime(a.last_run_ts)}</span>
      </div>
    </button>
  );
}

function SummaryStrip({ s }: { s: AgentsSummary | null }) {
  if (!s) return null;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
      <div className="rounded-lg bg-slate-900/50 border border-slate-800 p-3">
        <div className="text-xs text-slate-400">Agents</div>
        <div className="text-2xl font-semibold text-slate-100">{s.total}</div>
      </div>
      <div className="rounded-lg bg-slate-900/50 border border-slate-800 p-3">
        <div className="text-xs text-slate-400">Enabled</div>
        <div className="text-2xl font-semibold text-emerald-300">{s.enabled}</div>
      </div>
      <div className="rounded-lg bg-slate-900/50 border border-slate-800 p-3">
        <div className="text-xs text-slate-400">Snoozed</div>
        <div className="text-2xl font-semibold text-amber-300">{s.snoozed}</div>
      </div>
      <div className="rounded-lg bg-slate-900/50 border border-slate-800 p-3">
        <div className="text-xs text-slate-400">Ran 24h</div>
        <div className="text-2xl font-semibold text-blue-300">{s.ran_last_24h}</div>
      </div>
    </div>
  );
}

function DrillPanel({
  name,
  sessionId,
  onClose,
  onChanged,
}: {
  name: string;
  sessionId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ticker, setTicker] = useState<string>("");

  const load = useCallback(async () => {
    setErr(null);
    try {
      setDetail(await getAgentDetail(name, sessionId));
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [name, sessionId]);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!detail) {
    return (
      <div className="fixed inset-y-0 right-0 w-full sm:w-[480px] bg-slate-950 border-l border-slate-800 p-6 z-50 overflow-y-auto">
        <button onClick={onClose} className="text-slate-400 hover:text-slate-100 mb-4">← close</button>
        <div className="text-slate-400">Loading {name}…</div>
        {err && <div className="text-rose-400 mt-2 text-sm">{err}</div>}
      </div>
    );
  }

  const snoozedActive = detail.snoozed_until_ts && detail.snoozed_until_ts > Date.now() / 1000;
  const snap = detail.last_snapshot as Record<string, unknown> | undefined;
  const TICKER_AGENTS = new Set([
    "adversarial-research",
    "earnings-postmortem",
    "earnings-analyzer",
    "stock-analysis",
    "stock-compare",
  ]);
  const needsTicker = TICKER_AGENTS.has(detail.name);
  const runContext = needsTicker ? { ticker: ticker.trim().toUpperCase() } : {};
  const runDisabled =
    busy || !detail.runner_available || (needsTicker && !ticker.trim());

  return (
    <div className="fixed inset-y-0 right-0 w-full sm:w-[520px] bg-slate-950 border-l border-slate-800 p-6 z-50 overflow-y-auto">
      <div className="flex items-center justify-between mb-4">
        <button onClick={onClose} className="text-slate-400 hover:text-slate-100">← close</button>
        <StatusPill enabled={detail.enabled} snoozedUntil={detail.snoozed_until_ts} lastStatus={detail.last_run_status} />
      </div>

      <h2 className="font-mono text-lg text-slate-100 mb-1">{detail.name}</h2>
      <p className="text-sm text-slate-400 mb-4">{detail.description}</p>

      <div className="grid grid-cols-2 gap-3 text-xs mb-5">
        <div>
          <div className="text-slate-500">Trigger</div>
          <div className="text-slate-200">{detail.trigger}</div>
        </div>
        <div>
          <div className="text-slate-500">Category</div>
          <div className="text-slate-200">{CATEGORY_CFG[detail.category]?.label ?? detail.category}</div>
        </div>
        <div>
          <div className="text-slate-500">Schedule</div>
          <div className="text-slate-200">{fmtSchedule(detail.schedule)}</div>
        </div>
        <div>
          <div className="text-slate-500">Events</div>
          <div className="text-slate-200">{detail.event_types.join(", ") || "—"}</div>
        </div>
        <div>
          <div className="text-slate-500">Model</div>
          <div className="text-slate-200">{detail.model_pref}</div>
        </div>
        <div>
          <div className="text-slate-500">Auto-execute</div>
          <div className="text-slate-200">{detail.auto_execute ? "yes" : "propose-only"}</div>
        </div>
        <div>
          <div className="text-slate-500">Backend runner</div>
          <div className={detail.runner_available ? "text-emerald-300" : "text-rose-300"}>
            {detail.runner_available ? "available" : "missing"}
          </div>
        </div>
        <div>
          <div className="text-slate-500">Manual runs</div>
          <div className="text-slate-200">{detail.manual_runs_count}</div>
        </div>
      </div>

      {needsTicker && (
        <div className="mb-3">
          <label className="block text-xs text-slate-500 mb-1">
            Ticker (required)
          </label>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="AAPL"
            className="w-full px-3 py-1.5 text-sm rounded bg-slate-900 border border-slate-700 text-slate-100 placeholder-slate-600 focus:outline-none focus:border-blue-600"
          />
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-5">
        <button
          disabled={runDisabled}
          onClick={() => act(() => runAgent(detail.name, sessionId, runContext))}
          className="px-3 py-1.5 text-sm rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-white"
        >
          Run now
        </button>
        <button
          disabled={busy}
          onClick={() => act(() => setAgentEnabled(detail.name, !detail.enabled))}
          className="px-3 py-1.5 text-sm rounded bg-slate-700 hover:bg-slate-600 text-slate-100"
        >
          {detail.enabled ? "Disable" : "Enable"}
        </button>
        {snoozedActive ? (
          <button
            disabled={busy}
            onClick={() => act(() => unsnoozeAgent(detail.name))}
            className="px-3 py-1.5 text-sm rounded bg-amber-800 hover:bg-amber-700 text-white"
          >
            Unsnooze
          </button>
        ) : (
          <button
            disabled={busy}
            onClick={() => act(() => snoozeAgent(detail.name, 60))}
            className="px-3 py-1.5 text-sm rounded bg-amber-900/70 hover:bg-amber-800 text-amber-100"
          >
            Snooze 1h
          </button>
        )}
      </div>

      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}

      <div className="border-t border-slate-800 pt-4">
        <div className="text-xs uppercase text-slate-500 mb-2">Last snapshot</div>
        {snap ? <SnapshotView snap={snap} /> : (
          <div className="text-sm text-slate-500">No snapshot yet — run the agent to populate.</div>
        )}
      </div>
    </div>
  );
}

function verdictColor(v: string): { bg: string; text: string; border: string } {
  const up = v.toUpperCase();
  if (["BUY", "ADD", "PASS", "CONTINUE", "STRONG", "HEALTHY", "OK"].some(k => up.includes(k)))
    return { bg: "bg-emerald-900/40", text: "text-emerald-300", border: "border-emerald-600/40" };
  if (["SELL", "EXIT", "REJECT", "SUSPEND", "UNHEALTHY", "HIGH"].some(k => up.includes(k)))
    return { bg: "bg-rose-900/40", text: "text-rose-300", border: "border-rose-600/40" };
  if (["TRIM", "COOL", "WARN", "ATTENTION", "ELEVATED", "MISS"].some(k => up.includes(k)))
    return { bg: "bg-amber-900/40", text: "text-amber-300", border: "border-amber-600/40" };
  return { bg: "bg-slate-800/40", text: "text-slate-300", border: "border-slate-600/40" };
}

function SnapshotView({ snap }: { snap: Record<string, unknown> }) {
  const [rawOpen, setRawOpen] = useState(false);
  const summary = (snap.summary ?? {}) as Record<string, unknown>;
  const actionable = (snap.actionable ?? []) as Array<Record<string, unknown>>;
  const alerts = (snap.alerts ?? []) as Array<Record<string, unknown>>;
  const insights = (snap.key_insights ?? []) as string[];
  const error = snap.error as string | null | undefined;
  const computedAt = snap.computed_at as string | undefined;

  const verdictRaw = (summary.verdict ?? summary.action ?? summary.recommendation
    ?? summary.health ?? summary.risk_level ?? summary.regime ?? "") as string;
  const verdict = String(verdictRaw || "").trim();
  const vc = verdict ? verdictColor(verdict) : null;
  const reason = (summary.reason ?? summary.thesis ?? summary.headline ?? "") as string;

  const detailFields = Object.entries(summary).filter(
    ([k, v]) => !["verdict", "action", "recommendation", "health", "risk_level",
                  "regime", "reason", "thesis", "headline"].includes(k)
                && v !== null && v !== undefined
                && !(typeof v === "object" && Object.keys(v as object).length === 0),
  );

  return (
    <div className="space-y-4">
      {/* Verdict + reason */}
      {verdict && vc && (
        <div className={`rounded-lg border ${vc.border} ${vc.bg} p-4`}>
          <div className={`text-2xl font-bold tracking-wide ${vc.text}`}>
            {verdict.toUpperCase()}
          </div>
          {reason && <div className="text-sm text-slate-300 mt-1">{String(reason)}</div>}
        </div>
      )}

      {/* Error if any */}
      {error && (
        <div className="rounded border border-rose-700/40 bg-rose-900/20 text-rose-300 text-sm p-3">
          <div className="font-semibold mb-1">Error</div>
          <div>{String(error)}</div>
        </div>
      )}

      {/* Alerts */}
      {alerts.length > 0 && (
        <div>
          <div className="text-xs uppercase text-slate-500 mb-1">Alerts</div>
          <div className="space-y-1">
            {alerts.map((a, i) => {
              const lvl = String(a.level ?? "info");
              const cls = lvl === "warn" || lvl === "error"
                ? "border-rose-700/40 bg-rose-900/20 text-rose-300"
                : "border-blue-700/40 bg-blue-900/20 text-blue-300";
              return (
                <div key={i} className={`text-sm rounded border ${cls} px-3 py-1.5`}>
                  {String(a.message ?? a.symbol ?? JSON.stringify(a))}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Actionable */}
      {actionable.length > 0 && (
        <div>
          <div className="text-xs uppercase text-slate-500 mb-1">Actionable</div>
          <ul className="space-y-1.5">
            {actionable.map((a, i) => (
              <li key={i} className="text-sm rounded bg-slate-900 border border-slate-800 px-3 py-2">
                <div className="flex flex-wrap gap-2 items-baseline">
                  {Boolean(a.recommendation) && (
                    <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${verdictColor(String(a.recommendation)).bg} ${verdictColor(String(a.recommendation)).text}`}>
                      {String(a.recommendation).toUpperCase()}
                    </span>
                  )}
                  {Boolean(a.ticker) && (
                    <span className="text-xs font-mono text-slate-200">{String(a.ticker)}</span>
                  )}
                  {Boolean(a.shares) && (
                    <span className="text-xs text-slate-400">{String(a.shares)} sh</span>
                  )}
                  {Boolean(a.cost) && (
                    <span className="text-xs text-slate-400">${String(a.cost)}</span>
                  )}
                </div>
                {Boolean(a.reason) && (
                  <div className="text-xs text-slate-400 mt-1">{String(a.reason)}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Key insights */}
      {insights.length > 0 && (
        <div>
          <div className="text-xs uppercase text-slate-500 mb-1">Insights</div>
          <ul className="text-sm text-slate-300 space-y-1 list-disc list-inside">
            {insights.map((s, i) => <li key={i}>{String(s)}</li>)}
          </ul>
        </div>
      )}

      {/* Other summary fields */}
      {detailFields.length > 0 && (
        <div>
          <div className="text-xs uppercase text-slate-500 mb-1">Detail</div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            {detailFields.map(([k, v]) => (
              <div key={k} className="bg-slate-900 border border-slate-800 rounded px-2 py-1.5">
                <div className="text-slate-500">{k}</div>
                <div className="text-slate-200 font-mono break-words">
                  {typeof v === "object" ? JSON.stringify(v) : String(v)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="text-xs text-slate-500">
        Computed: {computedAt ? new Date(computedAt).toLocaleString() : "—"}
      </div>

      {/* Raw JSON collapsible */}
      <details
        open={rawOpen}
        onToggle={(e) => setRawOpen((e.target as HTMLDetailsElement).open)}
        className="border-t border-slate-800 pt-3"
      >
        <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-300">
          Raw JSON
        </summary>
        <pre className="text-xs bg-slate-900 border border-slate-800 rounded p-3 text-slate-400 overflow-auto max-h-[300px] mt-2">
          {JSON.stringify(snap, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [summary, setSummary] = useState<AgentsSummary | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");

  const load = useCallback(async (sid: string | null) => {
    setErr(null);
    try {
      const [a, s] = await Promise.all([
        listAgents(sid ?? undefined),
        getAgentsSummary(sid ?? undefined),
      ]);
      setAgents(a.agents);
      setSummary(s);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const r = await agentsWhoami();
        if (r.authenticated && r.session_id) {
          setSessionId(r.session_id);
          await load(r.session_id);
        } else {
          await load(null);
        }
      } catch {
        await load(null);
      }
    })();
  }, [load]);

  // Poll every 30s for fresh state
  useEffect(() => {
    const id = setInterval(() => load(sessionId), 30_000);
    return () => clearInterval(id);
  }, [load, sessionId]);

  const filtered = filter === "all"
    ? agents
    : agents.filter((a) => a.category === filter || a.trigger === filter);

  const categories = Array.from(new Set(agents.map((a) => a.category)));
  const triggers = Array.from(new Set(agents.map((a) => a.trigger)));

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 px-4 py-8 sm:px-8">
      <div className="max-w-6xl mx-auto">
        <header className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold">Agents</h1>
            <p className="text-sm text-slate-400">Autonomous skill runtime — self-firing on cron + events</p>
          </div>
          <Link href="/dashboard" className="text-sm text-slate-400 hover:text-slate-100">
            ← Dashboard
          </Link>
        </header>

        {!sessionId && (
          <div className="rounded border border-amber-700/40 bg-amber-900/20 text-amber-200 text-sm p-3 mb-4">
            No session. <Link href="/login" className="underline">Log in</Link> to enable Run/Snapshot features. Read-only registry shown.
          </div>
        )}

        <SummaryStrip s={summary} />

        <div className="flex flex-wrap gap-2 mb-4">
          <button
            onClick={() => setFilter("all")}
            className={`text-xs px-2.5 py-1 rounded ${filter === "all" ? "bg-slate-700 text-white" : "bg-slate-900 text-slate-400 hover:bg-slate-800"}`}
          >
            All ({agents.length})
          </button>
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setFilter(c)}
              className={`text-xs px-2.5 py-1 rounded ${filter === c ? "bg-slate-700 text-white" : "bg-slate-900 text-slate-400 hover:bg-slate-800"}`}
            >
              {CATEGORY_CFG[c]?.label ?? c}
            </button>
          ))}
          {triggers.map((t) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={`text-xs px-2.5 py-1 rounded ${filter === t ? "bg-slate-700 text-white" : "bg-slate-900 text-slate-400 hover:bg-slate-800"}`}
            >
              {TRIGGER_CFG[t]?.label ?? t}
            </button>
          ))}
        </div>

        {err && <div className="text-rose-400 text-sm mb-4">{err}</div>}

        {loading ? (
          <div className="text-slate-400">Loading agents…</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {filtered.map((a) => (
              <AgentCard key={a.name} a={a} onClick={() => setSelected(a.name)} />
            ))}
          </div>
        )}
      </div>

      {selected && sessionId && (
        <DrillPanel
          name={selected}
          sessionId={sessionId}
          onClose={() => setSelected(null)}
          onChanged={() => load(sessionId)}
        />
      )}
      {selected && !sessionId && (
        <div className="fixed inset-y-0 right-0 w-full sm:w-[420px] bg-slate-950 border-l border-slate-800 p-6 z-50">
          <button onClick={() => setSelected(null)} className="text-slate-400 hover:text-slate-100 mb-4">← close</button>
          <div className="text-sm text-slate-400">Log in to view agent details and snapshots.</div>
        </div>
      )}
    </main>
  );
}
