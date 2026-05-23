-- aifolimizer schema (TimescaleDB on PG16)
-- Initial migration. Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Tenants (one row per Wealthsimple session/user)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id   TEXT PRIMARY KEY,
  tenant_hash TEXT UNIQUE NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Signal history (per-tick integrated signal per holding)
-- Hypertable, compressed after 30d
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_history (
  id                   BIGSERIAL,
  tenant_hash          TEXT NOT NULL,
  symbol               TEXT NOT NULL,
  ts                   TIMESTAMPTZ NOT NULL,
  action               TEXT NOT NULL,
  conviction           TEXT,
  score                NUMERIC(4,2) NOT NULL,
  tech_score           NUMERIC(4,2),
  fund_score           NUMERIC(4,2),
  macro_score          NUMERIC(4,2),
  sentiment_score      NUMERIC(4,2),
  skill_consensus      INT,
  skill_confidence     NUMERIC(3,2),
  skill_evidence       JSONB,
  rsi                  NUMERIC,
  stage                INT,
  market_regime        TEXT,
  analyst_upside_pct   NUMERIC,
  weight               NUMERIC,
  signal_quality       NUMERIC,
  risk_reward          NUMERIC,
  kelly_pct            NUMERIC,
  win_prob             NUMERIC,
  earnings_risk        TEXT,
  realized_return_1d   NUMERIC,
  realized_return_5d   NUMERIC,
  realized_return_21d  NUMERIC,
  realized_return_63d  NUMERIC,
  weights_version      INT,
  PRIMARY KEY (tenant_hash, symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_signal_history_symbol_ts ON signal_history (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signal_history_tenant_ts ON signal_history (tenant_hash, ts DESC);

-- ---------------------------------------------------------------------------
-- Recommendations (one row per logged rec)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recommendations (
  id                  BIGSERIAL PRIMARY KEY,
  tenant_hash         TEXT NOT NULL,
  date                DATE NOT NULL,
  ts                  TIMESTAMPTZ NOT NULL,
  skill               TEXT NOT NULL,
  model_version       TEXT NOT NULL,
  ticker              TEXT NOT NULL,
  action              TEXT NOT NULL,
  conviction          TEXT NOT NULL,
  horizon_days        INT,
  thesis              TEXT,
  invalidation        TEXT,
  entry_price         NUMERIC,
  target_pct          NUMERIC,
  stop_pct            NUMERIC,
  expected_upside_pct NUMERIC,
  expected_downside_pct NUMERIC,
  account             TEXT,
  sector_etf          TEXT,
  benchmark_symbol    TEXT,
  benchmarks_entry    JSONB,
  features            JSONB,
  rationale_hash      TEXT,
  status              TEXT DEFAULT 'open',
  exit_price          NUMERIC,
  exit_date           DATE,
  return_pct          NUMERIC,
  win                 BOOLEAN,
  UNIQUE (tenant_hash, date, skill, ticker, action)
);
CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations (status, date);
CREATE INDEX IF NOT EXISTS idx_recommendations_tenant ON recommendations (tenant_hash, date DESC);

-- ---------------------------------------------------------------------------
-- Skill snapshots (per-skill output per tick, replaces .cache/skill_snapshots/)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_snapshots (
  id           BIGSERIAL PRIMARY KEY,
  tenant_hash  TEXT NOT NULL,
  skill        TEXT NOT NULL,
  computed_at  TIMESTAMPTZ NOT NULL,
  expires_at   TIMESTAMPTZ NOT NULL,
  status       TEXT NOT NULL,
  ttl_minutes  INT,
  summary      JSONB,
  actionable   JSONB,
  alerts       JSONB,
  key_insights JSONB,
  error        TEXT,
  UNIQUE (tenant_hash, skill, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_skill_snapshots_latest
  ON skill_snapshots (tenant_hash, skill, computed_at DESC);

-- ---------------------------------------------------------------------------
-- Weights (5 sub-signal weights, audit-versioned)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weights (
  version     SERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ DEFAULT now(),
  w_tech      NUMERIC(3,2) NOT NULL,
  w_fund      NUMERIC(3,2) NOT NULL,
  w_macro     NUMERIC(3,2) NOT NULL,
  w_sentiment NUMERIC(3,2) NOT NULL,
  w_skill     NUMERIC(3,2) NOT NULL,
  reason      TEXT,
  objective   TEXT,
  attribution JSONB
);

-- Seed initial weights row (v3 baseline; w_skill=0.0 for Phase 1)
INSERT INTO weights (w_tech, w_fund, w_macro, w_sentiment, w_skill, reason, objective)
SELECT 1.0, 1.0, 1.0, 1.0, 0.0, 'initial', 'baseline'
WHERE NOT EXISTS (SELECT 1 FROM weights);

-- ---------------------------------------------------------------------------
-- Signal changes (every flip/conviction-step/score-move detected)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_changes (
  id              BIGSERIAL,
  tenant_hash     TEXT NOT NULL,
  symbol          TEXT NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,
  prev_action     TEXT,
  new_action      TEXT,
  prev_conviction TEXT,
  new_conviction  TEXT,
  prev_score      NUMERIC,
  new_score       NUMERIC,
  reasons         TEXT[],
  pushed          BOOLEAN DEFAULT false,
  push_dedup_key  TEXT,
  PRIMARY KEY (tenant_hash, symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_signal_changes_dedup ON signal_changes (push_dedup_key);

-- ---------------------------------------------------------------------------
-- Alerts (existing alerts.py jsonl → here)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
  id           BIGSERIAL,
  tenant_hash  TEXT,
  ts           TIMESTAMPTZ NOT NULL,
  rule         TEXT NOT NULL,
  symbol       TEXT,
  severity     TEXT,
  title        TEXT,
  body         TEXT,
  pushed       BOOLEAN,
  dedup_key    TEXT,
  PRIMARY KEY (id, ts)
);
CREATE INDEX IF NOT EXISTS idx_alerts_tenant_ts ON alerts (tenant_hash, ts DESC);

-- ---------------------------------------------------------------------------
-- Crowding history (positioning snapshots, daily)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crowding_history (
  ts     TIMESTAMPTZ NOT NULL,
  symbol TEXT NOT NULL,
  score  NUMERIC(5,2) NOT NULL,
  label  TEXT NOT NULL,
  PRIMARY KEY (ts, symbol)
);

-- ---------------------------------------------------------------------------
-- Portfolio equity (daily NAV)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_equity (
  tenant_hash     TEXT NOT NULL,
  date            DATE NOT NULL,
  total_value_cad NUMERIC NOT NULL,
  cash_cad        NUMERIC,
  PRIMARY KEY (tenant_hash, date)
);

-- ---------------------------------------------------------------------------
-- Regime history (Phase 8)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regime_history (
  ts               TIMESTAMPTZ NOT NULL,
  trend            TEXT,
  volatility       TEXT,
  breadth          TEXT,
  macro            TEXT,
  composite        TEXT NOT NULL,
  confidence       NUMERIC(3,2),
  vix              NUMERIC,
  spy_vs_sma200_pct NUMERIC,
  ten_y_yield      NUMERIC,
  fed_funds        NUMERIC,
  PRIMARY KEY (ts)
);

-- Regime-skill multipliers (Phase 8, mutated by nightly tuner in Phase 11)
CREATE TABLE IF NOT EXISTS regime_skill_multipliers (
  id                 BIGSERIAL PRIMARY KEY,
  ts                 TIMESTAMPTZ DEFAULT now(),
  skill              TEXT NOT NULL,
  regime_composite   TEXT NOT NULL,
  multiplier         NUMERIC(3,2) NOT NULL,
  n_samples          INT,
  source             TEXT,
  UNIQUE (skill, regime_composite, ts)
);

-- ---------------------------------------------------------------------------
-- Calibration reports (Phase 9)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calibration_reports (
  id           BIGSERIAL PRIMARY KEY,
  ts           TIMESTAMPTZ DEFAULT now(),
  horizon_days INT,
  brier_score  NUMERIC(5,4),
  ece          NUMERIC(5,4),
  verdict      TEXT,
  bins         JSONB
);

-- ---------------------------------------------------------------------------
-- Live KPI snapshots (Phase 10)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live_kpi_snapshots (
  ts                  TIMESTAMPTZ NOT NULL,
  tenant_hash         TEXT NOT NULL,
  window_days         INT NOT NULL,
  expectancy_pct      NUMERIC(6,3),
  profit_factor       NUMERIC(5,2),
  sharpe              NUMERIC(5,2),
  sortino             NUMERIC(5,2),
  max_drawdown_pct    NUMERIC(5,2),
  hit_rate            NUMERIC(4,3),
  avg_win_pct         NUMERIC(5,3),
  avg_loss_pct        NUMERIC(5,3),
  n_trades            INT,
  after_cost_drag_bps NUMERIC(5,2),
  regime_breakdown    JSONB,
  PRIMARY KEY (tenant_hash, window_days, ts)
);

-- ---------------------------------------------------------------------------
-- Risk gate events (Phase 12)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_gate_events (
  id              BIGSERIAL,
  tenant_hash     TEXT NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,
  status          TEXT NOT NULL,
  size_multiplier NUMERIC(3,2),
  reasons         TEXT[],
  triggers        JSONB,
  valid_until     TIMESTAMPTZ,
  PRIMARY KEY (id, ts)
);

-- ---------------------------------------------------------------------------
-- Watchlist + discovery scans (Phase 13)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist (
  tenant_hash TEXT NOT NULL,
  symbol      TEXT NOT NULL,
  added_at    TIMESTAMPTZ DEFAULT now(),
  note        TEXT,
  PRIMARY KEY (tenant_hash, symbol)
);

CREATE TABLE IF NOT EXISTS discovery_scans (
  id              BIGSERIAL,
  tenant_hash     TEXT NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,
  universe_size   INT,
  filtered_count  INT,
  pushed_count    INT,
  top_picks       JSONB,
  PRIMARY KEY (id, ts)
);

-- ---------------------------------------------------------------------------
-- Convert time-series tables to TimescaleDB hypertables
-- ---------------------------------------------------------------------------
SELECT create_hypertable('signal_history',     'ts',   if_not_exists => TRUE);
SELECT create_hypertable('signal_changes',     'ts',   if_not_exists => TRUE);
SELECT create_hypertable('alerts',             'ts',   if_not_exists => TRUE);
SELECT create_hypertable('crowding_history',   'ts',   if_not_exists => TRUE);
SELECT create_hypertable('portfolio_equity',   'date', if_not_exists => TRUE);
SELECT create_hypertable('regime_history',     'ts',   if_not_exists => TRUE);
SELECT create_hypertable('live_kpi_snapshots', 'ts',   if_not_exists => TRUE);
SELECT create_hypertable('risk_gate_events',   'ts',   if_not_exists => TRUE);
SELECT create_hypertable('discovery_scans',    'ts',   if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Compression policies
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  ALTER TABLE signal_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol,tenant_hash'
  );
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$
BEGIN
  ALTER TABLE crowding_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol'
  );
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

SELECT add_compression_policy('signal_history',   INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('crowding_history', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('signal_changes',   INTERVAL '90 days', if_not_exists => TRUE);
SELECT add_compression_policy('alerts',           INTERVAL '90 days', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Continuous aggregate: weekly accuracy report
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS signal_accuracy_weekly
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('7 days', ts) AS week,
  action,
  COUNT(*) AS n,
  AVG(realized_return_21d) AS avg_ret_21d,
  AVG(CASE WHEN realized_return_21d > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_21d
FROM signal_history
WHERE realized_return_21d IS NOT NULL
GROUP BY week, action
WITH NO DATA;

SELECT add_continuous_aggregate_policy('signal_accuracy_weekly',
  start_offset      => INTERVAL '90 days',
  end_offset        => INTERVAL '1 day',
  schedule_interval => INTERVAL '1 day',
  if_not_exists     => TRUE);
