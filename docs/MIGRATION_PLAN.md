# MarketingLube — Migration Gameplan: Lovable Cloud → Railway + Claude Code

_Companion to `DIAGNOSIS.md`. This is the executable plan. It is written so you (or Claude Code) can follow it top-to-bottom. Nothing here has been executed yet — you asked for the plan only._

**Your four hard requirements, and where each is guaranteed:**

| Your requirement | How it's preserved | Section |
|---|---|---|
| 1. Rehost `app.marketinglube.ai` on Railway | Keep the domain, flip DNS to Railway last. Every external URL is domain-relative and survives. | §4, §6 |
| 2. Don't break connected Meta / GHL / Stripe | All credentials are plaintext rows in `integrations`; move the DB and they keep working. No provider holds a URL against your host. | §2, `DIAGNOSIS.md` D4 |
| 3. Keep all lead/sale-type filters + dashboard configs | Pure DB rows (`ghl_lead_types`, `sale_types`, `custom_ratios`, `clients.*`). Preserved by a UUID-preserving dump. | `DIAGNOSIS.md` D5 |
| 4. Don't redo the Meta App / GHL sub-accounts / Stripe API setup | No re-auth needed. Meta App config only matters for *new* connections; existing tokens are bearer tokens tied to the app, not the host. | §2, §5 |

**Downtime target: 2–4 hours,** achieved by doing everything additive *before* cutover and rehearsing the restore so the live window is just "final export → restore → repoint → DNS flip → verify."

---

## 1. The one strategic decision: where the database lives

You have three options. **Recommendation: Option B.**

| | A. App→Railway, DB stays on Lovable | **B. App→Railway, DB→your own Supabase project** ✅ | C. App→Railway, DB→bare Railway Postgres |
|---|---|---|---|
| Effort | Lowest | Medium | Highest |
| Really "off Lovable"? | No — still Lovable-billed, Lovable can pause it | **Yes** | Yes |
| Auth / RLS / pgmq / pg_cron | Keep, untouched | **Keep, untouched** | Must rip out RLS, build an auth shim, replace pgmq + pg_cron |
| Logins survive | Yes | **Yes (auth.users dumps with hashes+identities)** | Risky — auth schema must be reimplemented |
| Perf-refactor win available? | No (1,000-row PostgREST tier) | **Yes — you also get a direct Postgres connection string** | Yes |
| Migration risk | Low but doesn't solve the problem | **Low-moderate, solves the problem** | High |

**Why Option B is the sweet spot:** Your own Supabase project keeps PostgREST + RLS + Auth + pgmq + pg_cron working **exactly as the code expects** (near-zero rewrite of the data layer), *and* Supabase hands you a **direct Postgres connection string** (session pooler). That direct connection is all you need to do the SQL-aggregation refactor later — you do **not** need bare Railway Postgres to kill the 1,000-row cap. You get the clean exit and the performance ceiling without the auth/RLS rewrite that Option C forces. (You can also raise your project's `db-max-rows` above Lovable's 1,000 immediately.)

Everything below assumes **Option B**.

---

## 2. Target architecture

```
                    ┌──────────────────────────────────────────┐
   app.marketinglube.ai (your domain, DNS → Railway)           │
                    │                                            │
      ┌─────────────▼─────────────┐      ┌────────────────────┐ │
      │  Railway: web service     │      │ Railway: worker    │ │
      │  TanStack Start (Node)     │      │ (cron + queue)     │ │
      │  - SSR + API routes        │      │ - ingest GHL/Meta/ │ │
      │  - /api/public/* hooks      │◄────►│   Stripe           │ │
      │  - browser → PostgREST/RLS  │      │ - precompute tiles │ │
      └─────────────┬──────────────┘      │ - email queue drain│ │
                    │                     │ - persistent, no   │ │
                    │  direct PG (pooler) │   45s cap          │ │
                    ▼                     └─────────┬──────────┘ │
      ┌──────────────────────────────────────────┐ │            │
      │  Your own Supabase project                │◄┘            │
      │  Postgres + Auth + RLS + PostgREST         │             │
      │  + pgmq + pg_cron + vault                  │             │
      └──────────────────────────────────────────┘             │
                                                                 │
   External (unchanged, all point at app.marketinglube.ai):     │
     Meta OAuth callback · GHL/Zapier inbound webhooks ─────────┘
     Stripe: pull-only (no inbound URL) · client share links
```

**Two Railway services, one Supabase project.** Splitting the web service from a persistent worker is the single biggest structural improvement Railway buys you: the ingest/precompute/email jobs stop competing with live dashboard requests for the same process, and the worker has no 45-second serverless ceiling. If you want to move fast, you *can* start with one combined service and split later — but plan for two.

