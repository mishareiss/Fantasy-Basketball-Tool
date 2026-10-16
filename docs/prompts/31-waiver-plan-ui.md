# Task 31 — Waiver plan UI: Actual/Plan toggle, plan roster, move composer with backups, plan list, warnings

Context: Task 30 (merged) built the waiver plan backend: `GET /team/plan`, `POST/PATCH/DELETE
/team/plan/moves`, `PUT /team/plan/acquisition-override`, `GET /team/day?view=plan`, `PUT
/team/plan/lineup/{sp}`, `in_plan` on free-agent rows, and ESPN auto-detect on sync. The TS types are
already in `frontend/lib/api.ts`. This task is the **UI** on the existing `/team` page. **Frontend only.**
If the API is genuinely missing something, stop and tell me.

Misha's agreed spec:
- **`Actual | Plan` toggle** in the roster header. Default **Actual**. It lives in the URL (`?view=plan`).
- **Plan view** shows my roster for the selected day *as if the plan had run*:
  - Planned pickups appear in their slots, clearly marked as not yet real: a "Planned" tag plus the move day,
    `or Draymond Green` under the name when the move has unresolved backups, and `Claim pending` when ESPN
    has a pending claim.
  - Players leaving that day show in a small "Dropped today" strip under the grid (`planned_drops`).
  - The day's `plan_warnings` render above the grid: warnings in amber, info in muted. They never block
    anything.
  - Slot moves work exactly as in Actual (same Move UI and legality), but save via `PUT
    /team/plan/lineup/{sp}` and never touch the Actual lineup. "Sync lineup from ESPN" is hidden in Plan
    view.
  - Lineup-source badge labels for `plan_edit` / `plan_carried` / `from_actual` exist in `lib/team.ts`
    already. Show `carried_from_date` ("Carried from Tue 10/20") now that the API returns it, in BOTH views.
- **Acquisitions meter** (both views): `used + planned / limit` for the day's matchup, e.g. `1 used + 3
  planned · 4/7`. Red when over. Show whether `used` comes from ESPN or an override.
- **Right panel becomes tabs: `Waivers | Plan`.** The waiver list is unchanged except:
  - rows that are in the plan get an `In plan · Wed` badge (from `in_plan`)
  - each row gets a **`+ Plan`** action that opens the move composer with that player as the primary pickup
- **Plan tab** (the full plan list):
  - Moves are grouped by matchup, with each matchup's acquisition row (`used`, `planned`, `limit`, over flag,
    and an **override control**: set or clear `used` for that matchup, inline, with a note that it
    overrides ESPN's count). Inside a matchup, moves are ordered by day then sequence.
  - Each move row reads like `Wed 10/21 · Drop Rudy Gobert → Add Jerami Grant (backups: Draymond Green)`, with
    a status chip (`Planned`, `Done ✓` (+ the actual pickup, if a backup was used), `Skipped`, `Overdue`,
    `Claim pending`).
  - Chained drops read `Drop move 1's pickup: Jerami Grant (or Draymond Green)`. A broken chain shows its
    `problem`.
  - Per-move warnings show inline under the row.
  - Row actions: Edit (opens the composer), ▲/▼ reorder within the same day (`sequence`), Mark done (pick
    which candidate was added), Skip, Restore to planned, Delete.
  - Delete with dependents (409) shows an inline prompt: "Move 3 drops this move's pickup. Repoint it to a
    named drop and delete?" → `DELETE ?repoint=true`. No browser dialogs anywhere.
  - Plan-level warnings that aren't tied to a move (e.g. `acq_limit`) show at the top of the tab.
