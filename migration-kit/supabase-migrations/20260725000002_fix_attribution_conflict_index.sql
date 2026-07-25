-- Fix the dead attribution writer (DIAGNOSIS C1).
--
-- attribution.server.ts upserts with:
--     onConflict: "client_id,lead_id,transaction_id,match_type"
-- but the only unique index is an EXPRESSION index on COALESCE(lead_id,'0000..'),
-- COALESCE(transaction_id,'0000..'). Postgres cannot infer an expression index
-- from a plain column list, so every write raises 42P10, which the code swallows
-- (`if (!error) ...`). attribution_matches has therefore never been populated, and
-- every In-Period / Cohort ROAS panel computes over an empty table.
--
-- Replace the expression index with a NULLS NOT DISTINCT plain unique index
-- (Postgres 15+). It treats NULL lead_id/transaction_id as equal — exactly like
-- the COALESCE index — AND is inferable from the plain column list, so the
-- existing app code starts persisting attribution with NO code change.
--
-- Copy to: supabase/migrations/20260725000002_fix_attribution_conflict_index.sql
-- (already applied to the restored DB by migration-kit/db/02_post_restore.sql.)

DROP INDEX IF EXISTS public.uniq_attr_lead_txn;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_attr_lead_txn
  ON public.attribution_matches (client_id, lead_id, transaction_id, match_type)
  NULLS NOT DISTINCT;
