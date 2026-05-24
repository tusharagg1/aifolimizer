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

function fmt(n: unknown, suffix = ""): string {
  if (typeof n !== "number") return "—";
  const sign = suffix === "%" && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}${suffix}`;
}

function fmtCurrency(n: unknown): string {
  if (typeof n !== "number") return "—";
  return `$${n.toLocaleString("en-CA", { maximumFractionDigits: 0 })}`;
}

// ── Skill-specific body renderers ─────────────────────────────────────────────

function DailyBriefingBody({ summary }: { summary: Record<string, unknown> }) {
  const nextAction = summary.next_action as string | null;
  return (
    <div className="space-y-2">
      {nextAction && (
        <div className="px-3 py-2 rounded bg-emerald-500/10 border border-emerald-500/30">
          <div className="text-[10px] uppercase tracking-wide text-emerald-300 mb-1">Next action</div>
          <div className="text-sm text-emerald-100">{nextAction}</div>
        </div>
      )}
    </div>
  );
}

function CashDeploymentBody({ summary }: { summary: Record<string, unknown> }) {
  const cash = summary.cash_available_cad as number | undefined;
  const remaining = summary.cash_remaining_after_plan_cad as number | undefined;
  const cands = (summary.candidates as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-3">
      <div className="flex gap-4 text-xs">
        <div>
          <div className="text-slate-500">Cash available</div>
          <div className="text-slate-100 font-medium">{fmtCurrency(cash)}</div>
        </div>
        <div>
          <div className="text-slate-500">After plan</div>
          <div className="text-slate-100 font-medium">{fmtCurrency(remaining)}</div>
        </div>
      </div>
      {cands.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-slate-500 text-[10px] uppercase tracking-wide">
            <tr>
              <th className="text-left">Symbol</th>
              <th className="text-right">Allocation</th>
              <th className="text-right">Kelly%</th>
              <th className="text-right">Score</th>
              <th className="text-right">R:R</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/40">
            {cands.map((c, i) => (
              <tr key={i}>
                <td className="py-1 text-slate-200 font-medium">{String(c.symbol)}</td>
                <td className="text-right text-emerald-400">{fmtCurrency(c.allocation_cad)}</td>
                <td className="text-right text-slate-300">{fmt(c.kelly_pct, "%")}</td>
                <td className="text-right text-slate-300">{fmt(c.score)}</td>
                <td className="text-right text-slate-300">{fmt(c.risk_reward)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RiskAssessmentBody({ summary }: { summary: Record<string, unknown> }) {
  const fields: Array<[string, unknown, string]> = [
    ["Sharpe", summary.sharpe, ""],
    ["Sortino", summary.sortino, ""],
    ["Ann. Vol", summary.annualized_volatility_pct, "%"],
    ["Max DD", summary.max_drawdown_pct, "%"],
    ["VaR 95%", summary.var_95_pct, "%"],
    ["ES 95%", summary.expected_shortfall_95_pct, "%"],
  ];
  return (
    <div className="grid grid-cols-3 gap-2">
      {fields.map(([label, val, suf]) => (
        <div key={label} className="border border-slate-800/60 rounded p-2">
          <div className="text-[10px] text-slate-500 uppercase tracking-wide">{label}</div>
          <div className="text-sm text-slate-100 font-medium">{fmt(val, suf)}</div>
        </div>
      ))}
    </div>
  );
}

function EarningsAnalyzerBody({ summary }: { summary: Record<string, unknown> }) {
  const upcoming = (summary.upcoming as Array<Record<string, unknown>>) ?? [];
  const totalRisk = summary.total_at_risk_cad as number | undefined;
  return (
    <div className="space-y-2">
      <div className="text-xs text-slate-400">
        Total expected-move risk: <span className="text-amber-400">{fmtCurrency(totalRisk)} CAD</span>
      </div>
      {upcoming.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-slate-500 text-[10px] uppercase tracking-wide">
            <tr>
              <th className="text-left">Symbol</th>
              <th className="text-right">Days</th>
              <th className="text-right">±Move</th>
              <th className="text-right">At Risk</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/40">
            {upcoming.slice(0, 8).map((u, i) => {
              const days = u.days_to_earnings as number | undefined;
              const cls = (days ?? 99) <= 7 ? "text-amber-400" : "text-slate-200";
              return (
                <tr key={i}>
                  <td className="py-1 text-slate-200 font-medium">{String(u.symbol)}</td>
                  <td className={`text-right ${cls}`}>{days ?? "—"}d</td>
                  <td className="text-right text-slate-300">±{fmt(u.expected_move_pct, "%")}</td>
                  <td className="text-right text-slate-300">{fmtCurrency(u.position_at_risk_cad)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function MacroImpactBody({ summary }: { summary: Record<string, unknown> }) {
  const regime = summary.regime as string | undefined;
  const misaligned = summary.misaligned_weight_pct as number | undefined;
  const alignment = (summary.sector_alignment as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-2">
      <div className="flex gap-4 text-xs">
        <div>
          <div className="text-slate-500">Regime</div>
          <div className="text-slate-100 font-medium">{regime ?? "—"}</div>
        </div>
        <div>
          <div className="text-slate-500">Misaligned weight</div>
          <div className={`font-medium ${(misaligned ?? 0) > 30 ? "text-amber-400" : "text-slate-100"}`}>
            {fmt(misaligned, "%")}
          </div>
        </div>
      </div>
      {alignment.length > 0 && (
        <div className="grid grid-cols-2 gap-1">
          {alignment.slice(0, 8).map((a, i) => {
            const verdict = a.verdict as string;
            const color = verdict === "favored"
              ? "text-emerald-400 border-emerald-500/30"
              : verdict === "disfavored"
                ? "text-rose-400 border-rose-500/30"
                : "text-slate-400 border-slate-700";
            return (
              <div key={i} className={`text-xs flex justify-between border rounded px-2 py-1 ${color}`}>
                <span className="truncate">{String(a.sector)}</span>
                <span>{fmt(a.weight_pct, "%")} · {verdict}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StockAnalysisBody({ summary }: { summary: Record<string, unknown> }) {
  const byAction = (summary.by_action as Record<string, number>) ?? {};
  const buys = (summary.top_buys as Array<Record<string, unknown>>) ?? [];
  const sells = (summary.top_sells as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-2">
      <div className="flex gap-2 text-xs flex-wrap">
        {Object.entries(byAction).map(([a, n]) => (
          <span key={a} className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
            {a}: {n}
          </span>
        ))}
      </div>
      {buys.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-emerald-400 mb-1">Top BUYs</div>
          <ul className="text-xs text-slate-200 space-y-0.5">
            {buys.map((b, i) => (
              <li key={i}>· {String(b.symbol)} — score {fmt(b.score)}</li>
            ))}
          </ul>
        </div>
      )}
      {sells.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-rose-400 mb-1">Top SELLs/TRIMs</div>
          <ul className="text-xs text-slate-200 space-y-0.5">
            {sells.map((s, i) => (
              <li key={i}>· {String(s.symbol)} — score {fmt(s.score)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function PortfolioHealthBody({ summary }: { summary: Record<string, unknown> }) {
  const score = summary.score as number | undefined;
  const grade = summary.grade as string | undefined;
  const verdict = summary.verdict as string | undefined;
  const breakdown = (summary.breakdown as Record<string, number>) ?? {};
  const color = (score ?? 0) >= 80 ? "text-emerald-400" : (score ?? 0) >= 60 ? "text-amber-400" : "text-rose-400";
  return (
    <div className="space-y-2">
      <div className="flex items-end gap-3">
        <div className={`text-3xl font-semibold ${color}`}>{score ?? "—"}</div>
        <div className="text-xs">
          <div className="text-slate-200 font-medium">{grade}</div>
          <div className="text-slate-500">{verdict}</div>
        </div>
      </div>
      {Object.keys(breakdown).length > 0 && (
        <div className="grid grid-cols-2 gap-1">
          {Object.entries(breakdown).map(([k, v]) => (
            <div key={k} className="text-xs flex justify-between border border-slate-800/60 rounded px-2 py-1">
              <span className="text-slate-400 capitalize">{k.replace(/_/g, " ")}</span>
              <span className="text-slate-200">{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DividendStrategyBody({ summary }: { summary: Record<string, unknown> }) {
  const top = (summary.top_income_payers as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-2">
      <div className="flex gap-4 text-xs">
        <div>
          <div className="text-slate-500">Portfolio yield</div>
          <div className="text-slate-100 font-medium">{fmt(summary.portfolio_yield_pct, "%")}</div>
        </div>
        <div>
          <div className="text-slate-500">Annual income</div>
          <div className="text-emerald-400 font-medium">{fmtCurrency(summary.annual_income_cad)} CAD</div>
        </div>
      </div>
      {top.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-slate-500 text-[10px] uppercase tracking-wide">
            <tr>
              <th className="text-left">Symbol</th>
              <th className="text-right">Yield</th>
              <th className="text-right">Payout</th>
              <th className="text-right">Income</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/40">
            {top.slice(0, 6).map((d, i) => (
              <tr key={i}>
                <td className="py-1 text-slate-200 font-medium">{String(d.symbol)}</td>
                <td className="text-right text-slate-300">{fmt(d.dividend_yield_pct, "%")}</td>
                <td className={`text-right ${d.sustainable ? "text-slate-300" : "text-amber-400"}`}>
                  {d.payout_ratio_pct ? `${d.payout_ratio_pct}%` : "—"}
                </td>
                <td className="text-right text-emerald-400">{fmtCurrency(d.annual_income_cad)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function SectorRotationBody({ summary }: { summary: Record<string, unknown> }) {
  const ranked = (summary.sector_momentum_ranked as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-2">
      {ranked.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-slate-500 text-[10px] uppercase tracking-wide">
            <tr>
              <th className="text-left">ETF</th>
              <th className="text-right">1m</th>
              <th className="text-right">3m</th>
              <th className="text-right">6m</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/40">
            {ranked.map((r, i) => (
              <tr key={i}>
                <td className="py-1 text-slate-200 font-medium">{String(r.etf)}</td>
                {(["1m_pct", "3m_pct", "6m_pct"] as const).map((k) => {
                  const v = r[k] as number | undefined;
                  const cls = (v ?? 0) > 0 ? "text-emerald-400" : (v ?? 0) < 0 ? "text-rose-400" : "text-slate-400";
                  return (
                    <td key={k} className={`text-right ${cls}`}>{fmt(v, "%")}</td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TaxLossReviewBody({ summary }: { summary: Record<string, unknown> }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {[
        ["Candidates", summary.n_candidates, ""],
        ["Harvest pot.", summary.total_loss_cad, "$"],
        ["Tax saved est.", summary.est_tax_saving_cad, "$"],
      ].map(([label, val, suf]) => (
        <div key={label as string} className="border border-slate-800/60 rounded p-2">
          <div className="text-[10px] text-slate-500 uppercase tracking-wide">{label as string}</div>
          <div className="text-sm text-slate-100 font-medium">
            {suf === "$"
              ? fmtCurrency(typeof val === "number" ? Math.abs(val) : val)
              : fmt(val)}
          </div>
        </div>
      ))}
    </div>
  );
}

function renderSkillBody(snap: SkillSnapshot) {
  const summary = snap.summary ?? {};
  switch (snap.skill) {
    case "daily-briefing": return <DailyBriefingBody summary={summary} />;
    case "cash-deployment": return <CashDeploymentBody summary={summary} />;
    case "risk-assessment": return <RiskAssessmentBody summary={summary} />;
    case "earnings-analyzer": return <EarningsAnalyzerBody summary={summary} />;
    case "macro-impact": return <MacroImpactBody summary={summary} />;
    case "stock-analysis": return <StockAnalysisBody summary={summary} />;
    case "portfolio-health": return <PortfolioHealthBody summary={summary} />;
    case "dividend-strategy": return <DividendStrategyBody summary={summary} />;
    case "sector-rotation": return <SectorRotationBody summary={summary} />;
    case "tax-loss-review": return <TaxLossReviewBody summary={summary} />;
    default:
      return (
        <pre className="bg-slate-950/60 p-2 rounded text-slate-300 overflow-x-auto max-h-64 text-xs">
          {JSON.stringify(summary, null, 2)}
        </pre>
      );
  }
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
              {renderSkillBody(snap)}
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
          <h3 className="text-sm font-semibold text-slate-200">Last Skill Analysis Results</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Cached results from the last time each skill ran. Re-runs automatically every {scheduler?.is_market_hours ? "15 min during market hours" : "60 min off-hours"}.
            {scheduler?.last_run_ts && (
              <> Last run: {formatRelative(new Date(scheduler.last_run_ts * 1000).toISOString())}.</>
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
        <div className="py-6 text-center space-y-1">
          <p className="text-xs text-slate-400">No results yet.</p>
          <p className="text-[11px] text-slate-500">Run any skill in the Claude chat — e.g. <span className="font-mono text-indigo-400">/portfolio-health</span> or <span className="font-mono text-indigo-400">/daily-briefing</span> — and results cache here automatically.</p>
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
