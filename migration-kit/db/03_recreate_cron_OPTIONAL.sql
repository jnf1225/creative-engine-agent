-- ============================================================================
-- OPTIONAL — only if you prefer DB-side pg_cron over the Railway worker.
--
-- The RECOMMENDED path is the Railway worker (worker/index.mjs), which removes
-- the URL coupling and needs no vault secret. Use THIS file only if you want to
-- keep scheduling inside Supabase (pg_cron + pg_net), mirroring how Lovable did it.
--
-- Fill in the placeholders from your db/00_capture_live_state.sql output.
-- ============================================================================

-- 1. Store the service-role key in Vault for the email dispatcher (value is not
--    exportable — paste your NEW project's service role key).
SELECT vault.create_secret(
  '<YOUR_NEW_SERVICE_ROLE_KEY>',
  'email_queue_service_role_key',
  'Service role key used by the process-email-queue cron job'
);
-- To rotate later: SELECT vault.update_secret((SELECT id FROM vault.secrets WHERE name='email_queue_service_role_key'), '<NEW_KEY>');

-- 2. Email queue dispatcher — every 10s, POST the Railway web app with the
--    service-role Bearer (matches process.ts auth).
SELECT cron.schedule(
  'process-email-queue',
  '10 seconds',
  $$
  SELECT net.http_post(
    url     := 'https://app.marketinglube.ai/lovable/email/queue/process',
    headers := jsonb_build_object(
      'content-type', 'application/json',
      'authorization', 'Bearer ' || (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'email_queue_service_role_key')
    ),
    body    := '{}'::jsonb
  );
  $$
);

-- 3. Ingest + precompute + analysis — POST the Railway web app with the private
--    CRON_SECRET (matches the migration _auth.ts x-cron-secret header).
--    Replace <CRON_SECRET> with your value.
SELECT cron.schedule('precompute-tiles', '*/2 * * * *', $$
  SELECT net.http_post('https://app.marketinglube.ai/api/public/hooks/precompute-tiles',
    headers := jsonb_build_object('content-type','application/json','x-cron-secret','<CRON_SECRET>'),
    body := '{}'::jsonb); $$);

SELECT cron.schedule('ingest-ghl', '*/15 * * * *', $$
  SELECT net.http_post('https://app.marketinglube.ai/api/public/hooks/ingest-ghl',
    headers := jsonb_build_object('content-type','application/json','x-cron-secret','<CRON_SECRET>'),
    body := '{}'::jsonb); $$);

SELECT cron.schedule('ingest-meta', '*/15 * * * *', $$
  SELECT net.http_post('https://app.marketinglube.ai/api/public/hooks/ingest-meta',
    headers := jsonb_build_object('content-type','application/json','x-cron-secret','<CRON_SECRET>'),
    body := '{}'::jsonb); $$);

SELECT cron.schedule('ingest-stripe', '*/30 * * * *', $$
  SELECT net.http_post('https://app.marketinglube.ai/api/public/hooks/ingest-stripe',
    headers := jsonb_build_object('content-type','application/json','x-cron-secret','<CRON_SECRET>'),
    body := '{}'::jsonb); $$);

SELECT cron.schedule('run-analysis', '0 8 * * *', $$
  SELECT net.http_post('https://app.marketinglube.ai/api/public/hooks/run-analysis',
    headers := jsonb_build_object('content-type','application/json','x-cron-secret','<CRON_SECRET>'),
    body := '{}'::jsonb); $$);

-- Verify:
SELECT jobname, schedule, active FROM cron.job ORDER BY jobname;
