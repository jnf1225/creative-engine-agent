# MarketingLube — Diagnosis (Issues Found Today)

_Analysis date: 2026-07-25. Source: full read of the uploaded codebase (TanStack Start + Lovable Cloud/Supabase, 64 migrations, ~130 source files). Every claim below is cited to `file:line` and was verified against the actual code._

---

## TL;DR — answering your three questions

1. **Is it Lovable's processing power?** Partly, but it's the amplifier, not the disease. Lovable Cloud's serverless functions and its PostgREST tier (a silent **1,000-row cap** on every query) force the app into slow workarounds — but those workarounds are the real problem.
2. **Is there code bloat?** No. The code is tidy and well-indexed. The disease is worse than bloat: **the app does all database work in JavaScript.** There is not a single `SUM`, `GROUP BY`, or aggregate query in the entire KPI engine — every dashboard number is produced by downloading raw rows into Node and looping. Confirmed: zero aggregate RPCs in the live function list (`src/integrations/supabase/types.ts:2367`).
3. **Is Railway + Claude Code better?** Yes — but **sequence it right.** Move first, refactor second. Porting the code as-is to Railway buys ~2–3× from losing the PostgREST tax; the architecture rewrite is what turns tens-of-seconds dashboards into sub-second. Details in `MIGRATION_PLAN.md`.

The original diagnosis you were given was **accurate**. This pass confirmed all of it and found **nine additional issues** — several of which are silent data-correctness bugs that change the numbers your clients see, plus **two that would break a naïve migration**.

---

## Part A — Why it "can't keep up" (the death spiral, confirmed)

For a client like ALIV (hundreds of leads/day, ~50k accumulated lead rows, millions/mo revenue), a **single uncached dashboard load** costs roughly **115–190 sequential PostgREST HTTP round-trips and 50MB–1GB of JSON** transferred into Node heap (`dashboard-tiles.server.ts:271-914`). Breakdown for an all-time view:

- The lead-tiles query selects `metadata,raw` — the **entire GHL webhook payload** (5–30KB each) — just to *count* leads (`dashboard-tiles.server.ts:424`).
- All-history **transactions are fetched three times** per compute (in-range + CPA block + ROAS block; two of them all-history) (`:532`, `:580`, `:713`).
- **Every** `ghl_leads` row is re-fetched with no date filter for the ROAS/attribution index (`:652`).
- Lead-type matching runs `evaluateLeadType` + `evaluateWebhookRules` **twice per lead per type** in JS (`:460-472`).
- All of it paginated 1,000 rows at a time through `fetchAllRows` (`supabase-paginate.ts`), sequential, capped at 200k rows.

Then three design decisions form a self-reinforcing loop:

1. **GHL cron re-downloads full history every cycle.** The scheduled ingest calls `ingestGhlClient(clientId)` with **no `since`** — even though the function fully supports incremental sync and the on-demand path uses it (`ingest-ghl-client.ts:13` vs `dashboard.functions.ts:126`). Every cycle re-pulls ALIV's entire contact history at 100/call and re-upserts all 50k rows.
2. **The precompute warmer skips *everyone* when *anyone* is syncing.** The guard is global, not per-client: if any `ingestion_runs` row is `running` (within a 12-min window), the whole fan-out returns `skipped` (`precompute-tiles.ts:42-53`). ALIV's long full-history sync keeps that flag lit, so nobody's cache gets warmed.
3. **The warmer can't finish the expensive presets anyway.** `MAX_RUNTIME_MS = 45_000` stops starting new presets after 45s (`precompute-tiles-client.ts:17`); "all-time" is last in the list and never gets cached — the exact preset that is most expensive to compute live.

Net effect: caches go stale (`CACHE_FRESH_MS = 2 min`), loads fall through to full live compute, live compute fights the sync for the same serverless CPU and the same 1,000-row PostgREST tier, the sync takes longer, the skip-guard fires more, and **ALIV starves every other client's cache too.** That's the "can't keep up."

