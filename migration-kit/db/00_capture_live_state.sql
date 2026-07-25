-- ============================================================================
-- PHASE 0 — run against the LIVE Lovable Cloud database (read-only).
-- These objects exist ONLY in the live DB, never in the repo migrations, so you
-- must capture them before cutover or ingestion/tiles/email silently stop.
-- Connect via the Supabase SQL editor for project hepiiyrbwyyauffpoxfp, or psql
-- with the live connection string. Save ALL output.
-- ============================================================================

-- 1. The cron schedules (the whole point — these drive ingest/precompute/email).
--    Copy jobname + schedule + command verbatim; you'll mirror them in the
--    Railway worker (env/worker.env.example) or in db/03_recreate_cron.sql.
SELECT jobid, jobname, schedule, command, active
FROM cron.job
ORDER BY jobname;

-- 2. Vault secrets (names only — values are NOT exportable, you re-enter them).
--    Expect at least 'email_queue_service_role_key'.
SELECT name, description, created_at, updated_at
FROM vault.secrets
ORDER BY name;

-- 3. Extensions in use (target project must have these).
SELECT extname, extversion FROM pg_extension ORDER BY extname;

-- 4. Database size vs the 5GB export cap. If close, prune old ghl_leads.raw /
--    transactions.raw before the final export (the `raw` jsonb is the bloat).
SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;

SELECT relname AS table,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 20;

-- 5. ALIV sizing (answers MIGRATION_PLAN.md §11 Q1 — drives the Tier-2 plan).
--    Replace the slug if different.
SELECT c.name,
       (SELECT count(*) FROM ghl_leads    l WHERE l.client_id = c.id) AS leads,
       (SELECT count(*) FROM transactions t WHERE t.client_id = c.id) AS transactions,
       (SELECT count(*) FROM meta_ads_metrics m WHERE m.client_id = c.id) AS meta_rows
FROM clients c
WHERE c.name ILIKE '%aliv%'
   OR c.slug ILIKE '%aliv%';

-- 6. Does the attribution write actually work today? (DIAGNOSIS C1)
--    If this returns 0 rows, the onConflict/42P10 bug means attribution has
--    never persisted — confirm before deciding whether the C1 migration is
--    "revive it" or "it already worked via an out-of-band index".
SELECT count(*) AS attribution_matches_rows FROM attribution_matches;

-- 7. Confirm the out-of-band columns/objects the repo migrations DON'T contain,
--    so you know the restore (not migration replay) is mandatory.
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'ghl_leads'
  AND column_name = 'first_seen_at';  -- expect 1 row live; 0 rows if you replayed migrations

SELECT proname FROM pg_proc
WHERE proname IN ('email_queue_dispatch', 'email_queue_wake')
ORDER BY proname;  -- expect these live; absent in migrations

-- 8. Row-count baseline for the parity check (04_verify_parity.sql). Save this.
SELECT 'clients' t, count(*) FROM clients
UNION ALL SELECT 'integrations', count(*) FROM integrations
UNION ALL SELECT 'integrations_backup', count(*) FROM integrations_backup
UNION ALL SELECT 'ghl_leads', count(*) FROM ghl_leads
UNION ALL SELECT 'transactions', count(*) FROM transactions
UNION ALL SELECT 'meta_ads_metrics', count(*) FROM meta_ads_metrics
UNION ALL SELECT 'ghl_lead_types', count(*) FROM ghl_lead_types
UNION ALL SELECT 'sale_types', count(*) FROM sale_types
UNION ALL SELECT 'custom_ratios', count(*) FROM custom_ratios
UNION ALL SELECT 'client_lead_webhooks', count(*) FROM client_lead_webhooks
UNION ALL SELECT 'client_shares', count(*) FROM client_shares
UNION ALL SELECT 'client_share_allowed_emails', count(*) FROM client_share_allowed_emails
UNION ALL SELECT 'workspaces', count(*) FROM workspaces
UNION ALL SELECT 'workspace_members', count(*) FROM workspace_members
ORDER BY t;
