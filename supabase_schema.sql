-- aifolimizer — Supabase schema
-- Run this in the Supabase SQL editor to set up tables.
-- RLS enabled on all tables. No PII stored anywhere.

-- Enable UUID extension
create extension if not exists "pgcrypto";

-- ─── user_profiles ───────────────────────────────────────────────────────────
-- Stores which WS account types the user holds (e.g. TFSA, RRSP).
-- No names, emails, account numbers, or WS IDs.
create table if not exists user_profiles (
    id              uuid primary key default gen_random_uuid(),
    supabase_user_id uuid references auth.users(id) on delete cascade,
    ws_account_types text[] not null default '{}',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

alter table user_profiles enable row level security;

create policy "Users can read own profile"
    on user_profiles for select
    using (auth.uid() = supabase_user_id);

create policy "Users can upsert own profile"
    on user_profiles for insert
    with check (auth.uid() = supabase_user_id);

create policy "Users can update own profile"
    on user_profiles for update
    using (auth.uid() = supabase_user_id);

-- ─── portfolio_snapshots ──────────────────────────────────────────────────────
-- PII-filtered holdings saved on each portfolio fetch.
-- holdings_json is the same filtered format sent to Claude — no PII.
create table if not exists portfolio_snapshots (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references user_profiles(id) on delete cascade,
    snapshot_date   date not null default current_date,
    holdings_json   jsonb not null,
    total_value     numeric(14, 2),
    cash_available  numeric(14, 2),
    created_at      timestamptz not null default now()
);

create index if not exists idx_snapshots_user_date
    on portfolio_snapshots (user_id, snapshot_date desc);

alter table portfolio_snapshots enable row level security;

create policy "Users can read own snapshots"
    on portfolio_snapshots for select
    using (
        user_id in (select id from user_profiles where supabase_user_id = auth.uid())
    );

create policy "Users can insert own snapshots"
    on portfolio_snapshots for insert
    with check (
        user_id in (select id from user_profiles where supabase_user_id = auth.uid())
    );

-- ─── ai_recommendations ──────────────────────────────────────────────────────
-- Stores Claude's analysis output. Response text + metadata. No PII.
create table if not exists ai_recommendations (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references user_profiles(id) on delete cascade,
    analysis_type   text not null,
    model_used      text not null,
    response_text   text not null,
    health_score    smallint check (health_score between 0 and 100),
    created_at      timestamptz not null default now()
);

create index if not exists idx_recommendations_user_type
    on ai_recommendations (user_id, analysis_type, created_at desc);

alter table ai_recommendations enable row level security;

create policy "Users can read own recommendations"
    on ai_recommendations for select
    using (
        user_id in (select id from user_profiles where supabase_user_id = auth.uid())
    );

create policy "Users can insert own recommendations"
    on ai_recommendations for insert
    with check (
        user_id in (select id from user_profiles where supabase_user_id = auth.uid())
    );
