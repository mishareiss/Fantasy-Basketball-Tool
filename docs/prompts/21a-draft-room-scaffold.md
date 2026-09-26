# Task 21a — Draft room: board, pick entry, sim controls (+ two small backend additions)

Context: The draft-plan BACKEND is done (T19 engine, T20 persistence + endpoints + plan). This
task is the first half of the draft-room FRONTEND: a new `/draft` page with the live snake board,
pick entry, and the simulation controls. It also adds TWO small backend endpoints the controls
need. The plan panels (targets/best-available with availability %) and the My Board draft-mode
toggle are Task 21b — do NOT build them here.

Read these first:
- `backend/app/api/draft.py` — the whole `/draft` router and its pydantic response models
  (`DraftStateResponse`, `DraftPickRow`, `DraftTeamRow`, `DraftAdvanceResponse`, `DraftCreate`,
  `DraftPickWrite`, `DraftSimulateWrite`). `api.ts` mirrors these EXACTLY (see below). Note
  `post_draft_pick`, `post_draft_simulate` (uses `simulate_opponents_until`), `post_draft_undo`,
  and `_load`/`_state_response`.
- `backend/app/draft/autopick.py` — `simulate_opponents_until(state, field, rng, *, stop_slot=None,
  stop=None, ...)` MUTATES state, returns the `Pick`s made, stops at my seat. The `stop` predicate
  is how you cap it to N picks.
- `backend/app/draft/state.py` — `apply_pick`, `on_the_clock`, `next_pick_number`, `is_complete`.
- `frontend/lib/api.ts` — the typed client. Every response type MIRRORS a backend pydantic model
  (field names + nullability copied, not invented); `request`/`post`/`put`/`del`, `ApiError`,
  `query()`. You will add the draft types + `api.draft.*` methods here.
- `frontend/lib/masterboard.ts` — the search helper `matches(row, term)` (accent-insensitive
  substring) and `PAGE` windowing; reuse `matches` for the pick-entry search.
- `frontend/components/masterboard/MasterBoardPage.tsx` — the reference for a stateful board page:
  the request-key `settled` guard, optimistic-then-replace, `ApiError` handling, `Segmented`/
  `Segment` controls (from `components/board/BoardControls`), the loading/error/empty state split.
- `frontend/app/layout.tsx` (add the nav link), `frontend/app/my-board/page.tsx` (page shell
  pattern), `frontend/__tests__/masterboard.test.tsx` (how api is mocked in Vitest/RTL).

Branch: `task-21a-draft-room` off `main` (main is at the merged T20 head, a738569).

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT. Misha commits/merges/pushes himself.
NO AI attribution / Co-Authored-By / session trailers anywhere.

STANDING CI RULE (do not trip it): a userEvent-driven Vitest test over a big fixture times out at
5s on CI's node-22 runner. Keep draft-page test fixtures SMALL — a 4-team × 3-round draft, not
10×20 — or set an explicit `it(name, fn, 15000)` timeout. Do NOT render a full 10×20×N fixture in a
userEvent test.

---

## Part 0 — two small backend additions (with tests; keep the suite green)

These are backend, so treat them as backend: add pytest coverage in `backend/tests/test_api_draft.py`,
keep `make test` + `make lint` green. No migration (no new columns; `is_auto` already exists).

1. **Step-one-pick: a `count` on `POST /draft/simulate`.** Add `count: int | None = Field(None, ge=1,
   ...)` to `DraftSimulateWrite`. When given, the advance makes AT MOST `count` opponent picks and
   still stops at my seat or the draft's end (whichever comes first) — implement it by passing a
   `stop` predicate / counter to `simulate_opponents_until`, not by re-rolling. Default (null) is the
   current behavior (advance to my next pick). `count=1` makes exactly one opponent pick. Everything
   else about the endpoint (persist `is_auto=true`, echo the seed, never pick for me) is unchanged.
   Tests: `count=1` commits exactly one pick; `count` larger than the gap to my seat still stops at
   my seat; `count` while I am already on the clock makes zero picks (empty list, 200).

