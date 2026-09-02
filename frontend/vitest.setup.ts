import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * `globals: false`, so Testing Library's own auto-cleanup never registers. Unmount between
 * tests explicitly, or every test after the first renders into a document that still holds
 * the previous board.
 *
 * No jest-dom matchers here on purpose: it declares `node >=22` and the CI frontend job is
 * pinned to Node 20, so the assertions stay on Vitest's own (a `getBy*` query already throws
 * when the element is missing, which is most of what `toBeInTheDocument` was doing).
 */
afterEach(() => {
  cleanup();
});
