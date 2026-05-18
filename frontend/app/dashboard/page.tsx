"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  wsGetPortfolio,
  wsGetHealthScore,
  wsGetAlerts,
  wsGetRecommendations,
  wsGetMacro,
  wsGetNarratives,
  wsGetBenchmark,
  wsGetOptimizer,
  wsGetCrowding,
  PortfolioResponse,
  UserProfile,
  HealthScore,
  Alert,
  Recommendation,
  SignalChange,
  MacroSnapshot,
  BenchmarkResult,
  OptimizerResult,
  CrowdingMap,
} from "@/lib/api";
import PortfolioTable from "@/components/PortfolioTable";
import AllocationChart from "@/components/AllocationChart";
import PriceChart from "@/components/PriceChart";
import HealthScoreWidget from "@/components/HealthScoreWidget";
import AlertsPanel from "@/components/AlertsPanel";
import RecommendationsPanel from "@/components/RecommendationsPanel";
import MacroWidget from "@/components/MacroWidget";
import BenchmarkWidget from "@/components/BenchmarkWidget";
import OptimizerWidget from "@/components/OptimizerWidget";
import CountdownLabel from "@/components/CountdownLabel";

function currency(val: number) {
  return val.toLocaleString("en-CA", { style: "currency", currency: "CAD" });
}

function pct(val: number, showSign = true) {
  const sign = showSign && val >= 0 ? "+" : "";
  return `${sign}${val.toFixed(2)}%`;
}

const REFRESH_INTERVAL_MS = 5 * 60 * 1000;

const SKILLS = [
  { name: "/portfolio-health", desc: "BlackRock health score + rebalance plan", icon: "◈" },
  { name: "/risk-assessment", desc: "Bridgewater stress test + hedging", icon: "⬡" },
  { name: "/stock-analysis", desc: "Goldman + Citadel deep dive on a ticker", icon: "◉" },
  { name: "/macro-impact", desc: "McKinsey macro briefing on your holdings", icon: "◫" },
  { name: "/dividend-strategy", desc: "Harvard Endowment income blueprint", icon: "◈" },
  { name: "/earnings-analyzer", desc: "JPMorgan pre-earnings brief", icon: "◆" },
  { name: "/sector-rotation", desc: "Renaissance rotation signals", icon: "◎" },
  { name: "/adversarial-research", desc: "Parallel bull/bear agents → verdict", icon: "⬡" },
  { name: "/tax-loss-review", desc: "Canadian tax-loss harvesting (TFSA/RRSP-aware)", icon: "◈" },
  { name: "/daily-briefing", desc: "Morning digest: health + alerts + crowding + macro", icon: "◐" },
  { name: "/cash-deployment", desc: "Add-to-winners cash allocation with crowding guard", icon: "◇" },
  { name: "/stock-compare", desc: "Head-to-head A vs B matchup", icon: "◇" },
  { name: "/earnings-postmortem", desc: "Post-report EPS beat/miss breakdown", icon: "◆" },
];

function useCountUp(target: number, duration = 700): number {
  const [display, setDisplay] = useState(target);
  const frameRef = useRef<number | null>(null);
  const prevRef = useRef<number>(target);
  useEffect(() => {
    const from = prevRef.current;
    prevRef.current = target;
    if (from === target) return;
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - t0) / duration, 1);
      const ease = 1 - (1 - p) ** 3;
      setDisplay(from + (target - from) * ease);
      if (p < 1) frameRef.current = requestAnimationFrame(tick);
      else frameRef.current = null;
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => { if (frameRef.current !== null) cancelAnimationFrame(frameRef.current); };
  }, [target, duration]);
  return display;
}