---

## Part B — Confirmed issues from the original diagnosis

| # | Issue | Location | Status |
|---|-------|----------|--------|
| B1 | `fetchAllRows` = sequential 1,000-row HTTP paging, 200k ceiling, load-bearing for all analytics | `supabase-paginate.ts:19` | ✅ Confirmed |
| B2 | Tiles query selects full `raw`+`metadata` payloads just to count leads | `dashboard-tiles.server.ts:424` | ✅ Confirmed |
| B3 | ROAS block fetches ALL `ghl_leads` with no date filter | `dashboard-tiles.server.ts:652` | ✅ Confirmed |
| B4 | All-history transactions fetched **twice** (CPA + ROAS), plus a third in-range fetch | `:580`, `:713`, `:532` | ✅ Confirmed |
| B5 | O(leads × types) rule evaluation in JS, twice, after in-memory `mergeLeads` | `:440-472` | ✅ Confirmed |
| B6 | Synchronous inline Meta Graph sync **inside** the dashboard request | `:161`, `:263` | ✅ Confirmed |
| B7 | `CACHE_FRESH_MS = 2 min`; stale → full live compute | `:160` | ✅ Confirmed |
| B8 | GHL cron passes no `since` → full-history re-download every cycle | `ingest-ghl-client.ts:13` | ✅ Confirmed |
| B9 | Precompute skips ALL clients when ANY sync is running (global guard) | `precompute-tiles.ts:42` | ✅ Confirmed |
| B10 | `MAX_RUNTIME_MS = 45s` caps the warmer; expensive presets never cached | `precompute-tiles-client.ts:17` | ✅ Confirmed |
| B11 | `attribution.server.ts`: fbclid → `ads[0]` at 0.95 conf; `.limit(5000)` unordered | `attribution.server.ts:32`, `:67` | ✅ Confirmed |
| B12 | Zero SQL aggregation for any KPI value | `dashboard-tiles.server.ts` (whole file) | ✅ Confirmed |

One refinement on B12: there is exactly **one** SQL-side aggregate in the path — a `count: exact, head: true` existence check for `hasSaleSource` (`:333`). It computes no KPI. "Zero aggregation for the numbers" is correct.

---

## Part C — NEW issues found today (not in the original diagnosis)

These are the ones that matter most, because several are **silent correctness bugs** — the dashboard shows wrong numbers with no error — and two would **break a migration** if you replay migrations instead of restoring a live dump.

### 🔴 CRITICAL

**C1 — Attribution has never persisted a single row (silent 42P10).**
`attribution.server.ts:98` upserts with `onConflict: "client_id,lead_id,transaction_id,match_type"` (a plain column list), but the only unique index on the table is an **expression** index: `uniq_attr_lead_txn ON (client_id, COALESCE(lead_id,'0000…'), COALESCE(transaction_id,'0000…'), match_type)` (`migration 20260707213952:199`). Postgres cannot infer an expression index from a plain column list, so the statement errors with `42P10`. Line 102 is `if (!error) inserted = inserts.length;` — **the error is swallowed with no log.** `runAttribution` always returns `{matches: 0}` and `attribution_matches` stays empty.
Downstream, `attribution-summary.functions.ts:156` reads that (empty) table to build **In-Period ROAS and Cohort ROAS/LTV** panels shown to clients — so those panels compute over nothing. The hardcoded Aurenza rule (`inbound-webhook-handler.server.ts:377`) uses the same broken conflict target. **Verify against the live DB** — it's possible Lovable added a plain unique index out-of-band — but the code as written cannot work.

