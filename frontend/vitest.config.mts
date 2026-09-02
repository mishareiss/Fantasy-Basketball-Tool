import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Component tests for the board (see node_modules/next/dist/docs — Vitest is the setup
 * Next documents for unit-testing client components; `.mts` is the extension that guide
 * uses, and the one that keeps Vite from loading this as CommonJS).
 *
 * `resolve.tsconfigPaths` is what makes the `@/lib/api` alias resolve the same way it does
 * in the app, so the tests mock the module the components actually import.
 */
export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.test.{ts,tsx}"],
  },
});
