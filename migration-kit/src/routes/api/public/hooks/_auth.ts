// Shared authorization check for /api/public/hooks/*.
// These endpoints run privileged server-side work (data ingestion, AI spend,
// cron fan-outs) so they must not be reachable by anonymous internet callers.
//
// MIGRATION CHANGE (fixes DIAGNOSIS.md S1): the old check compared against the
// Supabase publishable/anon key — the SAME key shipped in the browser bundle,
// so anyone who viewed source could trigger these endpoints. It now requires a
// private CRON_SECRET that only the Railway worker (and you) hold.
//
// The Railway worker sends it as the `x-cron-secret` header. For backward
// compatibility during cutover it is also accepted via `apikey` / `x-api-key` /
// `Authorization: Bearer`.

export function isAuthorizedHookCaller(request: Request): boolean {
  const expected = process.env.CRON_SECRET || "";
  // Fail closed: if no secret is configured, reject everything.
  if (!expected) {
    console.error("[hooks/_auth] CRON_SECRET is not set — rejecting all hook calls");
    return false;
  }

  const provided =
    request.headers.get("x-cron-secret") ||
    request.headers.get("apikey") ||
    request.headers.get("x-api-key") ||
    (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!provided) return false;

  // Constant-time comparison (length check first, then XOR accumulate).
  if (provided.length !== expected.length) return false;
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) {
    mismatch |= provided.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return mismatch === 0;
}

export function unauthorizedResponse(): Response {
  return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}
