-- ============================================================================
-- POST-RESTORE reconciliation. Run against the NEW Supabase project AFTER
-- 01_restore.sh. Idempotent — safe to re-run during rehearsal.
-- ============================================================================

-- 1. Ensure the extensions the app depends on exist (the dump may not recreate
--    all of them cleanly on a fresh project).
CREATE EXTENSION IF NOT EXISTS pgcrypto;          -- client_shares.token default gen_random_bytes
CREATE EXTENSION IF NOT EXISTS pg_net SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS supabase_vault;
CREATE EXTENSION IF NOT EXISTS pgmq;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    CREATE EXTENSION pg_cron;
  END IF;
END $$;

-- 2. auth.identities FK repair pass (known Lovable-export trap). If the restore
--    loaded identities before their auth.users parents, orphans can linger.
--    Remove any identity with no matching user so logins resolve cleanly.
DELETE FROM auth.identities i
WHERE NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = i.user_id);

-- 3. Recreate the pgmq email queues if the dump didn't carry the pgmq schema
--    objects (they live in the pgmq schema, sometimes skipped by public-only dumps).
DO $$ BEGIN PERFORM pgmq.create('auth_emails');           EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN PERFORM pgmq.create('transactional_emails');  EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN PERFORM pgmq.create('auth_emails_dlq');        EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN PERFORM pgmq.create('transactional_emails_dlq'); EXCEPTION WHEN OTHERS THEN NULL; END $$;

-- 4. first_seen_at honesty (DIAGNOSIS C2). The column exists in the live DB (added
--    out-of-band) so a proper restore already has it WITH real values. This block
--    only self-heals if it is somehow missing, and NEVER overwrites existing
--    values (which would inflate "new customer" KPIs). It backdates a missing
--    column from the provider timestamp, not insert-time.
ALTER TABLE public.ghl_leads
  ADD COLUMN IF NOT EXISTS first_seen_at timestamptz;
UPDATE public.ghl_leads
  SET first_seen_at = COALESCE(ghl_created_at, created_at)
  WHERE first_seen_at IS NULL;
ALTER TABLE public.ghl_leads
  ALTER COLUMN first_seen_at SET DEFAULT now();
ALTER TABLE public.ghl_leads
  ALTER COLUMN first_seen_at SET NOT NULL;

-- 5. Attribution writer fix (DIAGNOSIS C1). The code upserts with
--    onConflict "client_id,lead_id,transaction_id,match_type" but the only unique
--    index is an EXPRESSION index on COALESCE(...), which Postgres cannot infer
--    from a plain column list -> 42P10 -> silently swallowed -> table stays empty.
--    Replace it with a NULLS-NOT-DISTINCT plain unique index (PG15+) that (a)
--    treats NULL lead_id/transaction_id as equal exactly like the COALESCE index
--    did, and (b) IS inferable from the column list, so the existing app code
--    starts persisting attribution with no code change.
DROP INDEX IF EXISTS public.uniq_attr_lead_txn;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_attr_lead_txn
  ON public.attribution_matches (client_id, lead_id, transaction_id, match_type)
  NULLS NOT DISTINCT;

-- 6. (OPTIONAL — leave commented unless you also apply the Tier-3 code fix)
--    ghl_contact_id collision (DIAGNOSIS C3). The leftover UNIQUE(client_id,
--    ghl_contact_id) collides GHL and webhook leads. Dropping it here without the
--    accompanying ingest-merge code change could allow duplicate GHL contacts, so
--    do NOT drop it during the migration — schedule it with the Tier-3 work.
-- ALTER TABLE public.ghl_leads DROP CONSTRAINT IF EXISTS ghl_leads_client_id_ghl_contact_id_key;

-- 7. Sanity: confirm the auth signup trigger survived (viewer/admin provisioning).
SELECT tgname FROM pg_trigger WHERE tgname = 'on_auth_user_created';

SELECT 'post_restore_ok' AS status;
