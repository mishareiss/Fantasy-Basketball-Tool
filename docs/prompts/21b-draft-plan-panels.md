# Task 21b — Draft plan panels + My Board draft mode (frontend; final draft-plan task)

Context: The draft room exists (T21a: `/draft` with the snake board, search pick entry, sim
controls, edit-pick). The backend plan + drafted-annotation are done (T20: `GET /draft/plan`,
and `GET /master/board?draft_mode=&hide_drafted=`). This task wires those two into the UI and
COMPLETES the draft-plan builder. It is frontend-only — no backend changes, no migration.

Read these first:
- `backend/app/api/draft.py` — the plan models `DraftPlanResponse`, `PlanPickRow`, `PlanPlayerRow`
  and the `GET /draft/plan` route (params `iterations`, `seed`, `picks`, `size`; 404 when no draft;
  empty `picks` when the draft is complete; `availability` in [0,1], non-increasing across later
  picks; `targets` are my `tag=='target'` still-available, `best_available` is the top of my board
  still available). `api.ts` mirrors these EXACTLY.
- `backend/app/api/master.py` — `MasterPlayerRow` now carries `drafted`, `drafted_by_slot`,
  `drafted_by_me` (default false/None), and `GET /master/board` takes `draft_mode` / `hide_drafted`
  (both default false; OFF = the endpoint is unchanged). Mirror the three fields + the two params.
- `frontend/lib/api.ts` — the typed client; add the plan types + `api.draftPlan(...)`, and extend
  `MasterPlayerRow` (+3 fields) and `api.masterBoard(...)` (the two new params). Same doc-comment
  style; field names/nullability copied, not invented.
- `frontend/components/draft/DraftRoomPage.tsx` — where the plan panels slot in. Note its `commit()`
  flow (every write replaces state from the response), the `pending` request-key ref, the
  `drafted`/`catalog` reads, `makePick(playerId)`, and `live.is_my_pick` / `live.next_pick_number`.
- `frontend/components/masterboard/MasterBoardPage.tsx` + `MasterRow.tsx` — the board page's
  request-key `settled` guard, its `reload` counter, the horizon/position `Segmented` controls, and
  how a row renders. You add a draft-mode control here and pass the new row fields to `MasterRow`.
- `frontend/lib/masterboard.ts`, `frontend/components/board/BoardControls.tsx` (Segmented/Segment),
  `frontend/__tests__/masterboard.test.tsx` + `draft.test.tsx` (Vitest/RTL mocking patterns).

Branch: `task-21b-draft-plan-ui` off `main` (main is at the merged T21a head, 93722e5).

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT. Misha commits/merges/pushes himself.
NO AI attribution / Co-Authored-By / session trailers anywhere.

STANDING CI RULE: keep userEvent-driven Vitest fixtures SMALL (a handful of players / a 4-team
draft), or set an explicit `it(name, fn, 15000)` timeout. Do not render a full-depth fixture in a
userEvent test.

---

## Decisions already made (implement these)
1. **Panels show my next pick + a few ahead** — request `GET /draft/plan?picks=4` by default (next
   pick prominent, plus ~3 more), with a "show more" that raises `picks`. Not all 20 by default.
2. **Auto-refresh after every pick** — the plan refetches whenever the draft state changes (any
   pick / advance / undo / reset). Keep it responsive: use the endpoint's default `iterations`
   (1000) for a ~4-pick depth, and pass a smaller `iterations` only if it feels slow. Guard it with
   a request key so a slow older response can't land on top of a newer one (mirror the room's
   `pending` ref).
3. **A panel click drafts only on my pick** — a `PlanPlayerRow` is clickable (→ `makePick`) only
   when `live.is_my_pick` AND that panel is the current pick (`picks_away === 0`). Otherwise the
   panels are read-only reference (still show availability). Opponent picks stay on the search box /
   advance.
4. **My Board draft mode is a 3-state control** — Off / Show drafted / Hide drafted, mapping to
   `{}` / `{draft_mode:true}` / `{draft_mode:true, hide_drafted:true}`. Default Off, so the board is
   exactly as it is today until you opt in.

## Scope

### 1. `api.ts`
- Add plan types `PlanPlayerRow`, `PlanPickRow`, `DraftPlanResponse` (mirror the pydantic), and
  `api.draftPlan(params?: { picks?: number; size?: number; iterations?: number; seed?: number })`
  → `GET /draft/plan` (404s when no draft — the caller handles that).
- Extend `MasterPlayerRow` with `drafted: boolean` (default false), `drafted_by_slot: number | null`,
  `drafted_by_me: boolean` (defaults matching the backend), documented as annotation-only fields that
  are false/null unless the request asked for draft mode.
- Extend `api.masterBoard` to accept `draft_mode?` and `hide_drafted?` and pass them via `query()`.
  Keep the existing positional `(horizon, position)` call sites working (add an options arg or
  optional params — don't break `masterBoard()` with no args, which the draft room's catalog uses).