2. **Edit any pick: `PUT /draft/picks/{pick_number}`.** Body `{player_id}` (a small `DraftPickEdit`
   model). It REPLACES the player taken at an already-made `pick_number` — the manual override for a
   mis-entry or a re-decided pick. Rules:
   - 404 when no draft.
   - 422 when `pick_number` is not a made pick (`1..picks_made`), when `player_id` is not in the draft
     universe, or when `player_id` is already taken at a DIFFERENT pick (name the conflict).
   - Editing to the same player he already is: a no-op 200 (not an error).
   - The previously-taken player becomes available again; the row's `team_slot` is unchanged (the
     snake owns it); set `is_auto=false` (an edited pick is a manual decision).
   - Returns `DraftStateResponse`. Rehydration/replay stays valid — `build_state` replays by
     `pick_number` and the edited player is simply what gets applied at that slot.
   Tests: edit swaps the player and frees the old one; editing to a player drafted elsewhere → 422;
   editing an unmade pick → 422; edit then `build_state` round-trips; is_auto flips to false.

## Part 1 — `api.ts` draft client (frontend)

Add, mirroring the pydantic models in `app/api/draft.py` (copy field names + nullability exactly,
with the same doc-comment style the existing types use):
- Types: `DraftPickRow`, `DraftTeamRow`, `DraftStateResponse`, `DraftAdvanceResponse`, and the
  request bodies `DraftCreateBody`, `DraftPickWriteBody`, `DraftSimulateBody` (incl. `count`),
  `DraftPickEditBody`. (Do NOT add the plan types here — that is Task 21b.)
