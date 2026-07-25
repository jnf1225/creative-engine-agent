# Surgical in-place edits

Nine edits to apply by hand (or hand to Claude Code — the before/after blocks are
unambiguous even if line numbers have drifted). Each shows the **exact** current
code and its replacement. Grouped: **Tier-1** = apply during migration; the AI +
email swaps are **required** to leave Lovable.

---

## Edit 1 — Meta OAuth origin allowlist + fallback  ·  `src/lib/meta.functions.ts`

Kills the `data-charm-hub-20.lovable.app` fallback and makes Railway/your domain valid for **new** Meta connections. (Existing tokens are unaffected either way.)

**Find:**
```ts
    const fallbackOrigin = (process.env.APP_URL || process.env.VITE_APP_URL || "https://data-charm-hub-20.lovable.app").replace(/\/$/, "");
```
**Replace:**
```ts
    const fallbackOrigin = (process.env.APP_URL || process.env.VITE_APP_URL || "https://app.marketinglube.ai").replace(/\/$/, "");
```

**Find:**
```ts
        const isAllowedHost =
          parsedOrigin.hostname === "localhost" ||
          parsedOrigin.hostname === "app.marketinglube.ai" ||
          parsedOrigin.hostname.endsWith(".lovable.app") ||
          parsedOrigin.hostname.endsWith(".lovableproject.com");
```
**Replace:**
```ts
        const isAllowedHost =
          parsedOrigin.hostname === "localhost" ||
          parsedOrigin.hostname === "app.marketinglube.ai" ||
          parsedOrigin.hostname.endsWith(".up.railway.app");
```

---

## Edit 2 — Stable OTP pepper  ·  `src/lib/access.server.ts`

Removes the `LOVABLE_API_KEY` dependency for the sign-in-code hash. Set `CODE_PEPPER` in Railway env (and keep the old `LOVABLE_API_KEY` value available ~3h so in-flight codes still verify).

**Find:**
```ts
  const pepper = process.env.LOVABLE_API_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!pepper) throw new Error("Missing code signing secret");
```
**Replace:**
```ts
  const pepper =
    process.env.CODE_PEPPER ||
    process.env.LOVABLE_API_KEY ||          // transitional: verifies codes issued pre-cutover
    process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!pepper) throw new Error("Missing code signing secret");
```

---

## Edit 3 — GHL incremental `since`  ·  `src/routes/api/public/hooks/ingest-ghl-client.ts`  (Tier-1, fixes B8)

The single highest-leverage one-liner: stop re-downloading each client's **entire** GHL history every cron cycle. `ingestGhlClient` already supports `{ since }`; the cron just never passes it.

**Find:**
```ts
          const { ingestGhlClient } = await import("@/lib/ghl-ingest.server");
          const result = await ingestGhlClient(body.clientId);
```
**Replace:**
```ts
          const { ingestGhlClient } = await import("@/lib/ghl-ingest.server");
          // Incremental window: re-pull only the last ~7 days (buffer for GHL's
          // eventual-consistency + late edits) instead of full history every cycle.
          // Full backfills still happen on connect and via the dashboard's
          // on-demand lowGap path. Widen SINCE_DAYS if you see gaps.
          const SINCE_DAYS = Number(process.env.GHL_INCREMENTAL_DAYS || 7);
          const since = new Date(Date.now() - SINCE_DAYS * 86400_000).toISOString();
          const result = await ingestGhlClient(body.clientId, { since });
```

---

## Edit 4 — Fan-out concurrency guard + forward CRON_SECRET  ·  4 files

`ingest-ghl.ts`, `ingest-meta.ts`, `ingest-stripe.ts`, `run-analysis.ts`. Two changes each:
(a) forward `x-cron-secret` (not the old public `apikey`) to the `-client` siblings, and
(b) cap concurrency so a single Railway process doesn't run every client at once (fixes C11).

**In each file, find the apikey line, e.g.:**
```ts
        const apikey = request.headers.get("apikey") || process.env.SUPABASE_PUBLISHABLE_KEY || "";
```
**Replace with:**
```ts
        const cronSecret = request.headers.get("x-cron-secret") || process.env.CRON_SECRET || "";
```

