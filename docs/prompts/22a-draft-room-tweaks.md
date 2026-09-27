# Task 22a — Draft room follow-up: Teams sidebar tab, next-pick availability, drop board search

Context: T22 (draft-room redesign) is built and STAGED on `task-22-draft-redesign`, NOT yet
committed. These are three refinements Misha wants folded into T22 BEFORE he commits/merges, so
**work on the SAME branch `task-22-draft-redesign`** — do not create a new branch, keep everything
staged (`git add -A`), do not commit. NO AI attribution / trailers.

Read first:
- `frontend/components/draft/DraftRoomPage.tsx` — the room. Note `makePick(playerId)` drafts for
  `state.on_the_clock`; `canDraft = live.is_my_pick && !busy` (line ~356); the board-level
  `PickSearch` (label "Your pick …" / "Pick N for team X", `onChoose={makePick}`, line ~408) —
  that is the "search bar above the board" to REMOVE; a second `PickSearch` in the edit-pick panel
  (~441) STAYS (editing a made pick still needs a search).
- `frontend/components/draft/DraftSidebar.tsx` + `AvailableRow.tsx` — the sidebar (search + targets
  toggle + position chips + availability + Draft button gated by `canDraft`).
- `frontend/lib/draft.ts` — `availableBoard`, `boardColumn`, `filterAvailable`, `availabilityOf`.
  You add the pure roster-fill here.
- `backend/app/api/draft.py` — `get_draft_availability` (uses `_remaining(state)[0]` as the target)
  and `_remaining`; `backend/app/draft/needs.py` — `RosterFill.add` is the greedy slot rule to mirror
  in the frontend (eligible dedicated slot in PG/SG/SF/PF/C order, else UT, else bench).
- `frontend/lib/api.ts` — `DraftStateResponse`/`DraftTeamRow` (`team_slot`, `name`, `player_ids`),
  `roster_slots`, `DraftAvailabilityResponse` (`pick_number`).

Keep all four green: `make test` + `make lint` (backend), `npm test` + `npm run build` +
`npm run lint` (frontend). CI rule: small Vitest fixtures / explicit timeouts.

---

## 1. Sidebar "Teams" tab
The sidebar gets a top-level two-way toggle: **Available** (the current view — search, targets-only,
position chips, availability, Draft buttons) and **Teams** (new). Default is Available.

In the Teams view, the Available filters are REPLACED by a single **team dropdown**, defaulting to my
seat (`state.my_slot`), listing every seat by its name (`teams[].name`, mine as "… (You)"). Selecting
a team shows THAT team's roster laid out in slots, in this order from `state.roster_slots`:
the dedicated starters PG, SG, SF, PF, C, then UT × `roster_slots.UT`, then Bench × `roster_slots.BE`.
Each FILLED slot shows the **slot label** (PG/SG/SF/PF/C/UT/BE) and the player's name — NO pick number,
no availability, no draft button. Each EMPTY slot shows the slot label with a blank where the name
would go. Switching the dropdown re-renders for that team.

Slot assignment is a PURE function in `lib/draft.ts`, mirroring `backend/app/draft/needs.py`
`RosterFill.add`: for each of the team's drafted players in draft order (`teams[].player_ids`, whose
positions come from the catalog by id), fill an eligible OPEN dedicated slot (PG/SG/SF/PF/C order)
first, else a UT slot, else bench. Return the ordered list of slots with their occupant (or null).
Unit-test it: a PG/SG taken while both open takes PG; UT absorbs an overflow guard; bench catches the
rest; a partially-drafted team shows the right blanks. (Players not in the catalog — shouldn't happen
in the draft universe — render by id as a fallback, don't crash.)

## 2. Availability targets my NEXT pick when I'm on the clock
Change `GET /draft/availability` (backend) so the target pick is:
- `_remaining(state)[1]` when it's my pick right now (`is_my_pick` / I'm on the clock — `_remaining[0]`
  is the pick I'm making, so everyone is trivially 100% at it and that's useless), i.e. the pick AFTER
  this one that I own;
- `_remaining(state)[0]` otherwise (my upcoming pick, as today).
Edge: if it IS my pick and I have no later pick (`len(_remaining) < 2` — I'm on the clock at my last
pick), there is nothing to wait for → return `is_complete=true`, `pick_number=null`, `availability={}`
(same shape as a finished draft). The response already carries `pick_number`; the frontend labels the
sidebar/rankings availability with it ("chance he lasts to pick N") so it's clear the number is about a
FUTURE pick, not the current one. Add/adjust tests in `test_api_draft.py`: on my pick, the target is my
following pick and a top player is < 100%; not on my pick, unchanged; on my last pick while on the
clock → complete/empty. No migration.

## 3. Remove the board search; the sidebar enters every pick
Remove the board-level `PickSearch` (the one above/near the board, ~line 408). The sidebar's Available
search is the only player search now. BECAUSE that board search was the sole way to enter an OPPONENT's
pick in manual mode, the sidebar Draft button must now cover it:
- Change the enable rule from "my pick only" to **"whenever a seat is on the clock and we're not busy"**:
  `canDraft = !busy && !live.is_complete && live.on_the_clock !== null`. `makePick` still drafts for
  `on_the_clock` (omit `team_slot`), so one Draft click enters the current seat's pick whoever it is.
- The Draft button / sidebar header should say whose pick it is when it's not mine — e.g. button label
  or a sidebar note "Pick 12 · Team 7" vs "Your pick". This keeps manual mode fully usable (find in the
  sidebar → Draft for the seat on the clock) and simulation mode unchanged (Advance still auto-picks
  opponents; the button is simply also available for a hand-entered pick).
- Keep the edit-pick `PickSearch` panel (editing a made cell) exactly as is.
This reverses T22's "Draft disabled unless my pick" gating on purpose — with the board search gone the
sidebar is the single entry surface, so its button has to work for the seat on the clock.

## Testing
- `lib/draft.ts` roster-fill unit tests (above).
- Frontend (mocked api, small fixtures): the sidebar toggles Available/Teams; Teams defaults to my seat,
  the dropdown switches teams, filled slots show position+name and empty slots show the label blank, no
  pick numbers; the board search is gone (assert it's not rendered) while the edit-pick search remains; a
  sidebar Draft click enters a pick for the on-the-clock seat when it's NOT my pick (manual entry), and
  the button is disabled only when busy/complete.
- Backend: the availability target tests above.

## Acceptance criteria
1. Backend `uv run pytest -q` green; `make lint` clean; `alembic heads` still single (no migration).
2. `npm test` + `npm run build` + `npm run lint` green.
3. Sidebar has Available/Teams tabs; Teams shows a team's slot roster (PG/SG/SF/PF/C/UT/UT/BE…) with
   position labels and blanks, switchable by dropdown, defaulting to my seat.
4. When I'm on the clock, sidebar/rankings availability % is computed for my FOLLOWING pick (labeled
   with that pick number), not the current one.
5. The board search is removed; opponent picks in manual mode are entered from the sidebar (Draft
   button works for the on-the-clock seat); the edit-pick search still works.

## Report back with
- The files touched (same branch, `task-22-draft-redesign`); confirm STAGED not committed; four checks green.
- A DOM/words sketch of the Teams sidebar (my roster, one empty UT slot) and the sidebar header/button
  when it's an opponent's pick.
- Confirm the board search is gone and manual opponent entry works via the sidebar.

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
