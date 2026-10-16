import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Component tests for the board (see node_modules/next/dist/docs — Vitest is the setup
 * Next documents for unit-testing client components; `.mts` is the extension that guide
 * uses, and the one that keeps Vite from loading this as CommonJS).
 *
 * `resolve.tsconfigPaths` is what makes the `@/lib/api` alias resolve the same way it does
 * in the app, so the tests mock the module the components actually import.
 *
 * If a cold `npm test` dies with "Failed to start forks worker / Timeout waiting for worker
 * to respond" and no test ever runs, it is almost certainly the `jsdom` import: on a cold
 * page cache it can take 30s+ to load, and vitest's worker-start timeout is a hardcoded 60s.
 * `node -e "console.time(0); require('jsdom'); console.timeEnd(0)"` in this directory says
 * which; run it once to warm the cache and the suite runs normally.
 *
 * `testTimeout` is 20s rather than vitest's 5s default because CI's node-22 runner is much
 * slower than a local machine: tests that render `deepMasterBoard(400)` have passed locally
 * and timed out at 5s on CI more than once. The timeout only limits how long a test may run.
 * It doesn't change what any test asserts.
 */
export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.test.{ts,tsx}"],
    testTimeout: 20_000,
  },
});
