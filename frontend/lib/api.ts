const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
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

export async function wsGetLlmStatus(session_id: string) {
  return apiFetch<{ available_providers: string[] }>(
    `/ws/llm-status?session_id=${session_id}`
  );
}

// ─── Types ────────────────────────────────────────────────────────────────────

export interface PriceHistory {
  symbol: string;
  period: string;
  dates: string[];
  close: number[];
  sma_50: (number | null)[];
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
  day_change_pct: number;
  total_return_pct: number;
  weight: number;
  asset_class: string;
  sector?: string;
}

export interface PortfolioSummary {
  total_value: number;
  total_cost: number;
  total_return_pct: number;
  cash_available: number;
  day_change_cad: number;
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
  action: "BUY" | "HOLD" | "WATCH" | "SELL";
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