**Service swaps (host-independent, must happen regardless of DNS):**

| Lovable service | Replace with | File(s) |
|---|---|---|
| Google OAuth via `@lovable.dev/cloud-auth-js` | `supabase.auth.signInWithOAuth({provider:'google'})` + your own Google OAuth client in Supabase | `integrations/lovable/index.ts`, `auth.tsx:132`, `share.$token.tsx:144` |
| Outbound email `@lovable.dev/email-js` + `LOVABLE_SEND_URL` | **Resend** (or Postmark) — same html/text/subject/from payload | `routes/lovable/email/queue/process.ts:316-332` |
| Auth-email webhook (`/lovable/email/auth/webhook`) | Supabase "Send Email" auth hook → your endpoint, OR delete (the custom OTP flow already bypasses provider emails — `access.server.ts:290`) | `routes/lovable/email/auth/*` |
| AI gateway `ai.gateway.lovable.dev` (Gemini 2.5 Flash) | Direct Google Gemini API or any OpenAI-compatible endpoint (one-file swap — it's chat-completions shaped) | `ai-analyst.server.ts:4` |
| `LOVABLE_API_KEY` as OTP-hash pepper | A dedicated stable `CODE_PEPPER` env var | `access.server.ts:117` |
| Nitro **Cloudflare** target (default) | `NITRO_PRESET=node-server` (or stock TanStack Start config) so `vite build` emits a runnable Node entry | `vite.config.ts`, `server.ts` |
| Lovable error reporting (`window.__lovableEvents`) | Sentry (optional; it no-ops off Lovable) | `lovable-error-reporting.ts` |
| OG image on Lovable's R2 CDN | Move into `public/` | `__root.tsx:100` |

---

## 3. Environment variables for Railway

Set these on **both** the web and worker services (build-time `VITE_*` on the web service):

```
# Supabase (your NEW project)
SUPABASE_URL=https://<your-project>.supabase.co
VITE_SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<new anon/publishable key>
VITE_SUPABASE_PUBLISHABLE_KEY=<new anon/publishable key>
SUPABASE_SERVICE_ROLE_KEY=<new service role key>
DATABASE_URL=<session-pooler direct connection string>   # for the perf refactor

# App identity
APP_URL=https://app.marketinglube.ai
VITE_APP_URL=https://app.marketinglube.ai

# Meta — IDENTICAL values to today (do NOT rotate; tokens + OAuth state depend on them)
META_APP_ID=<unchanged>
META_APP_SECRET=<unchanged>          # also signs OAuth state — S3: never leave unset

# New secrets replacing Lovable services
CRON_SECRET=<new random 32+ bytes>   # replaces the public-key hook auth (S1)
CODE_PEPPER=<new stable secret>       # replaces LOVABLE_API_KEY pepper; keep old value for 3h overlap
RESEND_API_KEY=<...>                  # or POSTMARK_*
AI_API_KEY=<gemini or openai-compatible key>
AI_GATEWAY_URL=<endpoint>
```

**Gotchas:**
- Keep `META_APP_ID`/`META_APP_SECRET` **byte-identical** — existing Meta tokens are bearer tokens; changing the secret only breaks in-flight OAuth, but keep it to avoid surprises.
- `CODE_PEPPER`: outstanding OTP codes are peppered with `LOVABLE_API_KEY` for a 3-hour window (`access.server.ts:117`). Either cut over during a low-login window or keep `LOVABLE_API_KEY` available for that overlap.
- When you mint a new Supabase project, **all three keys change** — the cron headers and every `VITE_*` build var must move in lockstep.

---

## 4. What survives the DNS flip untouched (and the one thing to audit)

Every URL an external system holds is `{origin}/path` with `origin` frozen at copy/send time from `window.location.origin`. Because your users copied them while on `app.marketinglube.ai`, **repointing that domain's DNS at Railway preserves all of them:**

- **GHL / Zapier inbound webhooks:** `https://app.marketinglube.ai/api/public/{leads,sales}/inbound/{token}` — tokens + HMAC secrets live in `client_lead_webhooks` (moves with the DB). The route path and the `x-lube-signature` HMAC-SHA256 scheme must stay byte-identical (they will — same code).
- **Meta OAuth callback:** `https://app.marketinglube.ai/api/public/meta/callback` — already whitelisted in your Meta App. Needed only for *new/reconnect* flows; existing tokens don't touch it.
- **Client share links + viewer login links:** `https://app.marketinglube.ai/share/{token}`, `/invite/{token}`, `/auth` — tokens in `client_shares` / `workspace_invites` (move with the DB).
- **Stripe:** zero URL changes — pull-only, no webhook endpoint exists.

**The one audit:** any webhook/share URL that was copied while the admin was browsing a `*.lovable.app` or `*.lovableproject.com` preview host is frozen to *that* host and will break. Check `lead_webhook_events` payload history for non-`app.marketinglube.ai` origins, and spot-check a couple of clients' GHL workflow configs. Mitigation if found: keep a tiny redirect from the old Lovable host, or update those specific GHL workflows.

---

## 5. Provider-console changes (all additive — do them days ahead, they don't touch the live app)

- **Meta App (developer console):** add `https://app.marketinglube.ai/api/public/meta/callback` to **Valid OAuth Redirect URIs** (it's likely already there) and add the domain to **App Domains**. Also extend the origin allowlist in code (`meta.functions.ts:40-47`) and replace the hardcoded fallback `data-charm-hub-20.lovable.app` (`:35`) with `APP_URL`. **Existing connections need none of this** — it only affects new/reconnect flows.
- **Google Cloud Console:** create an **OAuth 2.0 Client** (you'll own it now, instead of Lovable). Authorized redirect URI = your Supabase project's `/auth/v1/callback`. Put the client ID/secret into Supabase Auth → Providers → Google. Enable **email-based identity linking** so returning Google users attach to their migrated `auth.users` row instead of minting duplicates.
- **Resend/Postmark:** verify DKIM/SPF for `app.marketinglube.ai` and `notify.app.marketinglube.ai` (these are currently delegated to Lovable's sender). This is additive DNS — add the new records now; they don't disturb the live app.
- **DNS TTL:** lower the `app.marketinglube.ai` A/CNAME TTL to 300s **at least a day before** cutover so the flip propagates fast.

---

## 6. Phased execution plan

### Phase 0 — Foundations (do now, no downtime, no risk)
1. Get the code into **GitHub** (Claude Code works from the repo). Lovable syncs to GitHub — connect it, or push the current tree.
2. Create the **Railway** project (web + worker services, not yet serving the domain) and a **new Supabase project** in your own account.
3. Enumerate the invisible state on the **live** Lovable DB and save it:
   - `SELECT jobname, schedule, command FROM cron.job;` (the ingest/precompute/email schedules)
   - `SELECT name FROM vault.secrets;` (expect `email_queue_service_role_key`)
   - `SELECT pg_size_pretty(pg_database_size(current_database()));` (sizing vs the 5GB export cap — the `raw` columns are the bloat risk)
4. Do the additive provider-console + DNS-TTL work in §5.

### Phase 1 — De-Lovable the code (Claude Code, feature branch, no downtime)
Make the swaps in §2 on a branch, plus the **surgical fixes that must ride along** (§7 tier 1). Deploy this branch to a **Railway staging URL** (`*.up.railway.app`) pointed at a **throwaway restore** of a day-old export. Get it booting and serving before you touch anything live.

### Phase 2 — Rehearse the restore (no downtime)
This is what makes the real cutover 2 hours instead of a bad day.
1. Trigger a Lovable **Export project data** (Cloud → Overview → Advanced settings). This is a `zstd` pg_dump — you need **`pg_restore` 16+**.
2. `pg_restore` into the new Supabase project. Known fix passes (documented Lovable-export traps): re-order/repair `auth.identities` FKs; ensure extensions (`pgcrypto`, `pgmq`, `pg_cron`, `pg_net`, `supabase_vault`) exist; clean storage ghost rows if any.
3. Recreate the out-of-band objects the dump/migrations miss: the **vault secret**, and the **`cron.job` rows** (from your Phase 0 capture) **rewritten to point at `https://app.marketinglube.ai/...`** with the new `CRON_SECRET` header — or, better, move scheduling to the Railway worker (removes the URL coupling entirely).
4. Point staging at this restored DB and run the **verification gate** (§8) against it. Fix every surprise here, script the steps, and **time the restore** so you know your real window.

### Phase 3 — Production cutover (the 2–4 hour window)
Run the rehearsed runbook. Minute-by-minute in §9.

### Phase 4 — Re-sync + verify + hold
Immediately after DNS flips, trigger a **full re-sync** of GHL/Meta/Stripe to close the gap between the export snapshot and now (all three are re-pullable from their APIs — this is why point-in-time export is fine). Run the §8 gate against production. **Do not click "Remove Lovable Cloud"** — leave it **Paused** for ~1 week as a hot rollback.

### Phase 5 — Post-migration refactor (week 1+, on Railway/Claude Code)
Now do the architecture work that actually makes it fast — §7 tier 2.

---

## 7. What to fix WHEN (sequencing the fixes vs. the move)

**Do NOT attempt the big SQL-aggregation rewrite during cutover.** That's how a 2-hour window becomes a 2-week outage. Migrate a faithful copy first.

### Tier 1 — Ride along with the migration (surgical, low-risk, high-leverage)
These either (a) are forced by the move, or (b) prevent the always-on Railway process from immediately falling over. All are small.
- **S1:** replace public-key hook auth with `CRON_SECRET` (you're recreating the cron headers anyway).
- **S3:** set `META_APP_SECRET`; remove the `dev-fallback-secret`.
- **B8:** pass an incremental `since` to the GHL cron ingest (`ingest-ghl-client.ts:13`) — the one-line fix that breaks the death-spiral (re-download of full history every cycle). Test it.
- **C11:** add an `ingestion_runs` running-guard + a concurrency cap (`p-limit 3–5`) to the ingest fan-outs — critical because one Railway process now shares a heap.
- **C8:** chunk the Meta upsert at 500 (`meta-ingest.server.ts:310`) — prevents integrations flipping to `error` on backfill.
- **B6 / all-time cache bypass:** clamp the Meta-refresh check so `endDate = 2999-12-31` (all-time preset) doesn't force a synchronous 37-month Meta sync on every load (`dashboard-tiles.server.ts:260`).
- **C1:** fix the attribution `onConflict` (match the expression index, or add a plain unique index) **or** temporarily disable attribution writes — so the ROAS/LTV panels stop silently showing $0.
- **C2:** confirmed handled by "restore live dump, don't replay migrations" — just make sure your process is a data restore, and add a real migration for `first_seen_at` afterward so the schema is honest.

### Tier 2 — Week-1 refactor on Railway (the actual speed win)
- Push aggregation into Postgres: lead counts per type, spend sums, first-purchase-per-customer, ROAS → `GROUP BY` + one window function via the **direct connection** or SQL RPCs. What takes the Node loop 30s becomes a sub-second query. **This is the "under one second on any host" fix.**
- Precompute lead-type membership at **ingest** (stamp `lead_type_ids uuid[]` when a lead lands) → deletes the O(leads×types) loop.
- Stop selecting `raw`/`metadata` in analytics paths (B2, C13); reserve `raw` for the lead-detail view.
- Move the inline Meta sync **out** of the request path entirely (serve cached spend, refresh in the worker).
- Decouple ingest from precompute: per-client locks, not the global running-guard (B9).
- Split the persistent worker from the web service (if you started combined).

### Tier 3 — Correctness backlog (schedule after the dust settles)
Refunds/disputes (C4), sale-type `name` matching without `raw` (C5), identity connected-components (C6), `webhook_sales`-as-leads (C7), Stripe 20k pagination (C9), GHL tie-drop (C15), adset-scope stale rows (C14), plaintext-cred encryption (S2), SSRF (S5), share-token pre-auth disclosure (S6), remove the hardcoded Aurenza/PYN client special-cases.

---

## 8. Verification gate (run on staging in Phase 2, and on prod in Phase 4)

**All green or stop.** Compare against a baseline you capture from the live Lovable app first.

- [ ] **Login (OTP):** request a 6-digit code with an existing account's email → email arrives via Resend → code verifies → session works.
- [ ] **Login (Google):** existing Google user signs in → lands on the **same** account (not a duplicate) → same workspace/clients visible.
- [ ] **Row-count parity:** `ghl_leads`, `transactions`, `meta_ads_metrics`, `integrations`, `ghl_lead_types`, `sale_types`, `custom_ratios`, `clients`, `client_lead_webhooks`, `client_shares` — counts match the live DB.
- [ ] **Integrations intact:** every `integrations` row has its `access_token`/`external_account_id`/`metadata` (Meta scope filters!) preserved. Trigger a manual GHL, Meta, and Stripe sync per a test client → succeeds with **no re-auth**.
- [ ] **ALIV dashboard** renders with sane numbers on 30-day and all-time presets; spot-check against the baseline.
- [ ] **Filters intact:** every client's lead types and sale types present; custom ratios evaluate (not 0 — confirms UUIDs preserved); `hidden_dashboard_metrics` respected.
- [ ] **Inbound webhook:** POST a signed test lead to `/api/public/leads/inbound/{token}` → 200 → row lands.
- [ ] **Share link:** open a public `/share/{token}` and a restricted one (with OTP) → both work.
- [ ] **Crons firing:** ingest/precompute/email jobs run on schedule against the Railway origin (check `ingestion_runs` and `email_send_log`).
- [ ] **Attribution:** if you fixed C1, confirm `attribution_matches` gains rows; if deferred, confirm panels degrade gracefully.

---

## 9. Cutover runbook (the 2–4 hour window)

_Prereqs: Phase 1–2 done and rehearsed; Railway staging verified against a restored copy; DNS TTL already 300s; all §5 provider changes live; Railway env set with production values._

| Step | Action | ~Time |
|---|---|---|
| 1 | **Freeze.** Pause the live Lovable cron jobs (or accept that the final export is the cutoff). Post a maintenance notice if desired. | 0:00 |
| 2 | **Final export.** Trigger Lovable **Export project data**. (Note: export is limited to once/24h — your Phase-2 rehearsal export must be ≥24h earlier.) Download it + any storage files. | 0:05 |
| 3 | **Restore** into the production Supabase project via the rehearsed `pg_restore` script (+ identity fix pass, extensions, vault secret). | 0:20 |
| 4 | **Recreate crons** pointed at `https://app.marketinglube.ai/...` with `CRON_SECRET` (or enable the Railway worker's scheduler). | 0:10 |
| 5 | **Point Railway** production services at the restored DB; deploy the de-Lovable branch as production. | 0:10 |
| 6 | **Smoke test on the raw Railway URL** (`*.up.railway.app`) — run the §8 gate before touching DNS. | 0:20 |
| 7 | **Flip DNS:** `app.marketinglube.ai` → Railway. Wait for propagation (≤5 min at 300s TTL). | 0:10 |
| 8 | **Full re-sync** GHL + Meta + Stripe for all clients to close the snapshot→now gap. | background |
| 9 | **Verify on production domain:** §8 gate again (login, a real client dashboard, a live inbound webhook, a share link). | 0:20 |
| 10 | **Hold.** Leave Lovable Cloud **Paused** (not Removed) for ~1 week. | — |

**Rollback (any time before step 10 "commits"):** flip DNS back to Lovable and unpause. Because all data sources are re-pullable and you never removed Lovable, there is **no data-loss path** in a rollback — you only lose the leads/sales that arrived during the Railway window, which the next Lovable sync re-pulls.

**Risks to the 2-hour target, and mitigations:**
- *Large DB / raw-payload bloat pushing near the 5GB export cap* → check size in Phase 0; if close, prune old `raw` payloads before the final export or use the fresh-project fallback path.
- *Restore slower than expected* → you'll know the real number from the Phase-2 rehearsal; size the maintenance window to that, not to a guess.
- *A webhook URL frozen to a `*.lovable.app` host* → caught in the §4 audit; keep a redirect.

---

## 10. Why Railway + Claude Code is the right pairing (your original question)

- **Railway** gives you three things Lovable structurally cannot: a **direct Postgres connection** (kills the 1,000-row PostgREST cap and the sequential HTTP paging — the entire `fetchAllRows` layer becomes replaceable with SQL aggregation), a **persistent worker process** for real background jobs instead of 45-second serverless windows, and **cron you control** instead of invisible platform config.
- **Claude Code** fits because the Tier-2 fixes are surgical refactors that span the whole codebase and need to be held in context at once — exactly where Lovable's session-by-session amnesia hurt you (the duplicate all-history transaction fetch and the stale "runs ingest" comments are scars from separate Lovable sessions that never saw each other).
- **But the host is the multiplier, not the fix.** Port as-is and you get ~2–3× from dropping PostgREST overhead while still shipping 250MB of lead JSON to count rows. Do the §7 Tier-2 aggregation work and ALIV's dashboard goes from tens of seconds to **under one second on any host** — Railway just also lets the background jobs actually finish.

---

## 11. Open questions I need from you before scoping the refactor

1. **Real table sizes on ALIV right now:** `SELECT count(*) FROM ghl_leads WHERE client_id = '<ALIV>'` and the same for `transactions`. That number decides whether Tier-2 aggregates are plain queries or need a rollup table.
2. **Total DB size** (Phase 0 query) — decides export-path (normal vs 5GB-fallback) and whether to prune `raw` first.
3. **Confirm on the live DB** whether Lovable added an out-of-band plain unique index on `attribution_matches(client_id,lead_id,transaction_id,match_type)` — decides whether C1 is "already dead" or "occasionally works."
4. **Combined vs split Railway services** for v1 — I recommend split (web + worker); confirm you're OK provisioning two.
5. **Email provider preference** — Resend (simplest) vs Postmark vs staying on an SMTP you already own.
