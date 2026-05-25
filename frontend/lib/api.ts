const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 30_000;

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Default 30s timeout when caller didn't pass its own AbortSignal.
  // Prevents indefinite hangs when backend stalls on yfinance/Wealthsimple.
  const signal = init?.signal ?? AbortSignal.timeout(DEFAULT_TIMEOUT_MS);
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    credentials: "include",
    ...init,
    signal,
  });
  if (!res.ok) {
    if (res.status === 401) {
      sessionStorage.removeItem("ws_session_id");
      sessionStorage.removeItem("ws_profile");
    }
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

export async function wsLogin(email: string, password: string) {
  return apiFetch<{ needs_otp?: boolean; session_id: string; profile?: UserProfile }>("/ws/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function wsVerifyOtp(session_id: string, otp: string) {
  return apiFetch<{ session_id: string; profile: UserProfile }>("/ws/verify-otp", {
    method: "POST",
    body: JSON.stringify({ session_id, otp }),
  });
}

export async function wsRestoreSession() {
  return apiFetch<{
    restored: boolean;
    session_id?: string;
    profile?: UserProfile | null;
  }>("/ws/restore", { method: "POST" });
}

export async function wsGetPortfolio(
  session_id: string,
  account_id: string = "",
  signal?: AbortSignal,
) {
  const qs = account_id ? `&account_id=${encodeURIComponent(account_id)}` : "";
  return apiFetch<PortfolioResponse>(
    `/ws/portfolio?session_id=${session_id}${qs}`,
    { signal },
  );
}

export async function wsGetPriceHistory(
  session_id: string,
  symbol: string,
  period: string = "1y",
  signal?: AbortSignal,
) {
  return apiFetch<PriceHistory>(
    `/ws/price-history?session_id=${session_id}&symbol=${encodeURIComponent(symbol)}&period=${period}`,
    { signal },
  );
}

export async function wsGetHealthScore(session_id: string, signal?: AbortSignal) {
  return apiFetch<HealthScore>(
    `/ws/health-score?session_id=${session_id}`, { signal },
  );
}

export async function wsGetAlerts(session_id: string, signal?: AbortSignal) {
  return apiFetch<Alert[]>(
    `/ws/alerts?session_id=${session_id}`, { signal },
  );
}

export interface SignalChange {
  symbol: string;
  name: string;
  from_action: string;
  to_action: string;
  score: number;
  confidence: string | null;
  top_reason: string | null;
  ev_dollars: number | null;
}

export interface RecommendationsResponse {
  recommendations: Recommendation[];
  signal_changes: SignalChange[];
}

export async function wsGetRecommendations(session_id: string, signal?: AbortSignal) {
  return apiFetch<RecommendationsResponse>(
    `/ws/recommendations?session_id=${session_id}`, { signal },
  );
}

export async function wsGetMacro(session_id: string, signal?: AbortSignal) {
  return apiFetch<MacroSnapshot>(
    `/ws/macro?session_id=${session_id}`, { signal },
  );
}

export async function wsGetBenchmark(session_id: string, signal?: AbortSignal) {
  return apiFetch<BenchmarkResult>(
    `/ws/benchmark?session_id=${session_id}`, { signal },
  );
}

export async function wsGetOptimizer(session_id: string, signal?: AbortSignal) {
  return apiFetch<OptimizerResult>(
    `/ws/optimize?session_id=${session_id}`, { signal },
  );
}

export async function wsGetNarratives(session_id: string, signal?: AbortSignal) {
  return apiFetch<NarrativesResponse>(
    `/ws/ai-narratives?session_id=${session_id}`, { signal },
  );
}

export interface PortfolioCommentary {
  commentary: string | null;
  actions: string[];
  provider: string | null;
  error?: string;
}

export async function wsGetPortfolioCommentary(session_id: string, signal?: AbortSignal) {
  return apiFetch<PortfolioCommentary>(
    `/ws/ai-commentary?session_id=${session_id}`, { signal },
  );
}

export async function wsGetCrowding(
  session_id: string,
  top_n: number = 15,
  signal?: AbortSignal,
) {
  return apiFetch<CrowdingMap>(
    `/ws/crowding?session_id=${session_id}&top_n=${top_n}`, { signal },
  );
}

export interface CrowdingSignal {
  institutional_ownership_pct: number | null;
  short_pct_float: number | null;
  insider_ownership_pct: number | null;
  analyst_count: number | null;
  analyst_recommendation: string | null;
  headlines_7d: number;
  headlines_30d: number;
  headline_velocity_ratio: number | null;
  crowding_score: number;
  crowding_label: "consensus" | "neutral" | "contrarian";
  contrarian_flag: boolean;
  consensus_flag: boolean;
}

export type CrowdingMap = Record<string, CrowdingSignal>;

// ─── Types ────────────────────────────────────────────────────────────────────

export interface PriceHistory {
  symbol: string;
  period: string;
  dates: string[];
  close: number[];
  sma_20: (number | null)[];
  sma_50: (number | null)[];
  sma_200: (number | null)[];
  weekly_sma_20: (number | null)[];
}

export interface ChartPattern {
  pattern: string;
  neckline: number;
  confirmed: boolean;
  bearish: boolean;
  description: string;
  peak1_date?: string;
  peak2_date?: string;
  trough1_date?: string;
  trough2_date?: string;
  left_shoulder_date?: string;
  head_date?: string;
  right_shoulder_date?: string;
  peak1_price?: number;
  peak2_price?: number;
  trough1_price?: number;
  trough2_price?: number;
  head_price?: number;
  left_shoulder_price?: number;
  right_shoulder_price?: number;
}

export interface PatternResult {
  symbol: string;
  patterns: ChartPattern[];
  dates: string[];
  close: number[];
}

export async function wsGetPatterns(
  session_id: string,
  symbol: string,
  period: string = "1y",
  signal?: AbortSignal,
) {
  return apiFetch<PatternResult>(
    `/ws/patterns?session_id=${session_id}&symbol=${encodeURIComponent(symbol)}&period=${period}`,
    { signal },
  );
}

export interface UserProfile {
  accounts: { type: string; currency: string; cash_balance: number; invested_value: number }[];
  total_cash: number;
  total_invested: number;
  account_types: string[];
}

export interface Position {
  symbol: string;
  name: string;
  quantity: number;
  currency: string;
  book_cost: number;
  book_cost_cad: number;
  market_value: number;
  market_value_cad: number;
  current_price?: number;
  current_price_cad?: number;
  day_change_pct: number;
  total_return_pct: number;
  weight: number;
  asset_class: string;
  sector?: string;
}

export interface PortfolioSummary {
  total_value: number;
  total_cost: number;
  total_return_pct: number;          // equity-only return (PnL / book_cost)
  cash_available: number;
  cash_available_usd?: number;
  day_change_cad: number;
  net_deposits_cad?: number;
  account_return_pct?: number;       // (NLV - deposits) / deposits
  simple_return_pct?: number | null; // WS-reported account-wide return
}

export interface PortfolioResponse {
  positions: Position[];
  summary: PortfolioSummary;
}

export interface HealthScore {
  score: number;
  grade: "A" | "B" | "C" | "D" | "F" | "N/A";
  verdict: string;
  breakdown: {
    diversification: number;
    concentration: number;
    performance: number;
    cash_efficiency: number;
    asset_class_diversity: number;
  };
  inputs: {
    position_count: number;
    max_single_weight_pct: number;
    total_return_pct: number;
    cash_pct: number;
    asset_classes: string[];
  };
}

export interface Alert {
  type: "concentration" | "earnings" | "technical" | "macro";
  severity: "high" | "warning" | "info";
  title: string;
  detail: string;
}

export interface Recommendation {
  symbol: string;
  name: string;
  currency: string;
  action: "BUY" | "HOLD" | "WATCH" | "SELL" | "ADD" | "TRIM" | "NO_EDGE";
  score: number;
  confidence: "high" | "medium" | "low";
  reasons: string[];
  flags: string[];
  asset_class: string;
  weight: number;
  total_return_pct: number;
  current_price: number | null;
  analyst_target: number | null;
  analyst_upside_pct: number | null;
  stage: number | null;
  rsi: number | null;
  market_regime: string;
  sentiment: number;
  tech_score: number;
  fund_score: number;
  macro_score: number;
  llm_demoted?: boolean;
  // Actionable trade levels
  stop_loss: number | null;
  stop_type: string | null;
  take_profit: number | null;
  risk_reward: number | null;
  entry_timing: "acceptable" | "wait_pullback";
  kelly_pct: number | null;
  // Expected value
  ev_dollars: number | null;
  win_prob: number | null;
  max_loss_dollars: number | null;
  // Earnings
  days_to_earnings: number | null;
  expected_move_pct: number | null;
  earnings_risk: "imminent" | "upcoming" | null;
  // Hedge
  hedge_flag: boolean;
  hedge_reason: string | null;
}

export interface BenchmarkPeriod {
  label: string;
  portfolio_return: number | null;
  benchmarks: Record<string, number | null>;
  alpha: Record<string, number | null>;
}

export interface BenchmarkResult {
  periods: Record<string, BenchmarkPeriod>;
  benchmarks_meta: Record<string, string>;
}

export interface OptimizerChange {
  symbol: string;
  current_weight: number;
  optimal_weight: number;
  change: number;
  action: "INCREASE" | "DECREASE" | "ADD" | "TRIM";
}

export interface OptimizerResult {
  optimal_weights: Record<string, number>;
  expected_annual_return_pct: number;
  expected_annual_volatility_pct: number;
  sharpe_ratio: number;
  changes: OptimizerChange[];
  missing_symbols: string[];
  method: string;
  risk_free_rate_pct: number;
  error?: string;
}

export interface NarrativesResponse {
  narratives: Record<string, string | null>;
  providers: string[];
  error?: string;
}

export interface WatchlistItem {
  symbol: string;
  notes: string;
  added_at: string;
}

export type WatchlistRecommendation = Omit<Recommendation, "action"> & {
  action: "BUY" | "WATCH" | "PASS";
  source: "watchlist";
  notes: string;
};

export interface WatchlistRecommendationsResponse {
  recommendations: WatchlistRecommendation[];
}

export async function wsGetWatchlist(session_id: string, signal?: AbortSignal) {
  return apiFetch<WatchlistItem[]>(
    `/ws/watchlist?session_id=${session_id}`, { signal },
  );
}

export async function wsAddToWatchlist(
  session_id: string,
  symbol: string,
  notes = "",
) {
  return apiFetch<WatchlistItem[]>("/ws/watchlist", {
    method: "POST",
    body: JSON.stringify({ session_id, symbol, notes }),
  });
}

export async function wsRemoveFromWatchlist(
  session_id: string,
  symbol: string,
) {
  return apiFetch<WatchlistItem[]>(
    `/ws/watchlist/${encodeURIComponent(symbol)}?session_id=${session_id}`,
    { method: "DELETE" },
  );
}

export async function wsGetWatchlistRecommendations(
  session_id: string,
  signal?: AbortSignal,
) {
  return apiFetch<WatchlistRecommendationsResponse>(
    `/ws/watchlist/recommendations?session_id=${session_id}`,
    { signal: signal ?? AbortSignal.timeout(60_000) },
  );
}

export interface ScreenerResult {
  symbol: string;
  technical_score: number | null;
  current_price: number | null;
  stage: number | null;
  minervini_score: number | null;
  rsi_14: number | null;
  rsi_signal: string | null;
  adx_14: number | null;
  adx_signal: string | null;
  macd_hist: number | null;
  stoch_k: number | null;
  stoch_signal: string | null;
  obv_trend: string | null;
  volume_score: number | null;
  atr_pct: number | null;
  pct_from_52w_high: number | null;
  trend: string | null;
  sma_200_slope_pct: number | null;
}

export interface ScreenerResponse {
  results: ScreenerResult[];
  universe: string;
  count: number;
}

export async function wsGetScreener(
  session_id: string,
  universe: "tsx" | "spx" | "full" = "full",
  max_results = 30,
  signal?: AbortSignal,
) {
  return apiFetch<ScreenerResponse>(
    `/ws/screener?session_id=${session_id}&universe=${universe}&max_results=${max_results}`,
    { signal },
  );
}

export interface MacroSnapshot {
  vix: number | null;
  vix_signal: string | null;
  vix_regime: string | null;
  spy_price: number | null;
  spy_sma200: number | null;
  spy_vs_sma200_pct: number | null;
  spy_regime: string | null;
  market_regime: string;
  regime_signal: string;
  fear_greed_score: number | null;
  fear_greed_rating: string | null;
  yield_curve_spread: number | null;
  yield_curve_inverted: boolean | null;
  yield_curve_signal: "deeply_inverted" | "inverted" | "flat" | "normal" | null;
  two_year_yield: number | null;
  ten_year_yield: number | null;
  portfolio_signal: "raise_cash" | "reduce_risk" | "stay_invested" | null;
  portfolio_signal_strength: "strong" | "moderate" | "none" | null;
  portfolio_target_cash_pct: number | null;
  portfolio_signal_reasons: string[];
  fred: {
    fed_funds: { value: number; date: string } | null;
    ten_year_yield: { value: number; date: string } | null;
    cad_usd: { value: number; date: string } | null;
    boc_overnight: { value: number; date: string } | null;
    canada_cpi: { value: number; date: string } | null;
    [key: string]: { value: number; date: string } | null;
  };
}


// ── Skill snapshots ──────────────────────────────────────────────────────────

export interface SkillSnapshot {
  skill: string;
  status: "ok" | "error" | "session_expired" | string;
  computed_at: string;
  ttl_minutes: number;
  expires_at: number;
  confidence_source: "backtested" | "live_validated" | "experimental";
  summary: Record<string, unknown>;
  actionable: unknown[];
  alerts: Array<{ level?: string; message?: string; [k: string]: unknown }>;
  error: string | null;
  fresh?: boolean;
}

export interface SkillListResponse {
  codified: string[];
  llm_only: string[];
}

export interface SchedulerStatus {
  running: boolean;
  last_run_ts: number | null;
  last_run_result: Record<string, unknown> | null;
  next_interval_seconds: number;
  is_market_hours: boolean;
}

export async function fetchAllSnapshots(signal?: AbortSignal) {
  return apiFetch<{ snapshots: SkillSnapshot[] }>("/skills/snapshots", { signal });
}

export async function refreshSnapshots(skill?: string) {
  const qs = skill ? `?skill=${encodeURIComponent(skill)}` : "";
  return apiFetch<SkillSnapshot | Record<string, unknown>>(
    `/skills/refresh${qs}`,
    { method: "POST" },
  );
}

export async function fetchSchedulerStatus(signal?: AbortSignal) {
  return apiFetch<SchedulerStatus>("/skills/scheduler/status", { signal });
}


// ── Trust / accuracy ─────────────────────────────────────────────────────────

export interface DecayHorizon {
  n: number;
  avg_ret_pct?: number;
  median_ret_pct?: number;
  win_rate_pct?: number;
  insufficient_data?: boolean;
}

export interface DecayCurve {
  action_filter: string;
  curve: Record<string, DecayHorizon>;
  peak_horizon: string | null;
  peak_avg_ret_pct: number | null;
  interpretation: string;
  as_of: string;
  error?: string;
}

export interface AttributionBucket {
  n: number;
  avg_ret_pct?: number;
  win_rate_pct?: number;
  verdict?: string;
  insufficient_data?: boolean;
}

export interface AttributionReport {
  horizon: number;
  n_total_scored: number;
  by_source: Record<string, AttributionBucket>;
  note: string;
  as_of: string;
  error?: string;
}

export interface CalibrationReport {
  horizon: number;
  n_total_scored: number;
  buckets: Record<string, { n: number; win_rate_pct?: number; avg_ret_pct?: number; insufficient_data?: boolean }>;
  verdict: "calibrated" | "weakly_calibrated" | "uncalibrated" | "insufficient_data";
  suggested_action: string;
  as_of: string;
  error?: string;
}

export interface TrackRecordWindow {
  count?: number;
  skipped?: number;
  win_rate_pct?: number;
  avg_return_pct?: number;
  avg_alpha_vs_spy_pct?: number;
  avg_alpha_vs_xeqt_pct?: number;
  target_hit_rate_pct?: number;
  stop_hit_rate_pct?: number;
  by_conviction?: Record<string, unknown>;
  by_action?: Record<string, unknown>;
}

export interface TrackRecord {
  windows: Record<string, TrackRecordWindow>;
  total_logged: number;
  as_of: number;
  model_version: string;
  error?: string;
}

export async function fetchTrustDecay(actionFilter?: string, signal?: AbortSignal) {
  const qs = actionFilter ? `?action_filter=${encodeURIComponent(actionFilter)}` : "";
  return apiFetch<DecayCurve>(`/skills/trust/decay${qs}`, { signal });
}

export async function fetchTrustAttribution(horizon = 21, signal?: AbortSignal) {
  return apiFetch<AttributionReport>(
    `/skills/trust/attribution?horizon=${horizon}`,
    { signal },
  );
}

export async function fetchTrustCalibration(horizon = 21, signal?: AbortSignal) {
  return apiFetch<CalibrationReport>(
    `/skills/trust/calibration?horizon=${horizon}`,
    { signal },
  );
}

export async function fetchTrustTrackRecord(signal?: AbortSignal) {
  return apiFetch<TrackRecord>("/skills/trust/track-record", { signal });
}

// ── Phase 3: integrated signals ────────────────────────────────────────────

export interface IntegratedSubSignals {
  tech: number | null;
  fund: number | null;
  macro: number | null;
  sentiment: number | null;
  skill_consensus: number | null;
  skill_confidence: number | null;
}

export interface IntegratedSignal {
  symbol: string;
  action: string;
  conviction: string | null;
  score: number | null;
  sub_signals: IntegratedSubSignals;
  skill_evidence: Record<string, number> | null;
  /** Phase 11: position-sizing surface. */
  kelly_pct: number | null;
  win_prob: number | null;
  risk_reward: number | null;
  ts: string | null;
}

export interface IntegratedSignalsResponse {
  as_of: string | null;
  signals: IntegratedSignal[];
}

export async function fetchIntegratedSignals(
  sessionId: string,
  signal?: AbortSignal,
) {
  return apiFetch<IntegratedSignalsResponse>(
    `/ws/signals?session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

export interface SignalHistoryPoint {
  ts: string | null;
  score: number | null;
  action: string | null;
  tech: number | null;
  fund: number | null;
  macro: number | null;
  sentiment: number | null;
  skill: number | null;
}

export interface SignalHistoryResponse {
  symbol: string;
  days: number;
  points: SignalHistoryPoint[];
}

export async function fetchSignalHistory(
  sessionId: string,
  symbol: string,
  days = 30,
  signal?: AbortSignal,
) {
  const qs =
    `?session_id=${encodeURIComponent(sessionId)}` +
    `&symbol=${encodeURIComponent(symbol)}&days=${days}`;
  return apiFetch<SignalHistoryResponse>(`/ws/signals/history${qs}`, { signal });
}

export interface WeightsRow {
  version: number | null;
  ts: string | null;
  w_tech: number;
  w_fund: number;
  w_macro: number;
  w_sentiment: number;
  w_skill: number;
  reason?: string | null;
  objective?: string | null;
}

export interface WeightsResponse {
  current: WeightsRow;
  history: WeightsRow[];
}

export async function fetchWeights(
  sessionId: string,
  limit = 30,
  signal?: AbortSignal,
) {
  return apiFetch<WeightsResponse>(
    `/ws/weights?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`,
    { signal },
  );
}

// ── Phase 10: live KPIs ────────────────────────────────────────────────────

export interface LiveKPIs {
  expectancy_pct: number;
  profit_factor: number;
  sharpe: number;
  sortino: number;
  max_drawdown_pct: number;
  hit_rate: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  n_trades: number;
  after_cost_drag_bps: number;
  regime_breakdown: Record<
    string,
    { pf: number; expectancy_pct: number; n: number }
  >;
  window_days: number;
  ts: string | null;
}

export interface LiveKPIsResponse {
  kpis: LiveKPIs;
  from: "snapshot" | "live";
}

export async function fetchLiveKPIs(
  sessionId: string,
  window = 30,
  signal?: AbortSignal,
) {
  return apiFetch<LiveKPIsResponse>(
    `/ws/kpis?session_id=${encodeURIComponent(sessionId)}&window=${window}`,
    { signal },
  );
}

// ── Phase 12: risk gate ────────────────────────────────────────────────────

export interface RiskGateState {
  status: "trade" | "reduce_size" | "halt";
  size_multiplier: number;
  reasons: string[];
  triggers: Record<string, unknown>;
  triggered_at: string;
  valid_until: string;
}

export interface RiskGateResponse {
  gate: RiskGateState | null;
}

export async function fetchRiskGate(
  sessionId: string,
  signal?: AbortSignal,
) {
  return apiFetch<RiskGateResponse>(
    `/ws/risk-gate?session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

export async function overrideRiskGate(
  sessionId: string,
  reason: string,
  hours = 24,
) {
  return apiFetch<RiskGateResponse>("/ws/risk-gate/override", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, reason, hours }),
  });
}

// ── Phase 13: discovery ───────────────────────────────────────────────────

export interface DiscoveryPick {
  symbol: string;
  action: string;
  score: number;
  conviction: string | null;
  kelly_pct: number | null;
  win_prob: number | null;
  risk_reward: number | null;
  current_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  reasons: string[];
  sector: string | null;
  warning?: string;
}

export interface DiscoveryTopResponse {
  picks: DiscoveryPick[];
  n: number;
}

export async function fetchDiscoveryTop(
  sessionId: string,
  n = 5,
  signal?: AbortSignal,
) {
  return apiFetch<DiscoveryTopResponse>(
    `/ws/discovery/top?session_id=${encodeURIComponent(sessionId)}&n=${n}`,
    { signal },
  );
}

export async function fetchDiscoveryScan(
  sessionId: string,
  minScore = 6.0,
  signal?: AbortSignal,
) {
  return apiFetch<{ picks: DiscoveryPick[] }>(
    `/ws/discovery/scan?session_id=${encodeURIComponent(sessionId)}&min_score=${minScore}`,
    { signal },
  );
}

export interface WatchlistEntry {
  symbol: string;
  note: string | null;
  added_at: string | null;
}

export async function fetchWatchlistV2(
  sessionId: string,
  signal?: AbortSignal,
) {
  return apiFetch<{ watchlist: WatchlistEntry[] }>(
    `/ws/discovery/watchlist?session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

export async function addWatchlistV2(
  sessionId: string,
  symbol: string,
  note?: string,
) {
  return apiFetch<{ status: string; symbol: string }>(
    "/ws/discovery/watchlist",
    {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        symbol,
        note: note ?? null,
      }),
    },
  );
}

export async function removeWatchlistV2(
  sessionId: string,
  symbol: string,
) {
  return apiFetch<{ status: string; removed: string }>(
    `/ws/discovery/watchlist/${encodeURIComponent(symbol)}?session_id=${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}