- **Move composer** (a dialog, reusing `components/PlayerDialog.tsx`'s frame):
  - **Day**: defaults to the roster's selected day when opened from the roster, else the waiver panel's
    first selected chip, else today. Changeable.
  - **Drop**: a picker listing the PLAN roster for that day (fetch `GET /team/day?sp=&view=plan`), each with
    slot + game that day, PLUS "Pickup from move N: A (or B)" options for earlier moves that land on or
    before that day. Choosing one sends `drop_from_move_id`, so the chain follows whichever candidate ends up
    added.
  - **Pickups**: ordered list. Primary first, then backups. Add players via a search box over `GET
    /team/free-agents` (name `q`, honouring the day) with a quick "Add from watchlist" list. Reorder with
    ▲/▼, and remove. Each candidate shows his game that day (two-line cell) and FPTS/G.
  - **Note** (optional).
  - Save → `POST` or `PATCH`. On success, close and refetch the plan, the day (in the current view) and the
    waiver list (house rule: refetch after write). Show the response's warnings for that move in a toast-style
    inline note on the plan tab, not a modal.
  - Shape errors (422) render inline in the composer.
- **Entry points to the composer**:
  - `+ Plan` on a waiver row (pickup prefilled)
  - in **Plan view**, a `Drop…` action on each roster row (drop prefilled, day = selected day)
  - an `Add move` button on the Plan tab (empty)
  - Edit on a plan row
- After a sync marks a move done, the Plan tab shows it done (with a `different_drop` info if applicable).
  The UI needs nothing special for auto-detect beyond rendering status/warnings.

Read first:
- `backend/app/team/schemas.py` (`PlanMoveOut`, `PlanDropOut`, `PlanResponse`, `AcquisitionRow`,
  `PlanWarningOut`, `PlanFlag`, `PlannedDropOut`) + `backend/app/api/team.py` (plan endpoints) — the exact wire shapes
- `frontend/lib/api.ts` (plan types/calls added in task 30), `frontend/lib/team.ts`
- `frontend/components/team/*` (TeamPage, RosterTable + move mode, WaiverPanel, TeamPlayerCard),
  `frontend/components/PlayerDialog.tsx`, `frontend/__tests__/team*.test.ts(x)` + `teamFixtures.ts`
- `docs/HANDOFF.md` (refetch-after-write; no browser dialogs; CI timing rule)

Branch: confirm `waiver-plan` is merged into `main` and `main` is clean. Create `waiver-plan-ui` off `main`.
**Before starting, delete any Finder/iCloud duplicates** (`find . -name "* 2.*" -not -path "*/node_modules/*"
-not -path "*/.venv/*" -delete`, plus the same inside `frontend/.next`). They break the local build
(`frontend/lib/team 2.ts`) and Alembic (a duplicate migration is a second head). List what you deleted.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere. Re-run the duplicate `find` right before staging, and stage
none.

## Scope
1. `components/team/`: `ViewToggle` (Actual|Plan, URL-backed), `PlanRosterExtras` (planned tags, alternatives,
   dropped-today strip, day warnings), `AcquisitionMeter`, `RightPanelTabs`, `PlanTab` (+ `PlanMoveRow`,
   `MatchupAcquisitionRow` with override), `MoveComposer`. Extend `RosterTable` to take a `view` and route
   saves to the right endpoint. Extend `WaiverPanel` with the `In plan` badge and the `+ Plan` action.
2. Pure logic in `lib/team.ts` (or `lib/plan.ts`), unit-tested:
   - grouping moves by matchup and day
   - move-row sentence text (incl. chained/broken drops and resolved backups)
   - status-chip choice
   - the composer's drop options (plan roster + chainable earlier moves landing on/before the day)
   - candidate reorder/remove
   - the meter text and over state
   - which composer prefill each entry point produces
3. The right-panel tab choice and the composer's last-used day may persist in `localStorage` (try/catch).
   The plan itself is server state. Don't cache it client-side beyond the fetch.

## Testing (Vitest + RTL, api mocked, small fixtures)
- Toggle: `?view=plan` round-trip; default Actual; Plan hides Sync; Plan-view saves call `savePlanLineup`,
  never `saveLineup`.
- Plan roster: a planned pickup tagged with `or <backup>`; claim pending; the dropped-today strip; day warnings
  (warning vs info styling); `carried_from_date` badge in both views.
- Meter: under/over and the override source label.
- Plan tab:
  - grouping + ordering
  - the sentence for a named drop and a chained drop (unresolved "A (or B)", then resolved)
  - status chips incl. Done with a backup used
  - reorder sends the right `sequence`
  - Mark done with a candidate choice
  - Skip/Restore
  - Delete → 409 → inline repoint prompt → `?repoint=true`
  - override set/clear
- Composer:
  - each entry point's prefill
  - drop picker includes the chain options only for moves landing on or before the day
  - add pickup via search + from watchlist
  - reorder/remove
  - POST body exact (`drop_player_id` XOR `drop_from_move_id`, `candidate_ids` in order)
  - PATCH on edit
  - 422 inline
  - refetches after save
- Waiver panel: the `In plan` badge and `+ Plan` opening the composer with the right prefill.
- Keep everything green: `npm test`, `npm run lint`, `npm run build`, backend `make test` + `make lint`.

## Constraints
- Reuse the existing Move UI/legality, PlayerDialog frame, two-line game cell, Segment control, `request`/`ApiError`
  (incl. `details`). No new dependencies, no backend changes, no browser dialogs.
- Planned (not-yet-real) players must be visually unmistakable from rostered ones in Plan view, but don't rely
  on colour alone: use a text tag too.

## Acceptance criteria
1. All checks green.
2. On Misha's DB:
   - From the waiver list, `+ Plan` on Jerami Grant → composer → drop Rudy Gobert on Wed 10/21, add backup
     Draymond Green → save.
   - Then `Add move` for Fri 10/23 → drop "Pickup from move 1" → add Cam Whitmore → save.
   - Plan view on Thu 10/22 shows Grant tagged Planned with "or Draymond Green" in Gobert's slot.
   - The Plan tab shows both moves under Matchup 1 with the meter at `0 used + 2 planned · 2/7`.
   - Mark move 1 done with Draymond Green, and move 2's row now reads "Drop … Draymond Green".
   - Clean up: delete both moves (repoint flow on the first delete) so the plan is empty again.
3. Actual view is unchanged from task 29a (the existing team tests pass untouched, except where a test
   deliberately covers the new toggle).

## Report back with
- Branch, file tree, and confirmation it's STAGED, not committed. The duplicates deleted.
- Screenshots: Plan view with a planned pickup + dropped-today strip + a warning; the Plan tab with two
  matchups and an over-limit meter; the composer with a chained drop option and two pickups.
- Decisions/deviations, and frontend test counts before → after.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