**Then replace the `await Promise.all(ids.map(... fetch ...))` block with a small concurrency-limited runner.** Example for `ingest-ghl.ts` (apply the same shape to the other three, keeping each file's own `-client` path and id variable):
```ts
        // Bounded fan-out: at most N per-client workers in flight at once.
        const LIMIT = Number(process.env.INGEST_CONCURRENCY || 4);
        let cursor = 0;
        async function runNext(): Promise<void> {
          const i = cursor++;
          if (i >= ids.length) return;
          await fetch(`${origin}/api/public/hooks/ingest-ghl-client`, {
            method: "POST",
            headers: { "content-type": "application/json", "x-cron-secret": cronSecret },
            body: JSON.stringify({ clientId: ids[i] }),
          }).catch(() => {});
          return runNext();
        }
        await Promise.all(Array.from({ length: Math.min(LIMIT, ids.length) }, runNext));
```
> Per-client overlap (same client syncing twice if a sync outlives the interval) is additionally guarded by the worker's non-overlap `every()` and — for real safety — add an `ingestion_runs` "running" check inside `ingestGhlClient` in the Tier-2 pass. For cutover, the concurrency cap + worker overlap-guard are enough.

---

## Edit 5 — Chunk the Meta upsert  ·  `src/lib/meta-ingest.server.ts`  (Tier-1, fixes C8)

Prevents a big backfill from serializing hundreds of MB into one PostgREST request and flipping the integration to `error`.

**Find:**
```ts
        const { error } = await admin
          .from("meta_ads_metrics")
          .upsert(upserts, { onConflict: "client_id,level,external_id,date" });
        if (error) throw error;
        inserted += upserts.length;
```
**Replace:**
```ts
        for (let i = 0; i < upserts.length; i += 500) {
          const { error } = await admin
            .from("meta_ads_metrics")
            .upsert(upserts.slice(i, i + 500), { onConflict: "client_id,level,external_id,date" });
          if (error) throw error;
        }
        inserted += upserts.length;
```
> Apply the same 500-row chunking to the synthesized campaign-rows upsert a few lines below if present.

---

## Edit 6 — All-time preset stops forcing an inline Meta sync  ·  `src/lib/dashboard-tiles.server.ts`  (Tier-1, fixes B6)

The all-time preset resolves `endDate = 2999-12-31`, so the "is my spend data fresh?" check `maxDate >= endDate` can never be true → every all-time load synchronously re-ingests ~37 months of Meta data on the request thread. Clamp the comparison to "today".

**Find:**
```ts
    const syncIsFresh = Date.now() - newestSyncMs < 5 * 60_000;
    if (maxDate && maxDate >= endDate && syncIsFresh) return false;
```
**Replace:**
```ts
    const syncIsFresh = Date.now() - newestSyncMs < 5 * 60_000;
    // Clamp the coverage check to "today": for open-ended presets (all-time ends
    // 2999-12-31) we only need spend up to the real current day, otherwise the
    // check never passes and every load re-syncs 37 months inline.
    const today = new Date().toISOString().slice(0, 10);
    const effectiveEnd = endDate > today ? today : endDate;
    if (maxDate && maxDate >= effectiveEnd && syncIsFresh) return false;
```

---

## Edit 7 — Attribution writer  ·  no code change

Handled entirely by `supabase-migrations/20260725000002_fix_attribution_conflict_index.sql` (and `db/02_post_restore.sql` step 5). The existing `onConflict` in `attribution.server.ts` becomes valid once the index is `NULLS NOT DISTINCT`. Nothing to edit in TS.

---

## Edit 8 — AI insights off the Lovable gateway  ·  `src/lib/ai-analyst.server.ts`  (required)

**Find:**
```ts
const GATEWAY_URL = "https://ai.gateway.lovable.dev/v1/chat/completions";
const MODEL = "google/gemini-2.5-flash";
```
**Replace:**
```ts
import { AI_GATEWAY_URL as GATEWAY_URL, AI_MODEL as MODEL, aiApiKey } from "@/lib/ai/ai-client";
```

**Find (inside `runAiAnalysis`):**
```ts
  const apiKey = process.env.LOVABLE_API_KEY;
  if (!apiKey) throw new Error("LOVABLE_API_KEY not configured");
```
**Replace:**
```ts
  const apiKey = aiApiKey();
```
> The existing `fetch(GATEWAY_URL, { headers: { Authorization: \`Bearer ${apiKey}\` }, body: JSON.stringify({ model: MODEL, ... }) })` call now targets your endpoint unchanged. If your model id differs from `google/gemini-2.5-flash`, set `AI_MODEL` in env (e.g. `gemini-2.5-flash`).

---

## Edit 9 — Email off @lovable.dev/email-js  ·  `src/routes/lovable/email/queue/process.ts`  (required)

**Find:**
```ts
import { sendLovableEmail } from '@lovable.dev/email-js'
```
**Replace:**
```ts
import { sendLovableEmail } from '@/lib/email/send-email'
```

**Find:**
```ts
              await sendLovableEmail(
                {
                  run_id: deliveryRunId,
                  to: payload.to,
                  from: payload.from,
                  sender_domain: payload.sender_domain,
                  subject: payload.subject,
                  html: payload.html,
                  text: payload.text,
                  purpose: payload.purpose,
                  label: payload.label,
                  idempotency_key: idempotencyKey,
                  unsubscribe_token: unsubscribeToken,
                  message_id: payload.message_id,
                },
                { apiKey, sendUrl: process.env.LOVABLE_SEND_URL }
              )
```
**Replace:** (identical call — the adapter ignores `sendUrl` and reads `RESEND_API_KEY`)
```ts
              await sendLovableEmail(
                {
                  run_id: deliveryRunId,
                  to: payload.to,
                  from: payload.from,
                  sender_domain: payload.sender_domain,
                  subject: payload.subject,
                  html: payload.html,
                  text: payload.text,
                  purpose: payload.purpose,
                  label: payload.label,
                  idempotency_key: idempotencyKey,
                  unsubscribe_token: unsubscribeToken,
                  message_id: payload.message_id,
                },
                { apiKey: process.env.RESEND_API_KEY }
              )
```
> The route still checks `process.env.LOVABLE_API_KEY` for its own request auth near the top of the handler — that check can stay (set a dummy value) or be switched to `SUPABASE_SERVICE_ROLE_KEY`, which is what the worker sends as Bearer. Simplest: leave `apiKey`'s presence check but pass `RESEND_API_KEY` to the send call as above.

---

## Also: `package.json` cleanup (optional, after staging is green)

Remove the Lovable-only deps once nothing imports them:
`@lovable.dev/cloud-auth-js`, `@lovable.dev/email-js`, `@lovable.dev/webhooks-js`, and (if you replaced the build config via `build/vite.config.node.ts` Option 2) `@lovable.dev/vite-tanstack-config`. Leave them installed until staging verification passes — removing early just breaks the build.
