-- ============================================================================
-- Dangkor Flood Forecast — Supabase schema v0.1
-- ============================================================================
-- Run this ONCE in the Supabase SQL Editor (New Query → paste → Run).
-- Creates 6 tables. Safe to re-run: uses IF NOT EXISTS / IF EXISTS guards.
-- ============================================================================

-- ----- 1. runs -------------------------------------------------------------
-- One row per pipeline execution (every 6h cron).
create table if not exists runs (
    run_id              text primary key,
    engine_version      text        not null,
    forecast_issued_at  timestamptz not null,
    horizon_hours       int         not null,
    timestep_hours      int         not null,
    ensemble_size       int         not null,
    models_used         text[]      not null,
    regional_signals    jsonb,
    summary             jsonb,
    created_at          timestamptz not null default now()
);

create index if not exists runs_issued_at_idx on runs (forecast_issued_at desc);

-- ----- 2. predictions ------------------------------------------------------
-- Per-cell per-run forecast. ~1,956 rows per run × 4/day = ~235k rows/month.
create table if not exists predictions (
    run_id            text    not null references runs(run_id) on delete cascade,
    grid_id           text    not null,
    commune_id        text,
    commune_name      text,
    centroid_lat      double precision,
    centroid_lon      double precision,
    peak_level        int     not null,
    peak_probability  double precision not null,
    peak_time         timestamptz,
    window_peaks      jsonb   not null,
    timeseries        jsonb   not null,
    primary key (run_id, grid_id)
);

create index if not exists predictions_grid_idx    on predictions (grid_id);
create index if not exists predictions_commune_idx on predictions (commune_id);
-- Partial index: queries for "where is the risk right now?" are cheap.
create index if not exists predictions_hotspot_idx on predictions (peak_level)
    where peak_level >= 3;

-- ----- 3. commune_predictions ---------------------------------------------
-- Pre-aggregated per-run per-commune. 13 rows per run — small + fast for UI.
create table if not exists commune_predictions (
    run_id                text not null references runs(run_id) on delete cascade,
    commune_id            text not null,
    commune_name          text not null,
    cell_count            int  not null,
    peak_level            int  not null,
    peak_probability      double precision not null,
    cells_l2_plus         int  not null,
    cells_l3_plus         int  not null,
    cells_l4              int  not null,
    affected_pct_l2_plus  double precision not null,
    affected_pct_l3_plus  double precision not null,
    primary key (run_id, commune_id)
);

create index if not exists commune_peak_idx on commune_predictions (commune_id, peak_level);

-- ----- 4. observations -----------------------------------------------------
-- Ground truth — backfilled later from GPM IMERG, Sentinel-1, manual reports.
-- Used for calibration (Brier score, reliability curves, CN tuning).
create table if not exists observations (
    id            bigserial primary key,
    observed_at   timestamptz not null,
    source        text        not null,   -- 'gpm_imerg' | 'sentinel1' | 'manual' | 'news' | 'gsmap'
    grid_id       text,
    commune_id    text,
    rainfall_mm   double precision,
    water_level_m double precision,
    flood_flag    boolean,
    notes         text,
    created_at    timestamptz not null default now()
);

create index if not exists observations_time_idx    on observations (observed_at desc);
create index if not exists observations_grid_idx    on observations (grid_id);
create index if not exists observations_commune_idx on observations (commune_id);

-- ----- 5. tg_users (Telegram phase — schema ready now) ---------------------
create table if not exists tg_users (
    telegram_id       bigint primary key,
    language          text        default 'en',
    alert_threshold   int         default 3,     -- L2 | L3 | L4
    created_at        timestamptz default now(),
    last_active_at    timestamptz
);

-- ----- 6. tg_locations -----------------------------------------------------
-- Max 5 per user enforced at application layer.
create table if not exists tg_locations (
    id            bigserial primary key,
    telegram_id   bigint not null references tg_users(telegram_id) on delete cascade,
    label         text,
    lat           double precision not null,
    lon           double precision not null,
    grid_id       text,
    commune_id    text,
    created_at    timestamptz default now()
);
create index if not exists tg_locations_user_idx on tg_locations (telegram_id);

-- ----- 7. tg_alerts_sent ---------------------------------------------------
-- Dedup ledger so a single level transition doesn't double-alert.
create table if not exists tg_alerts_sent (
    id              bigserial primary key,
    telegram_id     bigint    not null,
    location_id     bigint,
    run_id          text,
    level_triggered int,
    sent_at         timestamptz default now()
);
create index if not exists tg_alerts_recent_idx on tg_alerts_sent (telegram_id, sent_at desc);

-- ============================================================================
-- Row-Level Security (RLS) — anon role can READ runs/predictions/communes for
-- dashboard; service_role (used from the pipeline) bypasses RLS entirely.
-- ============================================================================
alter table runs                enable row level security;
alter table predictions         enable row level security;
alter table commune_predictions enable row level security;
alter table observations        enable row level security;
alter table tg_users            enable row level security;
alter table tg_locations        enable row level security;
alter table tg_alerts_sent      enable row level security;

-- Public read policies (anon key) — dashboard + external researchers
drop policy if exists "public read runs"                on runs;
drop policy if exists "public read predictions"         on predictions;
drop policy if exists "public read commune_predictions" on commune_predictions;
drop policy if exists "public read observations"        on observations;
create policy "public read runs"                on runs                for select using (true);
create policy "public read predictions"         on predictions         for select using (true);
create policy "public read commune_predictions" on commune_predictions for select using (true);
create policy "public read observations"        on observations        for select using (true);

-- tg_* tables: NO anon access (Telegram data is private). service_role only.

-- ============================================================================
-- Convenience view: latest run's commune ranking (for a read-only public API)
-- ============================================================================
create or replace view v_latest_commune_ranking as
select cp.*, r.forecast_issued_at
from commune_predictions cp
join runs r on r.run_id = cp.run_id
where r.run_id = (select run_id from runs order by forecast_issued_at desc limit 1)
order by cp.peak_level desc, cp.peak_probability desc;

-- Done. Verify with:  select count(*) from runs;