**C2 — `first_seen_at` exists in no migration → replaying migrations breaks the app.**
`grep` of `supabase/migrations/` finds **zero** occurrences of `first_seen_at`, yet the generated types declare it non-null on `ghl_leads` (`types.ts:1170`) and it drives the tiles attribution index (`dashboard-tiles.server.ts:657`), the in-period "new customer" gate (`:752`), and the cohort model (`attribution-summary.functions.ts:145`). It was added to the live DB out-of-band. **Two failure modes for the migration:** (1) rebuilding via `supabase db reset`/migration replay yields a schema *without* the column → tile compute and attribution error out; (2) if you re-ingest instead of copy the data, every historical lead gets `first_seen_at = migration day` → every past customer looks "new" this month → In-Period ROAS and New-Customer counts massively inflated. **Mandate: restore the live pg_dump; never replay migrations.**

**C3 — Leftover `UNIQUE(client_id, ghl_contact_id)` collides GHL and webhook leads.**
The constraint from `20260707213952:113` was **never dropped** (verified — later migrations dropped other constraints on this table but not this one). Webhook leads can carry a `ghl_contact_id`. Two outcomes: (a) if a GHL row exists first, an inbound webhook upsert (`onConflict client_id,provider,external_id`) hits the *other* unique constraint → **500, lead permanently dropped** with its fbclid/UTMs; (b) if the webhook row exists first, the next GHL sync's upsert (`onConflict client_id,ghl_contact_id`) **overwrites the webhook row's fbclid/UTMs with GHL's nulls** (`ghl-ingest.server.ts:314,338`) → a Meta-attributed lead flips to "unknown" and vanishes from Meta lead counts.

### 🟠 HIGH

**C4 — Stripe refunds and disputes are never subtracted.** `stripe-ingest.server.ts:137` keeps `paid && status==='succeeded'` and stores `amount_cents = c.amount` (**gross**). Refunded charges keep `status: succeeded` with `amount_refunded > 0`; `amount_refunded` is never read (verified: no `refund`/`dispute` handling anywhere, no refund webhook). Every refunded/charged-back dollar stays in `transactions` forever and re-upserts unchanged. Reported revenue, ROAS, MER, and new-revenue are **systematically overstated** for an agency doing millions/month.

**C5 — Sale-type "name" rules evaluated without `raw` → silently zero.** Stripe rule trees resolve `kind:'name'` from `raw.calculated_statement_descriptor` (`stripe-rules.ts:143`), but both tile paths fetch transactions **without** `raw` (`dashboard-tiles.server.ts:532`, `:585`). A sale type "name contains ACME" where `description` is null (common) → matches nothing → **0 sales** shown for a type that has real revenue.

**C6 — Identity merge double-counts the same person.** `getIdentityKey` (`lead-merge.ts:103`) picks a single key — email *or* phone *or* fbclid — with no cross-field joining. A phone-only Meta lead-form submission and the same person's GHL contact (email+phone) get different keys → two groups → counted twice → apparent cost-per-lead halved. Steady-state overcount at scale, not an edge case.

**C7 — `webhook_sales` purchase events counted as leads.** The tiles lead query has no provider filter (`dashboard-tiles.server.ts:419`), so sales-webhook rows (`provider='webhook_sales'`) inflate "Uncategorized Leads" — even though the viewer Lead Feed deliberately excludes them (`viewer.functions.ts:368`). Tile counts become unreconcilable with the feed.