- `api.draft` methods (or flat `api.*`, matching the file's existing shape):
  `getDraft()` (GET /draft — note it 404s when none exists; callers handle that, don't throw a
  page error on 404), `createDraft(body, reset?)` (POST /draft, `?reset=true` when replacing),
  `resetDraft()` (POST /draft/reset), `applyPick(body)` (POST /draft/picks),
  `editPick(pickNumber, body)` (PUT /draft/picks/{n}), `simulate(body)` (POST /draft/simulate),
  `undoPick()` (POST /draft/undo). All through the existing `request`/`post`/`put` error shaping.

## Part 2 — the `/draft` page (frontend)

Add `frontend/app/draft/page.tsx` (+ a `"use client"` container, e.g.
`components/draft/DraftRoomPage.tsx`, and split-out pieces under `components/draft/`), and a **"Draft"
nav link** in `app/layout.tsx` (after "My Board"). Function-first styling, consistent with the
existing pages (Tailwind, the zinc palette, the `Segmented`/`Segment` control, the same
loading/error/empty split as `MasterStates`). No Suspense/query-string state — like `/my-board`,
this is an editing surface, not a shareable view.

Behavior:
- **On load**: `getDraft()`. A 404 → the **setup form**: my seat (`my_slot`, 1..team_count), `mode`
  (simulation | manual), and — behind a collapsed "advanced" — nothing required (leave field sources
  / roster to backend defaults; an empty create body is our league). Submit → `createDraft`. A draft
  that exists → the **draft room**. Offer **Reset** (and, from the room, a "start over" that calls
  `resetDraft`).
- **Mode toggle** (simulation | manual): a client-side UI state, initialized from `state.mode`.
  Backend does not enforce mode, so this only decides which controls show — the simulation controls
  (Advance / Step) appear in simulation mode; Undo / Reset / manual entry appear in both. (Do not add
  a backend call to persist a mode change; there is none, and mode is advisory.)
- **On-the-clock banner**: the team on the clock, the pick number, the round, and a clear "YOUR PICK"
  state when `is_my_pick`. `is_complete` → a "draft complete" state.
- **The snake grid** (this is the board): a `rounds × team_count` grid — rows are rounds 1..R, columns
  are teams 1..T. Each cell is the pick at that (round, team); map a cell to its `pick_number` with
  the snake (odd rounds run team 1..T, even rounds T..1 — mirror `app/draft/config.py:_snake`; put
  this in a tested `lib/draft.ts` helper `cellPickNumber(round, teamSlot, teamCount)` and its inverse).
  Fill cells from `state.log` (name + positions, a subtle mark for `is_auto`); empty cells for future
  picks; **my column highlighted**; the on-the-clock cell marked. Clicking a FILLED cell opens the
  **edit** affordance (search → `editPick(pick_number, {player_id})`).
- **Pick entry — the search box** (the panels are Task 21b): a search input that finds an AVAILABLE
  player and drafts him for the team `on_the_clock` (`applyPick({player_id})` — omit `team_slot`, the
  backend defaults it to the clock). The candidate list comes from `api.masterBoard()` (the whole
  board's names) MINUS the drafted set (the `espn_player_id`s in `state.log`); filter with
  `matches()`. Note in a comment: a player the field doesn't rank isn't in the draft universe and
  `applyPick` 422s for him — surface that error, don't pre-hide beyond the drafted set. This one
  search box is how BOTH your own picks and (in manual mode) every opponent's pick get entered, since
  the snake only ever lets the on-the-clock seat pick.
- **Simulation controls**: **Advance to my pick** (`simulate({})`), **Step one pick**
  (`simulate({ count: 1 })`), **Undo last pick** (`undoPick()`), **Reset** (`resetDraft()`). Advance
  is a no-op when I am already on the clock (empty picks list) — reflect that, don't spin.
- **State handling**: every mutating call returns the fresh `DraftStateResponse` (or
  `DraftAdvanceResponse.state`) — REPLACE local state from the response rather than hand-patching, the
  same "server's board wins" rule `MasterBoardPage` follows. A failed call surfaces via `ApiError`
  (its `detail` is the human half). Re-fetch the master-board catalog once on mount (it doesn't change
  during a draft); the drafted set comes from the live draft state, so it stays current for free.

## Testing (Vitest/RTL, `frontend/__tests__/draft.test.tsx`, + `lib/draft` unit tests)
- `lib/draft.ts` snake helpers: `cellPickNumber` / its inverse round-trip; a 4×3 grid maps to the
  hand-computable snake; my column is the right set of pick numbers.
- Page (api MOCKED, SMALL fixture — 4 teams × 3 rounds): setup form shows when `getDraft` 404s and
  `createDraft` is called on submit; the room renders the grid with picks in the right cells; the
  on-the-clock banner; typing in the search box + clicking a result calls `applyPick` and the state
  is replaced from the response; clicking a filled cell + choosing a player calls `editPick`; Advance
  calls `simulate({})`, Step calls `simulate({count:1})`, Undo calls `undoPick`, Reset calls
  `resetDraft`; the mode toggle hides the sim controls in manual mode. Keep it small / set explicit
  timeouts per the CI rule.

## Constraints
- `api.ts` types mirror the backend models exactly — if a field name/nullability differs, the mirror
  is wrong. Reuse `request`/`post`/`put`, `ApiError`, `query`, `matches`, `Segmented`/`Segment`.
- Function-first, consistent with existing pages; no new frontend dependencies (no drag lib, no grid
  lib — a CSS grid is enough; a click-to-edit cell is fine, no cell drag needed this task).
- Keep `make test` + `make lint` (backend) and `npm test` + `npm run build` + `npm run lint`
  (frontend) all green. Do NOT touch the master-board endpoint or the frozen `/master/board` tests
  (draft-mode annotation is Task 21b).

## Acceptance criteria
1. `cd backend && uv run pytest -q` passes (old + new count/edit tests); `make lint` clean; `alembic
   heads` still single (no migration).
2. `cd frontend && npm test` passes; `npm run build` succeeds; `npm run lint` clean.
3. `PUT /draft/picks/{n}` swaps a made pick's player, frees the old one, 422s on an already-taken
   player / an unmade pick; `POST /draft/simulate {count:1}` makes exactly one opponent pick.
4. The `/draft` page: setup form when no draft; snake grid with picks in the correct cells (verified
   by the `lib/draft` snake test); search entry drafts for the on-the-clock seat; Advance/Step/Undo/
   Reset and edit-pick each call the right endpoint and replace state from the response.

## Report back with
- Branch + file tree (backend: the two endpoint additions + tests; frontend: `lib/api.ts`,
  `lib/draft.ts`, `app/draft/page.tsx`, `components/draft/*`, `app/layout.tsx`, the tests).
- Confirm STAGED, NOT committed, on `task-21a-draft-room`; `alembic heads` single; both suites green.
- A short note: how a cell maps to its pick number, and how manual mode enters opponents' picks.
- Decisions/deviations, and confirm the plan panels + My Board toggle were left for Task 21b.

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
