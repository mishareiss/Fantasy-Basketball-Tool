# Task 22 — Draft room redesign: fit-to-width board, left sidebar, tabbed views

Context: The draft room works (T21a board + entry + sim controls; T21b plan panels + My Board
draft mode). Misha has UI notes that reshape the `/draft` page: the board must fit on screen, a
persistent left sidebar of available players replaces the stacked plan panels, and the main panel
gains four tabbed views. This is ONE task — a big frontend rework plus three small backend
additions. Branch off `main`, which MUST already include T21b (merge it first).

Read these first:
- `frontend/components/draft/DraftRoomPage.tsx` — the container (state, `commit()`, `pending`
  request-key, catalog fetch, `makePick`/`editPick`/`advance`/`undo`/`reset`, mode toggle). You
  reshape its layout and REMOVE the `DraftPlan` panels.
- `frontend/components/draft/DraftBoard.tsx` — the current snake grid. It uses fixed `w-36` cells
  in an `overflow-x-auto min-w-max` box (that is the horizontal scroll to kill). `lib/draft.ts`
  `cellPickNumber` is the snake mapping — reuse it, don't re-derive.
- `frontend/components/draft/DraftPlan.tsx` — the plan panels being REMOVED (and `api.draftPlan`
  usage with them). Keep the backend `/draft/plan` endpoint; just stop calling it from the UI.
- `frontend/components/draft/{DraftControls,DraftSetup,PickSearch,DraftStates}.tsx` — reuse
  `PickSearch`'s search/`matches` pattern in the sidebar; `DraftSetup` gains team-name inputs.