export default function DashboardPage() {
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [selectedAccount, setSelectedAccount] = useState<string>("");

  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
  const [portfolioLoading, setPortfolioLoading] = useState(false);
  const [portfolioError, setPortfolioError] = useState<string | null>(null);

  const [healthScore, setHealthScore] = useState<HealthScore | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);

  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(false);

  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null);
  const [signalChanges, setSignalChanges] = useState<SignalChange[]>([]);
  const [recsLoading, setRecsLoading] = useState(false);

  const [narratives, setNarratives] = useState<Record<string, string | null>>({});
  const [narrativesLoading, setNarrativesLoading] = useState(false);
  const [narrativeProviders, setNarrativeProviders] = useState<string[]>([]);

  const [macro, setMacro] = useState<MacroSnapshot | null>(null);
  const [macroLoading, setMacroLoading] = useState(false);

  const [benchmark, setBenchmark] = useState<BenchmarkResult | null>(null);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);

  const [optimizer, setOptimizer] = useState<OptimizerResult | null>(null);
  const [optimizerLoading, setOptimizerLoading] = useState(false);

  const [crowding, setCrowding] = useState<CrowdingMap>({});
  const [crowdingLoading, setCrowdingLoading] = useState(false);

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [copiedSkill, setCopiedSkill] = useState<string | null>(null);
  const [skillsOpen, setSkillsOpen] = useState(false);

  // eslint-disable-next-line react-hooks/purity
  const [lastRefresh, setLastRefresh] = useState<number>(Date.now());
  // Per-loader AbortController so a new fetch cancels the prior in-flight one.
  // Prevents stale responses from stomping fresher state (e.g. account-tab race).
  const abortersRef = useRef<Record<string, AbortController>>({});

  const abortable = useCallback((key: string): AbortSignal => {
    abortersRef.current[key]?.abort();
    const ctrl = new AbortController();
    abortersRef.current[key] = ctrl;
    return ctrl.signal;
  }, []);

  useEffect(() => () => {
    for (const c of Object.values(abortersRef.current)) c.abort();
  }, []);

  useEffect(() => {
    const sid = sessionStorage.getItem("ws_session_id");
    const prof = sessionStorage.getItem("ws_profile");
    if (!sid) { router.push("/login"); return; }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSessionId(sid);
    if (prof) setProfile(JSON.parse(prof));
  }, [router]);

  const isAbort = (err: unknown) =>
    err instanceof DOMException && err.name === "AbortError";

  const loadPortfolio = useCallback(async (accountId: string = selectedAccount) => {
    if (!sessionId) return;
    const signal = abortable("portfolio");
    setPortfolioLoading(true);
    setPortfolioError(null);
    try {
      const data = await wsGetPortfolio(sessionId, accountId, signal);
      setPortfolio(data);
      if (!selectedTicker && data.positions.length > 0) {
        setSelectedTicker(data.positions[0].symbol);
      }
    } catch (err: unknown) {
      if (isAbort(err)) return;
      const msg = err instanceof Error ? err.message : "Failed to load portfolio";
      if (msg.includes("expired")) router.push("/login");
      else setPortfolioError(msg);
    } finally {
      if (!signal.aborted) setPortfolioLoading(false);
    }
  }, [sessionId, router, selectedTicker, selectedAccount, abortable]);

  const loadHealthScore = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("health");
    if (!healthScore) setHealthLoading(true);
    try { setHealthScore(await wsGetHealthScore(sessionId, signal)); }
    catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setHealthLoading(false); }
  }, [sessionId, abortable, healthScore]);

  const loadAlerts = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("alerts");
    if (alerts.length === 0) setAlertsLoading(true);
    try { setAlerts(await wsGetAlerts(sessionId, signal)); }
    catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setAlertsLoading(false); }
  }, [sessionId, abortable, alerts.length]);

  const loadNarratives = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("narratives");
    if (Object.keys(narratives).length === 0) setNarrativesLoading(true);
    try {
      const res = await wsGetNarratives(sessionId, signal);
      if (!res.error) {
        setNarratives(res.narratives);
        setNarrativeProviders(res.providers ?? []);
      }
    } catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setNarrativesLoading(false); }
  }, [sessionId, abortable, narratives]);

  const loadRecommendations = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("recs");
    if (!recommendations) setRecsLoading(true);
    try {
      const res = await wsGetRecommendations(sessionId, signal);
      setRecommendations(res.recommendations);
      if (res.signal_changes?.length) setSignalChanges(res.signal_changes);
      loadNarratives();
    } catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setRecsLoading(false); }
  }, [sessionId, loadNarratives, abortable, recommendations]);

  const loadMacro = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("macro");
    if (!macro) setMacroLoading(true);
    try { setMacro(await wsGetMacro(sessionId, signal)); }
    catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setMacroLoading(false); }
  }, [sessionId, abortable, macro]);

  const loadBenchmark = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("benchmark");
    if (!benchmark) setBenchmarkLoading(true);
    try { setBenchmark(await wsGetBenchmark(sessionId, signal)); }
    catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setBenchmarkLoading(false); }
  }, [sessionId, abortable, benchmark]);

  const loadOptimizer = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("optimizer");
    if (!optimizer) setOptimizerLoading(true);
    try { setOptimizer(await wsGetOptimizer(sessionId, signal)); }
    catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setOptimizerLoading(false); }
  }, [sessionId, abortable, optimizer]);

  const loadCrowding = useCallback(async () => {
    if (!sessionId) return;
    const signal = abortable("crowding");
    if (Object.keys(crowding).length === 0) setCrowdingLoading(true);
    try { setCrowding(await wsGetCrowding(sessionId, 15, signal)); }
    catch (err) { if (isAbort(err)) return; }
    finally { if (!signal.aborted) setCrowdingLoading(false); }
  }, [sessionId, abortable, crowding]);

  const refreshAll = useCallback(() => {
    setLastRefresh(Date.now());
    loadPortfolio();
    loadHealthScore();
    loadAlerts();
    loadRecommendations();
    loadMacro();
    loadBenchmark();
    loadOptimizer();
    loadCrowding();
  }, [loadPortfolio, loadHealthScore, loadAlerts, loadRecommendations, loadMacro, loadBenchmark, loadOptimizer, loadCrowding]);

  useEffect(() => {
    if (!sessionId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPortfolio();
    loadHealthScore();
    loadAlerts();
    loadRecommendations();
    loadMacro();
    loadCrowding();
    const t = setTimeout(() => { loadBenchmark(); loadOptimizer(); }, 1500);
    return () => clearTimeout(t);
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!sessionId) return;
    const interval = setInterval(refreshAll, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [sessionId, refreshAll]);

  const copySkill = (name: string) => {
    navigator.clipboard.writeText(name).catch(() => {});
    setCopiedSkill(name);
    setTimeout(() => setCopiedSkill(null), 1500);
  };

  const summary = portfolio?.summary;
  const totalValue = summary?.total_value ?? 0;
  const totalReturn = summary?.total_return_pct ?? 0;
  const cashAvailable = summary?.cash_available ?? 0;
  const totalCost = summary?.total_cost ?? 0;
  const dayChangeCad = summary?.day_change_cad ?? 0;

  const animatedTotalValue = useCountUp(totalValue);
  const animatedDayChange = useCountUp(dayChangeCad);

  const summaryCards = [
    { label: "Portfolio Value", value: currency(animatedTotalValue), color: "text-white" },
    {
      label: "Day Change",
      value: `${dayChangeCad >= 0 ? "+" : ""}${currency(animatedDayChange)}`,
      color: dayChangeCad >= 0 ? "text-emerald-400" : "text-rose-400",
    },
    {
      label: "Total Return",
      value: pct(totalReturn),
      color: totalReturn >= 0 ? "text-emerald-400" : "text-rose-400",
    },
    { label: "Book Cost", value: currency(totalCost), color: "text-slate-300" },
    { label: "Cash Available", value: currency(cashAvailable), color: "text-slate-300" },
  ];

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Sticky header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-bold text-white text-lg">aifolimizer</span>
            {selectedAccount && (
              <span className="text-xs text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded-full">
                {selectedAccount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-4">
            <CountdownLabel intervalMs={REFRESH_INTERVAL_MS} resetKey={lastRefresh} />
            <button
              onClick={refreshAll}
              disabled={portfolioLoading}
              className="text-xs text-indigo-400 hover:text-indigo-300 disabled:text-slate-600 transition-colors"
            >
              {portfolioLoading ? "Refreshing…" : "Refresh"}
            </button>
            <button
              onClick={() => { sessionStorage.clear(); router.push("/login"); }}
              className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              Disconnect
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-5">

        {/* ── Account tabs ── */}
        {profile && profile.accounts.length > 0 && (
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={() => { setSelectedAccount(""); loadPortfolio(""); }}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                selectedAccount === "" ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-400 hover:text-white"
              }`}
            >
              All Accounts
            </button>
            {profile.accounts.map(acc => (
              <button
                key={acc.type}
                onClick={() => { setSelectedAccount(acc.type); loadPortfolio(acc.type); }}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  selectedAccount === acc.type ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-400 hover:text-white"
                }`}
              >
                {acc.type}
                {acc.invested_value > 0 && (
                  <span className="ml-1.5 text-slate-500">{currency(acc.invested_value)}</span>
                )}
              </button>
            ))}
          </div>
        )}

        {/* ── Row 1: Summary cards ── */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {summaryCards.map(card => (
            <div key={card.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <p className="text-xs text-slate-500 mb-1">{card.label}</p>
              {portfolioLoading && !totalValue ? (
                <div className="h-5 w-24 bg-slate-800 rounded animate-pulse mt-0.5" />
              ) : (
                <p className={`text-base font-semibold ${card.color}`}>{card.value}</p>
              )}
            </div>
          ))}
        </div>

        {/* ── Row 2: Health + Macro + Allocation ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-1">
            <HealthScoreWidget data={healthScore} loading={healthLoading} />
          </div>
          <div className="lg:col-span-1">
            <MacroWidget data={macro} loading={macroLoading} />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-2">Allocation</p>
            {portfolio ? (
              <AllocationChart
                positions={portfolio.positions}
                cashAvailable={cashAvailable}
                totalValue={totalValue}
              />
            ) : (
              <div className="h-48 flex items-center justify-center text-slate-600 text-sm">
                {portfolioLoading ? "Loading…" : "No data"}
              </div>
            )}
          </div>
        </div>

        {/* ── Portfolio defensive signal — fires when macro risks align ── */}
        {macro?.portfolio_signal && macro.portfolio_signal !== "stay_invested" && (
          <div className={`rounded-xl p-4 border ${
            macro.portfolio_signal === "raise_cash"
              ? "bg-rose-500/10 border-rose-500/40"
              : "bg-amber-500/10 border-amber-500/30"
          }`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className={`text-sm font-bold ${macro.portfolio_signal === "raise_cash" ? "text-rose-400" : "text-amber-400"}`}>
                  {macro.portfolio_signal === "raise_cash" ? "🚨 RAISE CASH" : "⚠ REDUCE RISK"}
                  {macro.portfolio_target_cash_pct && (
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      target {macro.portfolio_target_cash_pct}% cash
                    </span>
                  )}
                </p>
                <ul className="mt-1.5 space-y-0.5">
                  {macro.portfolio_signal_reasons?.map((r, i) => (
                    <li key={i} className="text-xs text-slate-300">· {r}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        )}

        {/* ── Signal changes banner — fires when actions flip since last load ── */}
        {signalChanges.length > 0 && (
          <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-amber-400">⚡ Signal Changes Detected</p>
              <button onClick={() => setSignalChanges([])} className="text-xs text-slate-500 hover:text-slate-300">dismiss</button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {signalChanges.map(sc => (
                <div key={sc.symbol} className="flex items-start gap-3 bg-slate-900/60 rounded-lg p-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-semibold text-white">{sc.symbol}</span>
                      <span className="text-xs text-slate-500">{sc.from_action}</span>
                      <span className="text-xs text-slate-600">→</span>
                      <span className={`text-xs font-bold ${
                        sc.to_action === "BUY" ? "text-emerald-400" :
                        sc.to_action === "SELL" ? "text-rose-400" :
                        "text-amber-400"
                      }`}>{sc.to_action}</span>
                      {sc.confidence && (
                        <span className={`text-[10px] px-1 rounded ${sc.confidence === "high" ? "text-emerald-400 bg-emerald-500/10" : "text-slate-500 bg-slate-800"}`}>
                          {sc.confidence}
                        </span>
                      )}
                    </div>
                    {sc.top_reason && <p className="text-xs text-slate-400 mt-0.5 truncate">{sc.top_reason}</p>}
                    {sc.ev_dollars != null && (
                      <p className={`text-xs font-medium mt-0.5 ${sc.ev_dollars >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        EV {sc.ev_dollars >= 0 ? "+" : ""}${sc.ev_dollars.toFixed(0)} if Kelly-sized
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Row 3: Recommendations ── */}
        <RecommendationsPanel
          data={recommendations}
          loading={recsLoading}
          narratives={narratives}
          narrativesLoading={narrativesLoading}
          narrativeProviders={narrativeProviders}
        />

        {/* ── Row 4: Benchmark comparison ── */}
        <BenchmarkWidget data={benchmark} loading={benchmarkLoading} />

        {/* ── Row 5: Efficient Frontier optimizer ── */}
        <OptimizerWidget data={optimizer} loading={optimizerLoading} />

        {/* ── Row 6: Alerts ── */}
        <AlertsPanel alerts={alerts} loading={alertsLoading} />

        {portfolioError && (
          <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-400 text-sm">
            {portfolioError}
          </div>
        )}

        {/* ── Row 5: Holdings ── */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white">
              Holdings
              {portfolio && (
                <span className="text-slate-600 font-normal ml-2">
                  {portfolio.positions.length} positions
                </span>
              )}
            </h2>
          </div>
          <PortfolioTable
            positions={portfolio?.positions || []}
            crowding={crowding}
            onSelectTicker={setSelectedTicker}
            selectedTicker={selectedTicker}
          />
          {crowdingLoading && Object.keys(crowding).length === 0 && (
            <p className="text-[10px] text-slate-600 mt-2">Loading crowding signals…</p>
          )}
        </div>

        {/* ── Row 6: Price chart ── */}
        {sessionId && selectedTicker && (
          <PriceChart symbol={selectedTicker} sessionId={sessionId} />
        )}

        {/* ── Row 7: Skills (collapsible) ── */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <button
            onClick={() => setSkillsOpen(o => !o)}
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-white hover:bg-slate-800/40 transition-colors"
          >
            <span>Analysis Skills</span>
            <span className="text-slate-500 text-xs">
              {skillsOpen ? "▲ collapse" : "▼ expand"} · run in Claude Code or Claude Desktop
            </span>
          </button>
          {skillsOpen && (
            <div className="px-4 pb-4 pt-1">
              <p className="text-xs text-slate-500 mb-3">
                Click to copy command, paste in Claude Code for deep AI analysis.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {SKILLS.map(s => (
                  <button
                    key={s.name}
                    onClick={() => copySkill(s.name)}
                    className="text-left border border-slate-700 bg-slate-800/40 hover:bg-slate-800 rounded-lg p-3 transition-colors group"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-sm text-indigo-400">{s.name}</span>
                      <span className="text-xs text-slate-600 group-hover:text-slate-400 transition-colors">
                        {copiedSkill === s.name ? "✓ copied" : "copy"}
                      </span>
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">{s.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

      </main>
    </div>
  );
}
