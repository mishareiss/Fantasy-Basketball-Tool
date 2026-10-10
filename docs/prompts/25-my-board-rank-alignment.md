# Task 25 — My Board: align the rank numbers, and stop `font-mono` collapsing to a proportional font

Context: This is the Next.js 16 / React 19 / Tailwind 4 frontend. On the deployed My Board the
rank numbers wobble left-to-right instead of forming a clean vertical column. Two independent
causes, both fixed here.

Read these first:
- `frontend/components/masterboard/MasterRow.tsx` — the rank cell is the second `<td>`
  (around line 275): `<td className="py-1.5 pr-2 text-right">` holding the rank number `<span>`
  **and** the `T{tier}` chip **and** the position-tier chip (`{scope}{tier}`, e.g. `C1`, `PG2`),
  all inline in one right-aligned cell.
- `frontend/app/globals.css` — the `@theme inline` block defines `--font-sans`/`--font-mono`.
- `frontend/app/layout.tsx` — Geist + Geist Mono are loaded via `next/font/google`.
- `frontend/__tests__/masterboard.test.tsx` — the existing board test; mirror its render setup.

Branch: create off `main`, e.g. `task-25-rank-alignment`.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT. Misha commits himself. No
`Co-Authored-By` / AI-attribution trailers anywhere.

Scope:
1. **Rank column alignment (`MasterRow.tsx`).** The rank number shares one `text-right` cell with
   the two tier chips, so the right-aligned group's number is pushed to a different x-position on
   every row as the chips change width (`C2` is narrower than `PG2`). Fix so the rank number's
   position is fixed and independent of the chips:
   - Put the rank number in its own fixed-width, right-aligned box (e.g. wrap the existing
     `<span className="font-mono text-base font-semibold tabular-nums …">{row.rank ?? MISSING}</span>`
     so it is `inline-block w-8 text-right` — pick the width from the real max rank, see the
     landmine below).
   - Move the `T{tier}` and position-tier chips into a **separate sibling element** after the
     number box, so their width can never shift the number. Anchor the number box at a consistent
     left position (remove the whole-group `text-right` from the `<td>`, or restructure so the
     number box's left edge is fixed).
   - Preserve everything else exactly: the `MISSING` em-dash fallback for a null rank, both chips
     with their existing `title` text and `data-tier` / `data-position-tier` attributes, dark-mode
     classes, and the drag handlers on the row.
   - LANDMINE: this is a dynasty board that ranks well past 100 players, so ranks are up to 3
     digits. The fixed width must hold a 3-digit number without clipping or re-introducing wobble
     — size it for `"250"`, not `"12"` (`w-8`/2rem fits three tabular digits at `text-base`;
     verify and bump if needed).
2. **Font fallback (`globals.css`).** In the `@theme inline` block the mono chain is
   `--font-mono: var(--font-geist-mono);` with no fallback. Any element using the Tailwind
   `font-mono` utility (the rank number does) therefore renders in the browser's default
   *proportional* font whenever Geist Mono fails to load — which is why the deployed board looks
   different from local. Add real fallback stacks to both vars so tabular figures survive a
   missing webfont:
   - `--font-mono: var(--font-geist-mono), ui-monospace, SFMono-Regular, Menlo, monospace;`
   - `--font-sans: var(--font-geist-sans), ui-sans-serif, system-ui, sans-serif;`

Migrations & wiring: none — frontend-only, no API/model/route changes.

Testing (not optional): extend `frontend/__tests__/masterboard.test.tsx`, mirroring its existing
render setup. jsdom does no layout, so assert STRUCTURE, not pixels:
- Render rows with a single-digit rank (e.g. 3) and a triple-digit rank (e.g. 120) and assert the
  rank number is in its own element carrying the fixed-width class, and that the `T{tier}` /
  position-tier chips are **separate sibling elements** — i.e. the number and the chips are no
  longer commingled in one span. This is the regression guard that the wobble can't come back.
- Keep the existing assertions (chip text, titles, data attributes) green.
- `npm run lint`, `npm run test` (vitest run), and `npm run build` must all pass. Stay
  offline/deterministic — no network, no secrets (already the house pattern).

Constraints: frontend-only; no new dependencies; don't touch the board's data flow, API calls, or
the master-board endpoints; keep Tailwind v4 idioms; keep both tier chips and all their metadata.

Acceptance criteria (verify before reporting done):
1. `cd frontend && npm run lint` — clean.
2. `cd frontend && npm run test` — green, including the new alignment case.
3. `cd frontend && npm run build` — succeeds.
4. In `MasterRow.tsx` the rank number is in its own fixed-width right-aligned box and the tier
   chips are a separate sibling; a 3-digit rank does not clip or shift the column.
5. `globals.css` `--font-mono`/`--font-sans` each carry a real fallback stack after the Geist var.

Report back with: branch name + `git status` (confirm STAGED, not committed); the before/after of
the rank `<td>` JSX in `MasterRow.tsx`; the `globals.css` diff; the new/changed test; and any
deviation from this brief with its reason.

Final reminder: STAGE everything (`git add -A`), DO NOT COMMIT, no AI-attribution trailers.
