// Node-server build config for Railway.  ⚠️ VALIDATE IN STAGING (Phase 1).
//
// The current vite.config.ts uses @lovable.dev/vite-tanstack-config, whose Nitro
// target defaults to **cloudflare** — `vite build` there does NOT emit a runnable
// Node server. Two ways to fix, in order of preference:
//
// ── Option 1 (try first — least invasive): keep the Lovable wrapper, force Node ──
// Many Nitro-based wrappers honor the NITRO_PRESET env var at build time. Set on
// the Railway web service:
//     NITRO_PRESET=node-server
// then keep your existing vite.config.ts as-is and check that `vite build`
// produces `.output/server/index.mjs`. If it does, you're done — ignore Option 2.
//
// ── Option 2 (fallback): replace vite.config.ts with the stock config below ─────
// Use this only if Option 1's preset override is ignored by the wrapper. Pin the
// plugin versions to what your package.json already resolves. `src/server.ts`'s
// { fetch } default export is compatible with Nitro's node-server preset via
// srvx — verify SSR + an API route locally after switching.
//
// npm i -D @tanstack/react-start @tanstack/router-plugin @vitejs/plugin-react \
//          @tailwindcss/vite vite-tsconfig-paths nitropack

import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [
    tsConfigPaths({ projects: ["./tsconfig.json"] }),
    tailwindcss(),
    tanstackStart({
      // Preserve the SSR error-wrapper entry (src/server.ts) exactly as today.
      server: { entry: "server" },
      // Emit a plain Node server Railway can run: node .output/server/index.mjs
      target: "node-server",
    }),
    viteReact(),
  ],
  resolve: {
    // Preserved from the original config — the `entities` dedupe aliases.
    alias: {
      entities: fileURLToPath(new URL("../node_modules/entities", import.meta.url)),
      "entities/lib/decode.js": fileURLToPath(
        new URL("../node_modules/entities/lib/decode.js", import.meta.url),
      ),
      "entities/lib/encode.js": fileURLToPath(
        new URL("../node_modules/entities/lib/encode.js", import.meta.url),
      ),
    },
    // React/TanStack single-copy dedupe (the wrapper did this for you).
    dedupe: ["react", "react-dom", "@tanstack/react-router", "@tanstack/react-start"],
  },
});