**C8 — Meta ingest upserts a whole level in one unchunked request.** `meta-ingest.server.ts:310` sends all rows for a level in a single PostgREST call (GHL/Stripe chunk at 500; this doesn't). A large all-time backfill (campaign+adset+ad × daily × 37 months) builds a hundreds-of-MB JSON body → request fails → `catch` marks the integration `status='error'` → **spend tile goes blank** despite all the Graph API calls being paid for.

**C9 — Stripe cron re-downloads a fixed 90-day window every cycle and caps at 20k charges.** `stripe-ingest.server.ts:38` (`MAX_PAGES=200 × 100`). Stripe returns newest-first, so a client doing >20k charges/90 days silently **drops the oldest** of the window every run — those charges age out without ever being ingested. Also makes full historical backfills silently truncate to the newest 20k.

### 🟡 MEDIUM (perf/scale — get worse on a single always-on Railway process)

- **C10 — GHL opportunity sync is N+1 writes + full re-download.** One `await update().eq("id", …)` per matched lead (`ghl-ingest.server.ts:524`), after re-pulling all opportunities with no `since`, on **every** `ingestGhlClient` call. Minutes per sync for large clients.
- **C11 — Ingest fan-outs run all clients concurrently with no cap and no overlap guard.** `Promise.all(ids.map(fetch(...-client)))` in `ingest-ghl.ts:18`, `ingest-meta.ts:22`, `ingest-stripe.ts:18`, `run-analysis.ts:17`. On serverless this spreads across instances; **on one Railway Node process it's 50 simultaneous full syncs sharing one heap/event loop with live dashboard traffic.** No `ingestion_runs` running-check here (only precompute has one) → a slow sync + next tick = same client syncing twice, racing upserts.
- **C12 — No single-flight lock on cache miss.** N concurrent viewers past the 2-min window each run the full compute; viewer/share paths never *write* the cache (they pass no `preset`), so public share links (`share.functions.ts:539`) and the 5-min admin refetch each trigger full recomputes.
- **C13 — `getLeadTypeBreakdown` has no pagination** (`ghl.functions.ts:842`) → silently truncated at 1,000 rows → breakdown card disagrees with tiles for any client >1,000 leads.
- **C14 — Adset-scoped Meta sync leaves stale unscoped campaign rows** outside the 7-day refresh window (`meta-ingest.server.ts:318`) → dashboard mixes scoped and full-account spend across a date range.
- **C15 — GHL v2 pagination drops contacts sharing the oldest `dateAdded`** at the 10k-session boundary (`ghl-ingest.server.ts:222`) → bulk-imported contacts silently missing, `ingestion_run` still "success".

### 🔒 SECURITY (fix during the migration, since you're recreating this plumbing anyway)

- **S1 — Privileged hook endpoints are gated by the *public* browser key.** `_auth.ts:8` compares against `SUPABASE_PUBLISHABLE_KEY` — the exact value shipped as `VITE_SUPABASE_PUBLISHABLE_KEY` in the JS bundle (verified `client.ts:34`). Anyone can read it from your site and POST to `/api/public/hooks/run-analysis` to **fan out paid LLM calls across every client in every workspace**, or force GHL/Meta/Stripe re-ingestion into rate-limit lockout. → Replace with a private `CRON_SECRET`.
- **S2 — Integration credentials are stored plaintext at rest.** `integrations.access_token` holds raw Stripe `sk_`/`rk_` keys, GHL API keys, and Meta OAuth tokens in plaintext. **CORRECTION (verified against full migration history):** an earlier draft said "any workspace member can read these via RLS." That is **wrong** for the current schema — migration `20260707213952:35` created a member-read SELECT policy, but migration `20260707222120` **dropped it**, leaving only "workspace admins manage integrations" (owner/admin, `FOR ALL`). So Viewer/Member roles **cannot** read tokens via the API; only Owner/Admin can (which is expected). The remaining S2 concern is only **plaintext at rest** — anyone with raw database or service-role access (a DB dump, a leaked service-role key) sees the keys in the clear. Mitigation is encryption-at-rest (Supabase Vault / pgsodium), a hardening nice-to-have, not a live tenant-isolation hole. Webhook signing secrets (`client_lead_webhooks.secret`) are likewise plaintext but member-readable via `revealLeadWebhookSecret` (`lead-webhooks.functions.ts:141`) — that one IS member-accessible, so treat webhook secrets as visible to any workspace member.
- **S3 — Meta OAuth state secret falls back to a hardcoded `'dev-fallback-secret'`** (`meta-oauth.server.ts:6`). If `META_APP_SECRET` is ever unset on Railway, an attacker can forge OAuth `state` and attach their own Meta ad account/token into a victim tenant's client. **Set `META_APP_SECRET` before first boot; remove the fallback.**
- **S4 — Hook workers act on attacker-supplied `clientId` with no ownership binding** (`run-analysis-client.ts:9` et al.) → with S1's public key, targeted per-client cost abuse.
- **S5 — `sendTestLeadWebhookEvent` SSRF** — server fetches a caller-supplied URL (`lead-webhooks.functions.ts:264`).
- **S6 — `resolveShareToken` discloses client name + currency pre-auth** for restricted shares (`share.functions.ts:473`).

---

## Part D — What this means for the migration (the load-bearing facts)

1. **The schema does NOT restore onto plain Railway Postgres as-is.** Hard Supabase couplings: ~106 RLS policies on `auth.uid()`/`auth.role()`, 17 FKs + a signup trigger on `auth.users`, grants to `anon`/`authenticated`/`service_role`, and four extensions (`pg_cron`, `pg_net`, `supabase_vault`, `pgmq`). → **Target your own Supabase project, not bare Railway PG.** (See `MIGRATION_PLAN.md` for why this still gives you the perf win.)
2. **You must restore the LIVE database dump, not replay the 64 migrations.** Migrations both *fail mid-sequence* (a `REVOKE` on out-of-band functions `email_queue_dispatch`/`email_queue_wake` at `20260720233336:13`; a hardcoded client-UUID insert with an FK at `20260722171138:20`) **and omit live objects** (`first_seen_at`, the pgmq wrappers, the cron jobs, the vault secret). Lovable's official **Export project data** produces exactly the pg_dump you want (schema + data + RLS + `auth.users` with password hashes + identities).
3. **The cron schedules are invisible.** No migration schedules the ingest/precompute/email crons — they live only in the live DB's `cron.job` table (created out-of-band). **Run `SELECT jobname, schedule, command FROM cron.job` on the live DB before cutover** or ingestion, tile-warming, attribution, and email dispatch silently stop.
4. **Your three integrations survive as pure data.** Meta long-lived tokens, GHL API keys + location IDs, and Stripe restricted keys all live in `integrations` (+ `integrations_backup`) as plaintext rows. Nothing is registered on the providers' side against your hostname (no Stripe webhooks, no GHL platform webhooks — both are pull-based). **Move the rows byte-for-byte and all three keep working with zero re-auth.**
5. **Lead/sale type filters and dashboard config are pure DB rows** (`ghl_lead_types`, `sale_types`, `custom_ratios`, and ~20 config columns on `clients`) — **provided you preserve UUIDs** (`custom_ratios.expression` and `clients.hidden_dashboard_metrics` reference lead/sale-type UUIDs inside JSON; `lead_notes` key on `ghl_leads.id`). A logical dump preserves them; never re-insert with new IDs.
6. **Everything external points at `app.marketinglube.ai`.** Inbound webhook URLs (in GHL workflows + landing-page snippets), share links (in clients' inboxes), and the Meta OAuth callback are all built from the browsing origin at issuance time and were frozen as `app.marketinglube.ai`. **Keep that domain and point its DNS at Railway → every external URL survives untouched.** The only exceptions are any URLs copied while on a `*.lovable.app` preview host (audit `lead_webhook_events` history).
7. **Logins survive.** There are **no passwords** — auth is a custom 6-digit email OTP + Google OAuth. OTP users migrate via `auth.users` (same UUIDs) and just request a fresh code; Google needs your own Google OAuth client but re-links to the same accounts by verified email.
8. **Five Lovable platform services must be swapped** (host-independent — they break regardless of DNS): Google OAuth broker (`@lovable.dev/cloud-auth-js`), outbound email (`@lovable.dev/email-js` + `LOVABLE_SEND_URL`), the auth-email webhook, the AI gateway (`ai.gateway.lovable.dev`, Gemini 2.5 Flash), and the `LOVABLE_API_KEY` OTP-hash pepper. The build output also defaults to a **Cloudflare Workers target** and must be switched to a Node server preset.

---

_Full remediation sequencing (what to fix during the 2-hour cutover vs. the week-1 refactor vs. backlog) is in `MIGRATION_PLAN.md` §7._
