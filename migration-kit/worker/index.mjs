// MarketingLube background worker — Railway service.
//
// Replaces the invisible Lovable/pg_cron jobs (net.http_post fan-outs) with a
// persistent process you control. It POSTs the web service's existing hook
// endpoints on a schedule, authenticated with CRON_SECRET. No app code depends
// on this — it only *drives* the endpoints the app already exposes — so it is
// safe to deploy/redeploy independently of the web service.
//
// Plain ESM, zero dependencies (Node 18+ has global fetch). Run with:
//     node worker/index.mjs
//
// Schedules are simple interval-based (not full cron syntax) to stay dependency
// free; the env vars accept cron strings for documentation but this runner uses
// the interval fallbacks below. If you want true cron expressions, add
// `node-cron` and swap `every()` for `cron.schedule()`.

const APP_URL = (process.env.APP_URL || "http://localhost:3000").replace(/\/$/, "");
const CRON_SECRET = process.env.CRON_SECRET || "";
const SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || "";

if (!CRON_SECRET) {
  console.error("[worker] CRON_SECRET is not set — refusing to start");
  process.exit(1);
}

const FLAG = (name, def = true) =>
  (process.env[name] ?? String(def)).toLowerCase() !== "false";

const ENABLE = {
  emailQueue: FLAG("ENABLE_EMAIL_QUEUE"),
  precompute: FLAG("ENABLE_PRECOMPUTE"),
  ingest: FLAG("ENABLE_INGEST"),
  analysis: FLAG("ENABLE_ANALYSIS"),
};

// Interval fallbacks (ms). Tune via env if you like; these mirror the plan's
// defaults and are deliberately gentle.
const EVERY = {
  emailQueue: 10_000, // 10s
  precompute: 120_000, // 2 min
  ingestGhl: 15 * 60_000, // 15 min
  ingestMeta: 15 * 60_000, // 15 min
  ingestStripe: 30 * 60_000, // 30 min
  runAnalysis: 24 * 60 * 60_000, // 24h
};

async function hook(path, { bearer } = {}) {
  const url = `${APP_URL}${path}`;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        // The web hooks accept x-cron-secret (see migration _auth.ts).
        "x-cron-secret": CRON_SECRET,
        // Email queue route authenticates with the service-role Bearer instead.
        ...(bearer ? { authorization: `Bearer ${bearer}` } : {}),
      },
      body: "{}",
    });
    const text = await res.text().catch(() => "");
    if (!res.ok) {
      console.error(`[worker] ${path} -> ${res.status} ${text.slice(0, 200)}`);
    } else {
      console.log(`[worker] ${path} -> ${res.status} ${text.slice(0, 160)}`);
    }
  } catch (err) {
    console.error(`[worker] ${path} failed:`, err?.message || err);
  }
}

// Run a task now, then on an interval. Guards against overlap: a task won't
// start again until the previous run settles.
function every(ms, label, fn) {
  let running = false;
  const tick = async () => {
    if (running) {
      console.warn(`[worker] skip ${label} — previous run still in flight`);
      return;
    }
    running = true;
    try {
      await fn();
    } finally {
      running = false;
    }
  };
  // stagger the first run a little so everything doesn't fire at boot at once
  setTimeout(tick, 2_000 + Math.floor(ms / 20));
  return setInterval(tick, ms);
}

console.log(`[worker] starting; APP_URL=${APP_URL}`);
console.log(`[worker] enabled:`, ENABLE);

if (ENABLE.emailQueue) {
  if (!SERVICE_ROLE_KEY) {
    console.warn("[worker] ENABLE_EMAIL_QUEUE but SUPABASE_SERVICE_ROLE_KEY unset — email drain disabled");
  } else {
    every(EVERY.emailQueue, "email-queue", () =>
      hook("/lovable/email/queue/process", { bearer: SERVICE_ROLE_KEY }),
    );
  }
}

if (ENABLE.precompute) {
  every(EVERY.precompute, "precompute-tiles", () => hook("/api/public/hooks/precompute-tiles"));
}

if (ENABLE.ingest) {
  every(EVERY.ingestGhl, "ingest-ghl", () => hook("/api/public/hooks/ingest-ghl"));
  every(EVERY.ingestMeta, "ingest-meta", () => hook("/api/public/hooks/ingest-meta"));
  every(EVERY.ingestStripe, "ingest-stripe", () => hook("/api/public/hooks/ingest-stripe"));
}

if (ENABLE.analysis) {
  every(EVERY.runAnalysis, "run-analysis", () => hook("/api/public/hooks/run-analysis"));
}

// Keep the process alive + log unhandled rejections instead of crashing.
process.on("unhandledRejection", (r) => console.error("[worker] unhandledRejection", r));
process.on("SIGTERM", () => {
  console.log("[worker] SIGTERM — shutting down");
  process.exit(0);
});
