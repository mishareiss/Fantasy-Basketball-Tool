# Task 10 — Frontend draft board: render the value board with horizons, tiers, and filters

Context: Builds on task 9 (branch `board-tiers`, merged to `main`). Branch off `main`. This is the
FIRST real UI — until now everything is JSON. Read docs/FEATURE_SPEC.md section 9 (draft board UI),
docs/PLAN.md, the frontend scaffold (frontend/CLAUDE.md + frontend/AGENTS.md for repo conventions,
frontend/lib/api.ts for the typed client pattern — `request<T>`, `ApiError`, `API_BASE_URL` from
NEXT_PUBLIC_API_BASE_URL — frontend/app/page.tsx, frontend/app/layout.tsx, package.json, Tailwind
v4 setup), and the BACKEND response models you'll type against: app/api/players.py
(`BoardResponse` / `BoardRow` — the `/players/board` shape, its query params, `tier`, `tier_summary`,
`horizon`, the value + adp + age fields) and app/api/valuation.py (`/valuation/curve`,
`/valuation/tiers`). Private ESPN fantasy basketball dynasty tool, custom scoring; Next.js 16 App
Router + React 19 + TypeScript + Tailwind v4. Backend runs on :8000 (make backend), frontend on
:3000 (make frontend), CORS already allows localhost:3000. Scope is the BOARD VIEW ONLY — the draft
plan panel, live pick feed, drag-to-reorder personal ranking, and live slider calibration are all
LATER tasks. Create a new branch `frontend-board`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope:

1) Typed API client (frontend/lib/api.ts):
- Extend the existing `api` object (keep the `request<T>` / `ApiError` pattern) with `board(params)`,
  `valuationCurve()`, `valuationTiers(horizon)`. Add TypeScript types that MIRROR the backend
  response models exactly (read them from app/api/players.py + valuation.py — don't invent field
  names). `board` params: horizon, position, source, season, adp_source, adp_season, limit, tiers.

2) The board page (make it the primary view at `/`; move the existing health-probe to a small
   footer/status strip or a `/status` route — don't just delete the reachability check, it's useful):
- A dense, sortable, legible data TABLE of the ranked players. Columns: rank, tier, name, team,
  positions, age, the SELECTED horizon's value (prominent), the other horizon's value (secondary),
  fantasy_points_per_game, ESPN/consensus ADP, and the age multiplier. Tabular/monospace numerals so
  columns line up; the board is a tool, not a landing page — prioritize scannability and density
  over decoration, and keep it dark-mode aware (the scaffold already uses zinc + `dark:`).
- Controls: a horizon toggle (current_year <-> dynasty — refetches and reorders), a position filter
  (All / PG / SG / SF / PF / C), a top-N limit, and a tiers on/off toggle. Reflect controls in the
  URL query string so a view is shareable/reloadable (nice-to-have if cheap).
- TIER DIVIDERS: group rows by `tier` with a clear labeled divider between tiers ("Tier 3", with the
  tier's size) using `tier`/`tier_summary` — this is the draft-usability payoff, the thing that makes
  the board draftable at a glance. Untiered players (below the pool) render below the last tier.
- A compact header showing what the board is built from: source, season, horizon, `age_as_of`, and a
  small, collapsible read-only "curve & tiers" inspector (the age->multiplier table from
  /valuation/curve and the tier structure from /valuation/tiers) so the two calibration dials are
  VISIBLE without reading JSON. Read-only this task; live sliders are later.
- States: a loading state; a graceful error state when the backend is unreachable (reuse `ApiError`);
  and an explicit EMPTY state when `/players/board` 404s because nothing is synced yet — tell the
  user to run `make sync`, don't show a broken table.

3) Frontend test setup (your test-net rule applies to the UI too):
- Add a component test runner — Vitest + React Testing Library + jsdom (the standard for Next/React;
  add as devDependencies, a `vitest.config.ts`, and a `"test": "vitest run"` script). Mock the api
  client.
- Tests: the board renders rows from a mocked BoardResponse; the horizon toggle triggers a refetch
  with `horizon=dynasty`; the position filter narrows rows; tier dividers render from `tier_summary`;
  the loading, error, and empty (404) states each render. Keep them fast and deterministic (no real
  network).
- Wire it into CI: add the `npm test` step to the existing FRONTEND job in .github/workflows/ci.yml
  (after lint/build). Confirm `npm run lint`, `npm run build`, and `npm test` all pass locally,
  exactly as CI runs them.

Constraints:
- Reuse frontend/lib/api.ts (`request`/`ApiError`/API_BASE_URL) — one place for fetch/error/base-url.
  Follow frontend/CLAUDE.md + AGENTS.md. TypeScript strict; no `any` on the response types. Tailwind
  v4 (already configured) — no CSS framework swap. Keep new deps to the test runner only; no UI
  component library, no data-grid dep — a hand-built table is fine and lighter. The board must degrade
  gracefully with the backend down or unsynced. Don't hardcode the API URL (use API_BASE_URL). No
  backend changes in this task.

Acceptance criteria (verify before reporting done):
1. `make backend` + `make sync` + `make frontend`, open :3000 -> the board renders the ranked players
   with tier dividers, from GET /players/board.
2. The horizon toggle switches current_year <-> dynasty and reorders; the position filter works; the
   tiers toggle shows/hides dividers.
3. Backend-down and no-data-yet both show a clear message, not a crash or an empty table.
4. `npm run lint`, `npm run build`, and `npm test` all pass; the CI frontend job runs the tests.
5. The read-only curve & tiers inspector shows the current age->multiplier table and tier structure.

Report back with:
- Branch + file tree of changes; confirm STAGED, NOT committed.
- The columns and controls implemented, and how tier dividers look (describe the layout — screenshots
  aren't needed).
- The test setup and what the component tests cover; confirm lint/build/test green and CI wired.
- How the empty (unsynced) and backend-down states render.
- Anything you decided/deviated on, and confirmation that the draft plan panel, live pick feed,
  personal-ranking drag/reorder, and live slider calibration remain deferred.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