### 2. The plan panels on `/draft` (`components/draft/DraftPlan.tsx` + rows)
- A `DraftPlan` section rendered in `DraftRoomPage` (below `OnTheClock`/controls, above or beside the
  board — your call, function-first, consistent with the page). It fetches `api.draftPlan({picks})`
  and re-fetches on every draft-state change (key the effect on `live.picks_made` + `live.is_complete`,
  request-key guarded). Show a subtle "updating…" state while it recomputes; a failed plan is a small
  notice, not a page error (the board still works) — mirror `CatalogUnavailable`.
- For each `PlanPickRow`: a header (pick number, round, `picks_away` — "on the clock" when 0), the
  `open_needs` chips, then TWO lists: **Targets** (`targets`) and **Best available** (`best_available`).
  Each `PlanPlayerRow` shows the name + positions, my `rank`/`tier`/`tag`, `field_rank`, `fills_need`,
  and **availability** as a clear visual (a % plus a small bar or a colour ramp — high availability
  reads calm, low reads urgent; never colour-only, keep the number). Reuse `tagStyle` from
  `lib/masterboard.ts` for the tag.
- Clickable to draft only under the rule in decision 3; when not clickable, render as reference
  (no button, or a disabled affordance) so nobody expects a click to do anything.
- `is_complete` → no panels (a "draft complete" note). No draft shouldn't happen here (the room only
  renders when a draft exists), but a 404 from the plan degrades to the same small notice.

### 3. My Board draft mode (`MasterBoardPage.tsx` + `MasterRow.tsx`)
- Add a `Segmented` draft-mode control (Off / Show drafted / Hide drafted). Its value is part of the
  request (fold `draft_mode`/`hide_drafted` into the `masterBoard` call AND into the `settled` request
  key, so switching it refetches and a stale response can't land).
- `MasterRow` gets `drafted` / `drafted_by_me` (and slot) props: a drafted row reads as taken —
  dimmed / struck name + a small "drafted" chip, and a distinct "yours" chip when `drafted_by_me`. In
  "Show drafted" the drafted rows stay in place (clearly marked); in "Hide drafted" they're gone
  (the backend omits them). Off = no annotation, no change.
- REFETCH-AFTER-WRITE (the carry-forward): the draft-mode annotation is only on `GET /master/board`,
  so a write (`putMasterOrder` / `putMasterEntry` / `putMasterTiers`) returns UN-annotated rows. When
  draft mode is ON, do NOT render the write's response as the board — trigger a refetch (bump the
  `reload` counter / re-run the GET with the draft params) so the annotations come back. When draft
  mode is OFF, the existing "replace from the write response" path is unchanged. Reordering is already
  disabled under a position filter; leave drag/reorder behavior otherwise as-is (a drafted player can
  still be reordered — draft mode is a lens, not a lock).

## Testing (Vitest/RTL; extend `draft.test.tsx`, `masterboard.test.tsx`; SMALL fixtures)
- api mirrors: `draftPlan` hits the right URL with params; `masterBoard` includes `draft_mode`/
  `hide_drafted` only when set; the new `MasterPlayerRow` fields parse.
- Plan panels: render targets + best_available with availability for the next few picks; refetch is
  triggered when the draft state changes (e.g. after a mocked `applyPick`, `draftPlan` is called
  again); a panel row is clickable and calls `makePick` only when it's my pick and `picks_away===0`,
  and is inert otherwise; `is_complete` shows the complete state and no panels.
- My Board draft mode: switching the control refetches with the right params; drafted rows are
  annotated in "Show drafted" and absent in "Hide drafted"; OFF renders the board unchanged (guard:
  assert an existing off-mode board test still passes); after a mocked write in draft mode, the page
  refetches the board (annotations preserved) rather than showing the write's un-annotated response.

## Constraints
- `api.ts` types mirror the backend exactly. Reuse `request`/`query`, `ApiError`, `Segmented`/
  `Segment`, `tagStyle`, the request-key guard. No new frontend dependencies.
- Function-first, consistent with the existing pages. Availability visuals: legible in light and dark,
  never colour-only.
- Keep all four green: `make test` + `make lint` (backend, unchanged), `npm test` + `npm run build` +
  `npm run lint` (frontend). No backend edits, no migration.

## Acceptance criteria
1. `cd frontend && npm test` passes; `npm run build` succeeds; `npm run lint` clean. Backend untouched
   (`make test` still 891, `alembic heads` single) — confirm you changed no backend files.
2. `/draft` shows the plan for the next pick + a few ahead, auto-refreshes after every pick, and a
   panel click drafts only on my pick.
3. `/my-board` draft-mode control: Off is byte-unchanged; Show drafted marks drafted rows (and my
   own); Hide drafted omits them; a write while draft mode is on refetches so annotations survive.

## Report back with
- Branch + file tree (frontend only: `lib/api.ts`, `components/draft/DraftPlan*`, the
  `DraftRoomPage` wiring, `MasterBoardPage`/`MasterRow` edits, tests).
- Confirm STAGED, NOT committed, on `task-21b-draft-plan-ui`; no backend files changed; all four
  checks green.
- A trimmed screenshot-in-words or DOM sketch of one plan panel (a pick with its two lists +
  availability) and a My-Board row in "Show drafted".
- Decisions/deviations. This is the last task in the draft-plan builder — note anything you'd flag
  for the (deferred) whole-app design/UX polish pass.

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
