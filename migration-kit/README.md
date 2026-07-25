# MarketingLube Migration Kit — Plug-and-Play

Prebuilt infrastructure to move MarketingLube from **Lovable Cloud → Railway + your own Supabase project** with a 2–4h cutover. See `../docs/MIGRATION_PLAN.md` for the full rationale and `../docs/DIAGNOSIS.md` for the issues these changes fix.

> **What this kit is:** exact drop-in files + surgical edits + DB scripts + a Railway worker, all matched to your current codebase. Tomorrow you (or Claude Code) apply them to the real MarketingLube repo in the order below. Nothing here has been run against your app.

---

## File → destination map

| Kit file | Goes to (in MarketingLube repo) | What it does |
|---|---|---|
| `src/routes/api/public/hooks/_auth.ts` | same path (**replace**) | Hook auth → private `CRON_SECRET` (fixes S1) |
| `src/integrations/lovable/index.ts` | same path (**replace**) | Google OAuth → native Supabase (removes Lovable broker) |
| `src/lib/email/send-email.ts` | same path (**new**) | Resend adapter, `sendLovableEmail`-compatible |
| `src/lib/ai/ai-client.ts` | same path (**new**) | Direct AI endpoint helper (replaces `ai.gateway.lovable.dev`) |
| `worker/index.mjs` | repo root `worker/` (**new**) | Persistent scheduler (replaces pg_cron fan-outs) |
| `build/vite.config.node.ts` | `vite.config.ts` (**replace, validate in staging**) | Nitro `node-server` preset for Railway |
| `build/railway.web.json` | `railway.json` on web service | Railway web build/start |
| `build/railway.worker.json` | Railway worker service settings | Railway worker start |
| `db/*.sql`, `db/*.sh` | run against DBs (not committed to app) | Restore + reconcile + verify |
| `supabase-migrations/*.sql` | `supabase/migrations/` (**new**) | Make the drifted schema honest (first_seen_at, attribution) |
| `patches/EDITS.md` | apply by hand / Claude Code | 6 surgical in-place edits (find→replace blocks) |
| `env/*.env.example` | Railway env vars | Web + worker environment |
| `scripts/verify.sh` | run post-cutover | The §8 verification gate |

---

## Apply order (tomorrow)

### A. Before the maintenance window (additive, zero downtime)
1. **Provider consoles** (see `MIGRATION_PLAN.md` §5): add Railway/`app.marketinglube.ai` to Meta OAuth redirect URIs; create your own Google OAuth client → put in Supabase Auth; verify Resend DKIM for `app.marketinglube.ai` + `notify.app.marketinglube.ai`; drop DNS TTL to 300s.
2. **Capture live state:** run `db/00_capture_live_state.sql` against the **live Lovable DB** and save the output (cron jobs, vault secrets, DB size, ALIV counts).
3. **Apply code changes** on a branch:
   - Replace the 2 files, add the 2 new files (auth, email adapter, ai helper).
   - Apply the 6 edits in `patches/EDITS.md`.
   - Add the 2 migrations in `supabase-migrations/`.
   - Swap the build config (`build/vite.config.node.ts` → `vite.config.ts`) and **validate `vite build` produces `.output/server/index.mjs` in staging.**
4. **Rehearse the restore** on a throwaway Supabase project using a day-old export (`db/01_restore.sh` + `db/02_post_restore.sql`), point a Railway staging service at it, run `scripts/verify.sh`. Fix every surprise here.

### B. The cutover window (2–4h)
Follow `MIGRATION_PLAN.md` §9 runbook. The DB half is: `01_restore.sh` → `02_post_restore.sql` → start the worker → `scripts/verify.sh` → flip DNS → full re-sync.

### C. After
Leave Lovable Cloud **Paused** (not Removed) for ~1 week. Then start the Tier-2 perf refactor (`MIGRATION_PLAN.md` §7).

---

## What each change fixes (cross-ref to DIAGNOSIS.md)

- `_auth.ts` → **S1** (public-key hook auth)
- `lovable/index.ts` → removes Lovable Google broker dependency
- `send-email.ts` + edit 9 → removes `@lovable.dev/email-js`
- `ai-client.ts` + edit 8 → removes `ai.gateway.lovable.dev`
- edit 1 (meta origin) → **S3-adjacent** + new-connection correctness on Railway
- edit 2 (CODE_PEPPER) → stable OTP pepper
- edit 3 (GHL `since`) → **B8** death-spiral one-liner
- edit 4 (fan-out guard) → **C11** single-process meltdown
- edit 5 (Meta chunk) → **C8** backfill→error
- edit 6 (all-time clamp) → **B6** inline sync on every all-time load
- `supabase-migrations/*_attribution*` → **C1** dead attribution writer
- `supabase-migrations/*_first_seen_at*` → **C2** schema honesty for future `db reset`

Tier-3 correctness bugs (refunds, identity merge, Stripe 20k, etc.) are intentionally **not** in this kit — they're post-migration work so the cutover copies faithful behavior.
