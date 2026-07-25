-- ============================================================================
-- Row-count parity — run on the NEW project, compare against the baseline you
-- saved from db/00_capture_live_state.sql step 8. Numbers should match (modulo
-- rows that arrived after the export snapshot, which the post-cutover re-sync
-- backfills).
-- ============================================================================
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
UNION ALL SELECT 'auth_users', count(*) FROM auth.users
ORDER BY t;

-- Credentials intact? Every active integration must still carry its token +
-- account id (this is what makes Meta/GHL/Stripe keep working with zero re-auth).
SELECT provider,
       count(*)                                              AS rows,
       count(*) FILTER (WHERE access_token IS NOT NULL)      AS with_token,
       count(*) FILTER (WHERE external_account_id IS NOT NULL) AS with_account
FROM integrations
WHERE status <> 'disconnected'
GROUP BY provider
ORDER BY provider;

-- Meta scope filters preserved (integrations.metadata.filter drives per-client
-- spend scoping; losing it double-counts shared ad accounts).
SELECT count(*) AS meta_rows_with_scope_filter
FROM integrations
WHERE provider = 'meta' AND metadata ? 'filter';

-- Config counts per client — spot-check a few against what the owner expects.
SELECT c.name,
       (SELECT count(*) FROM ghl_lead_types t WHERE t.client_id = c.id) AS lead_types,
       (SELECT count(*) FROM sale_types s     WHERE s.client_id = c.id) AS sale_types,
       (SELECT count(*) FROM custom_ratios r  WHERE r.client_id = c.id) AS custom_ratios,
       array_length(c.hidden_dashboard_metrics, 1)                      AS hidden_metrics
FROM clients c
WHERE c.is_archived = false
ORDER BY c.name;
