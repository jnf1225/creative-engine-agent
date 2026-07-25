#!/usr/bin/env bash
# Post-cutover smoke checks (the MIGRATION_PLAN.md §8 gate, automatable parts).
# Manual checks (real login, dashboard render, share link) still done by hand.
#
# USAGE:
#   BASE=https://app.marketinglube.ai CRON_SECRET=xxx SERVICE_ROLE_KEY=xxx ./verify.sh
# For the pre-DNS staging pass, set BASE to the *.up.railway.app URL.
set -uo pipefail

BASE="${BASE:?Set BASE to the app URL (railway staging or app.marketinglube.ai)}"
CRON_SECRET="${CRON_SECRET:-}"
SERVICE_ROLE_KEY="${SERVICE_ROLE_KEY:-}"
pass=0; fail=0
ok(){ echo "  ✅ $1"; pass=$((pass+1)); }
no(){ echo "  ❌ $1"; fail=$((fail+1)); }

echo "== 1. App is up (SSR root) =="
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")
[ "$code" = "200" ] && ok "GET / -> 200" || no "GET / -> $code"

echo "== 2. Hook auth rejects the OLD public key / no secret (S1 fixed) =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/public/hooks/precompute-tiles" -H 'content-type: application/json' -d '{}')
[ "$code" = "401" ] && ok "unauthenticated precompute -> 401" || no "unauthenticated precompute -> $code (expected 401)"

echo "== 3. Hook auth accepts the CRON_SECRET =="
if [ -n "$CRON_SECRET" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/public/hooks/precompute-tiles" -H "x-cron-secret: $CRON_SECRET" -H 'content-type: application/json' -d '{}')
  { [ "$code" = "200" ] || [ "$code" = "202" ]; } && ok "authed precompute -> $code" || no "authed precompute -> $code (expected 200)"
else echo "  (skip — set CRON_SECRET)"; fi

echo "== 4. Email queue route rejects without service-role Bearer =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/lovable/email/queue/process" -H 'content-type: application/json' -d '{}')
[ "$code" = "401" ] && ok "no-bearer email drain -> 401" || no "no-bearer email drain -> $code (expected 401)"

echo "== 5. Meta OAuth callback route exists (no 404) =="
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/public/meta/callback")
[ "$code" != "404" ] && ok "meta callback present ($code)" || no "meta callback 404 — path must stay /api/public/meta/callback"

echo "== 6. Inbound webhook route present (unknown token -> 404 unknown_webhook, not route-404) =="
body=$(curl -s -X POST "$BASE/api/public/leads/inbound/__notarealtoken__" -H 'content-type: application/json' -d '{"email":"x@y.z"}')
echo "$body" | grep -qi 'unknown_webhook\|invalid_signature\|webhook' && ok "inbound route reachable (got app-level error, not route miss)" || no "inbound route response unexpected: ${body:0:120}"

echo
echo "== Manual checks still required (MIGRATION_PLAN.md §8) =="
cat <<'EOF'
  [ ] Log in with an EXISTING account via 6-digit OTP (email arrives via Resend)
  [ ] Log in with Google -> lands on the SAME account (no duplicate)
  [ ] ALIV dashboard renders sane numbers (30-day + all-time)
  [ ] A client's lead types / sale types / custom ratios all present & evaluating
  [ ] Trigger a manual GHL + Meta + Stripe sync -> success, NO re-auth
  [ ] Post a signed test lead to a real inbound token -> row lands
  [ ] Open a public /share/<token> and a restricted one (OTP) -> both work
  [ ] db/04_verify_parity.sql row counts match the pre-cutover baseline
EOF
echo
echo "Automated: $pass passed, $fail failed."
[ "$fail" = "0" ]
