-- Monitor state table
-- Run once against Neon DB to enable the reliability + improvement loops.
--
-- Run with:
--   psql $DATABASE_URL -f db/migrate_monitor_state.sql

CREATE TABLE IF NOT EXISTS monitor_state (
  check_name        TEXT PRIMARY KEY,
  last_run_at       TIMESTAMPTZ,
  last_result       TEXT,          -- 'ok' | 'warn' | 'critical'
  consecutive_fails INT  DEFAULT 0,
  last_alerted_at   TIMESTAMPTZ,
  last_fixed_at     TIMESTAMPTZ,
  fix_attempts_24h  INT  DEFAULT 0,
  metadata          JSONB
);

COMMENT ON TABLE monitor_state IS
  'State for the two-loop monitor (Loop 1: reliability, Loop 2: improvement). '
  'One row per check (R1–R6, I1–I6). Persists across GitHub Actions runner teardowns.';
