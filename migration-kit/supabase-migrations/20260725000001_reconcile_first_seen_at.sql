-- Make the schema honest about ghl_leads.first_seen_at (DIAGNOSIS C2).
-- This column was added to the live DB out-of-band and appears in NO prior
-- migration, so a fresh `supabase db reset` / migration replay produces a schema
-- WITHOUT it and the app breaks (tile attribution + cohort model reference it).
-- Add it here so migration replay reproduces the live schema.
--
-- Copy to: supabase/migrations/20260725000001_reconcile_first_seen_at.sql
-- (already applied to the restored DB by migration-kit/db/02_post_restore.sql;
--  this file keeps the repo history in sync for future resets.)

ALTER TABLE public.ghl_leads
  ADD COLUMN IF NOT EXISTS first_seen_at timestamptz;

-- Backdate from the provider timestamp, never insert-time (insert-time would make
-- every historical lead look "new this month" and inflate New-Customer / ROAS).
UPDATE public.ghl_leads
  SET first_seen_at = COALESCE(ghl_created_at, created_at)
  WHERE first_seen_at IS NULL;

ALTER TABLE public.ghl_leads
  ALTER COLUMN first_seen_at SET DEFAULT now();
ALTER TABLE public.ghl_leads
  ALTER COLUMN first_seen_at SET NOT NULL;
