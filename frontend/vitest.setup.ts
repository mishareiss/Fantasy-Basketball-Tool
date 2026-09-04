import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * `globals: false`, so Testing Library's own auto-cleanup never registers. Unmount between
 * tests explicitly, or every test after the first renders into a document that still holds
 * the previous board.
 *
 * No jest-dom matchers here: the assertions stay on Vitest's own, because a `getBy*` query
 * already throws when the element is missing, which is most of what `toBeInTheDocument` was
 * doing. (This used to be forced — jest-dom declares `node >=22` and CI was pinned to 20 —
 * but CI now runs 22 for Vitest's sake, so it is a choice again rather than a constraint.)
 */
afterEach(() => {
  cleanup();
});