- `frontend/lib/api.ts` — `MasterBoardResponse` (`players[]` with `rank`, `positions`, `tag`,
  `overall_tier`; `tiers[]` = every scope's `cut_ranks`+`size`), `MasterPlayerRow`,
  `DraftStateResponse`/`DraftTeamRow`, `api.masterBoard()`. You add types + methods here.
- `frontend/lib/masterboard.ts` — `tierAt(cuts, rank)`, `tierBands`, `matches`, `tagStyle`,
  `TAG_CLASS` — reuse for tier dividers, search, and tag chips.
- `frontend/lib/draft.ts` — `cellPickNumber`, `roundOf`, `pickNumbersFor`, `draftedIds`. You add
  the pure availability-board derivations here (see below), with unit tests.
- Backend: `backend/app/api/draft.py` (models + routes; you add team names, a config edit, and a
  full-board availability endpoint), `backend/app/db/models/draft.py` (add `team_names`),
  `backend/app/draft/availability.py` (`simulate_availability` — `candidates=None` already covers
  the whole available field-ranked board), `alembic/versions/e5c18b7a2f90...`/`f3a9c41d7b62...`
  (migration pattern), `backend/tests/test_api_draft.py`.

Branch: `task-22-draft-redesign` off `main` (must include T21b's merge). STAGE, DO NOT COMMIT. No
AI attribution / trailers.

STANDING CI RULE: keep userEvent Vitest fixtures SMALL (a 4-team draft, a handful of players) or set
`it(name, fn, 15000)`. Put the heavy derivations in `lib/draft.ts` as PURE functions and unit-test
those; keep component tests light.

---

## Part 0 — three small backend additions (with tests; one migration)

1. **Team names.** Add `team_names: dict | None` (JSON, slot-string → name) to the `Draft` model +
   one Alembic migration off the current head (`f3a9c41d7b62`), pure `add_column` (nullable), applies
   on SQLite + Postgres. `DraftCreate` accepts optional `team_names`. `DraftTeamRow` gains
   `name: str` — the stored name for that slot, or `"Team {slot}"` when unset. (Leave the log rows
   alone; the frontend maps slot→name from `teams[]`.) Names are cosmetic — no pick logic touches them.
2. **Change seat / names before the draft starts.** `PUT /draft/config`, body `{my_slot?,
   team_names?}`: updates `my_slot` ONLY when `picks_made == 0` (else 422 — "reconfigure to change a
   started draft's seat"), validates `1..team_count`; merges `team_names` at any time. Returns
   `DraftStateResponse`. 404 when no draft.
3. **Full-board availability.** `GET /draft/availability` → `{ pick_number, is_complete,
   availability: { <player_id>: <pct 0..1> } }` for EVERY still-available field-ranked player at MY
   next pick (`my_remaining_pick_numbers[0]`). Reuse `simulate_availability` with `candidates=None`
   (the whole available board — it's cheap, the cost is simulating opponent picks, not tracking) and
   `my_pick_numbers=[my next pick]`, default iterations. 404 no draft; when the draft is complete or
   I have no pick left, `is_complete=true` and `availability={}`. This is what powers availability in
   the sidebar and every rankings column, at any depth.
   Tests (in `test_api_draft.py`): availabilities in [0,1]; a top field player is less available than
   a deep one; covers players well past the old plan `size`; deterministic under a seed; complete →
   empty. Also test config: seat change works empty, 422 after a pick, names merge anytime; and
   `team_names` round-trips through create + `DraftTeamRow.name` defaults to "Team N".
   Keep `make test` + `make lint` green.

## Part 1 — `api.ts`
- `DraftTeamRow` gains `name: string`. `DraftCreateBody` gains `team_names?: Record<string,string>`.
- Add `DraftAvailabilityResponse` (`pick_number: number | null`, `is_complete: boolean`,
  `availability: Record<string, number>`) and `api.draftAvailability()` (GET /draft/availability;
  404 handled by caller).
- Add `api.updateDraftConfig(body: { my_slot?: number; team_names?: Record<string,string> })`
  (PUT /draft/config).
- Remove nothing from the plan types (the endpoint stays); just stop importing `draftPlan` in the UI.

## Part 2 — pure derivations in `lib/draft.ts` (unit-tested)
The sidebar and rankings view are built from the master board CATALOG (fetched once — ranks, tags,
positions, and `tiers[]` cut-ranks are static during a draft) minus the live drafted set, joined with
the availability map. Put this logic here, pure:
- `availableBoard(catalog: MasterPlayerRow[], drafted: Set<number>): MasterPlayerRow[]` — the ranked,
  non-excluded, still-available players in board-rank order.
- `positionColumn(available: MasterPlayerRow[], catalog, tiers, position): { rows, tierAtRank }` — the
  available players eligible at `position`, in board order, each tagged with its POSITION tier. The
  position tier is a band over the FULL position sub-order (including drafted): compute each player's
  full-position-rank = his index (1-based) among ALL catalog players listed at `position` sorted by
  board rank, then `tierAt(tiers[position].cut_ranks, fullPositionRank)`. Dividers appear where the
  tier increments down the AVAILABLE list. (Overall column uses the same idea with the `overall`
  scope and board rank directly.)
- A small helper to bucket by tier for rendering dividers, mirroring `tierBands`/`tierAt` from
  `lib/masterboard.ts` (reuse those; don't duplicate the band math).
Unit tests: available excludes drafted; a multi-position player appears in each of his columns; the
position tier is his band in the full position order, not the available-only order; empty position →
empty column.

## Part 3 — the `/draft` layout (frontend)
Reshape `DraftRoomPage` into: a persistent LEFT SIDEBAR + a MAIN PANEL with a tab bar. Remove
`DraftPlan`. Function-first, consistent with existing pages (Tailwind zinc, `Segmented`/`Segment`,
`tagStyle`). Fetch the master-board catalog once (already done); fetch `api.draftAvailability()` and
re-fetch it on every draft-state change (key on `picks_made`+`is_complete`, request-key guarded like
T21b's plan). `drafted` comes off the live state log.

### Sidebar (always visible, left, vertically scrollable)
- The available players in MY board-rank order, each row: rank, name, positions, the **target/fade
  tag chip** (`tagStyle`), availability % (from the map), and a **Draft button** DISABLED unless it's
  my pick on the clock (`state.is_my_pick && picks_away===0`, i.e. `state.is_my_pick`). Clicking drafts
  via `makePick`.
- A **search bar** (reuse `matches`) filtering the available list by name/team.
- A **"Targets only" toggle** (show only `tag==='target'`).
- **Position filter** chips (All · PG · SG · SF · PF · C) — multi-select; show players eligible at any
  selected position. Reuse the chip styling from the board/master pages.

### Main panel — a tab bar (default **Board**), four views:
1. **Board** — the snake grid, but ALL 10 team columns VISIBLE with no horizontal scroll: a fluid
   grid (`grid-cols-[2.5rem_repeat(var(--teams),minmax(0,1fr))]` or flex with `flex-1 basis-0
   min-w-0`), compact cells, TRUNCATED names, small type. Vertical scroll for the 20 rounds is fine.
   Keep the click-a-filled-cell-to-edit behavior and the my-column highlight / on-the-clock ring.
   Team headers show the team NAME (`teams[].name`), mine marked "(You)".
2. **List** — picks grouped by round (heading "Round N"); each row = the overall pick number + team
   name + player (e.g. "11 · Team 1 · A. Edwards"), in snake pick order within the round. Only made
   picks (or show the round's slots with blanks — your call, but made-picks-only is fine here).
3. **Roster** — grouped by team (heading = team name, "(You)" on mine); under each, that team's pick
   slots by overall pick number in ascending order, "pickNo. player" — and UNMADE picks still appear
   as "pickNo. ____" (the number with an empty name). No draft buttons. (A team's pick numbers =
   `pickNumbersFor(slot, teams, rounds)`.)
4. **Rankings** — SIX columns: Best available (all positions), then PG, SG, SF, PF, C. Each column is
   the available players for that scope in MY board-rank order (numbers = my board rank), with TIER
   BREAK dividers from my board (overall scope for column 1, the position scope for the others — from
   Part 2), a **Draft button** per row (disabled unless my pick), and the availability %. Built from
   the Part-2 derivations.

### Setup (`DraftSetup`) + seat change
- The setup form gains optional **team-name inputs** (one per seat; blank → "Team N") passed as
  `team_names` to `createDraft`.
- Before the draft starts (a draft exists with `picks_made===0`), expose a control to **change my
  seat** (and edit team names) that calls `api.updateDraftConfig` — no reset, no lost picks (there are
  none). After picks exist, seat change is only via the existing Reconfigure (full reset) path;
  disable/hide the in-place seat change then.

## Testing
- Backend: the Part-0 tests above.
- `lib/draft.ts`: the Part-2 pure derivations (small hand fixtures).
- Frontend (api MOCKED, SMALL fixtures): the four tabs render and switch (default Board); the board
  shows all team columns with no `min-w-max`/overflow-x scroll wrapper (assert the fluid grid, not the
  old fixed-width one); sidebar filters (search, targets-only, position chips) narrow the list; a
  Draft button is enabled only on my pick and calls `makePick`; availability refetches on a state
  change; roster view shows unmade pick slots as numbered blanks; list view groups by round with
  overall pick numbers; team names render (default "Team N", "(You)" on mine); seat-change control
  calls `updateDraftConfig` when empty and is gone once a pick exists.
- Keep all four green: `make test`+`make lint` (backend), `npm test`+`npm run build`+`npm run lint`.

## Constraints
- `api.ts` mirrors the backend exactly. Reuse `matches`, `tagStyle`/`TAG_CLASS`, `tierAt`/`tierBands`,
  `Segmented`/`Segment`, the request-key guard, the "server's board wins" commit flow. No new frontend
  deps. Heavy logic in `lib/draft.ts` (pure), components thin.
- One migration, portable. Don't disturb the master-board endpoint or the frozen tests. The plan
  endpoint stays (unused by the UI now) — don't delete it in this task.
- Board must fit 10 columns at the page's `max-w-[1400px]` with no horizontal scroll on a laptop;
  names truncate rather than force width.

## Acceptance criteria
1. Backend `uv run pytest -q` green (old + new); `make lint` clean; `alembic upgrade head` applies on
   fresh SQLite; single head after your revision.
2. `npm test` + `npm run build` + `npm run lint` green.
3. `/draft` default tab is Board; all 10 columns visible without horizontal scroll; tabs switch to
   List / Roster / Rankings; the old stacked plan panels are gone.
4. Sidebar: available players by my rank with tags + availability %, search + targets-only + position
   filters, Draft buttons enabled only on my pick.
5. Rankings view: 6 columns (overall + 5 positions) with my-board tier breaks, availability %, draft
   buttons; Roster view shows unmade pick slots as numbered blanks; team names + "(You)".
6. Seat can be changed before the draft starts (empty draft) via `PUT /draft/config`; 422 after a pick.

## Report back with
- Branch + file tree (backend additions + migration + tests; frontend api, `lib/draft.ts`, the
  `/draft` components, tests).
- Confirm STAGED not committed on `task-22-draft-redesign`; single alembic head; all four checks green.
- A DOM/words sketch of: the board header row (10 named columns, one "(You)"), a sidebar row (rank ·
  name · pos · tag · avail% · Draft), a Rankings PG column with a tier divider, and a Roster team
  block with one unmade slot.
- Decisions/deviations (e.g. how you fit 10 columns, single vs multi position filter).

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
