# Task 18 — My Board: tier UI + position filter (frontend)

Context: Task 17 shipped the tier + position-filter BACKEND on the Master Ranking board. This task is
the UI: draw tier dividers, let Misha adjust them, and filter the board by position (which also switches
which tier scope is shown). Frontend only — do NOT change the backend (if you think you must, stop and
say why). The board's drag-reorder / tags / notes / exclude already exist from task 16; this extends it.

(Note: the "draft mode / hide drafted" toggle is NOT in this task — it needs the drafted-state the draft
engine produces, so it lands with the draft room, task 21.)

PRECONDITION / branch: tasks 16-17 on `main`. `git checkout main`, confirm frontend/app/my-board/page.tsx
and backend/app/api/master.py's tier fields exist, then `git checkout -b board-tiers-ui`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Read FIRST:
- backend/app/api/master.py — the EXACT shapes to mirror: `MasterPlayerRow` now carries `overall_tier`,
  `position_tier`, `position_scope`; `MasterBoardResponse` carries `position` and the per-scope tier
  structure (the `TierScopeRow` list — scope / size / `cut_ranks` / `tier_count`; read the file for the
  field's exact name). `GET /master/board?horizon=&position=`; `PUT /master/tiers` body {scope,
  cut_ranks}; `POST /master/tiers/reseed?scope=`. `cut_ranks` are tier START ranks within that scope's
  order, always beginning with 1 (`[1,4,12]` = tiers 1-3 / 4-11 / 12-N), and that's exactly what PUT
  takes back. Scopes: 'overall' + POSITIONS (PG/SG/SF/PF/C).
- frontend/components/masterboard/* (MasterBoardPage, MasterRow, SetAsideTray, MasterStates) — the page
  to extend. frontend/lib/masterboard.ts (PAGE window, moveTo, renumber, edge, tagStyle) and
  frontend/lib/api.ts (`masterBoard`, `putMasterOrder`, `putMasterEntry`, `MasterBoardResponse`/
  `MasterPlayerRow` types). frontend/components/board/BoardControls.tsx (the exported Segmented/Segment
  control) and lib/board.ts (POSITIONS, Horizon). __tests__/masterboard.test.tsx for the RTL patterns.

Scope
1) api client (lib/api.ts): extend `MasterPlayerRow` with overall_tier / position_tier / position_scope
   and `MasterBoardResponse` with `position` + the tier-scope structure, mirroring master.py. Add
   `position` to `masterBoard(horizon?, position?)`. Add `putMasterTiers(scope, cutRanks)` →
   PUT /master/tiers and `reseedMasterTiers(scope)` → POST /master/tiers/reseed?scope=. Reuse
   request/put/post + ApiError.
2) POSITION FILTER: a chip/segmented control — All · PG · SG · SF · PF · C (reuse Segmented/Segment).
   Selecting a position refetches `masterBoard(horizon, position)` (only that position's players, in
   board order, overall ranks intact) AND switches the displayed tier scope to that position; "All"
   shows the whole board and the 'overall' scope. Persist the choice in state (not the URL is fine).
3) TIER DIVIDERS: from the ACTIVE scope's `cut_ranks`, render a labeled divider ("Tier 2", etc.) before
   the row at each cut rank, within the windowed list. A divider shows the band it opens; keep it
   visually distinct from a player row. Misha can:
   - MOVE a divider: drag it to another gap, or ▲/▼ it one row (like MoveTo complements drag) →
     recompute the scope's cut_ranks and `putMasterTiers(scope, cutRanks)`.
   - ADD a break: an affordance between two rows ("+ tier break here") inserts a cut at that rank.
   - REMOVE a break: an × on the divider merges it into the tier above.
   - RESET: a "Reset tiers to auto" action per scope → `reseedMasterTiers(scope)` (behind a confirm,
     since it discards manual dividers). All of these send/replace the whole `cut_ranks` list (which
     must stay sorted, unique, and start with 1 — the backend 422s otherwise; keep the client valid so
     that never fires).
   Cut ranks are within the active scope: in All they're board ranks, in a position view they count that
   position. Place dividers by the row's rank within the shown order.
4) REORDER SCOPE: keep task-16 reorder (drag / ▲▼ / move-to-#) working in the ALL view. In a
   position-filtered view, DISABLE order editing (a filtered sub-order can't express a full-board move
   cleanly) — tags, notes, exclude, and tier-divider editing stay available. Say this in a small hint.
   (Flag: if Misha wants in-position reordering later, it's a follow-up.) Player drag and divider drag
   must not collide — distinguish the drag types.
5) Show each row's tier where useful (e.g. a subtle tier pill), and keep window/search/horizon from
   task 16 working with dividers interleaved. Loading / error / a saved indicator on tier writes.

Testing (frontend Vitest/RTL, api mocked; ADD tests; keep npm test + npm run build + make test + make
lint + CI green):
- dividers render at the active scope's cut_ranks with correct labels; a player row in the tier-1 band
  shows tier 1.
- moving a divider (▲/▼ or drag) calls putMasterTiers with the corrected cut_ranks; adding a break
  inserts the right rank; removing one drops it; the list stays sorted/unique/leading-1.
- reset calls reseedMasterTiers behind a confirm.
- position chips refetch with `position=` and switch the shown tier scope; "All" restores overall;
  order editing is disabled under a position filter but tag/note/exclude still work.
- player reorder still works in All view and doesn't trigger a tier write; window + search + horizon
  still behave; loading/error states.

Constraints: frontend only — NO backend changes. Reuse lib/api.ts, lib/masterboard.ts, lib/board.ts,
the Segmented/Segment control, and the masterboard components + __tests__ patterns — do NOT fork a
second api client, board builder, or control. Keep client-sent cut_ranks always valid (sorted, unique,
leading 1). No new deps (native HTML5 DnD). Match the existing Tailwind + light/dark theme.

Acceptance criteria (verify before reporting done):
1. My Board shows tier dividers from the backend's cut_ranks, labeled, with each row's tier reflecting
   its band; reordering a player into a higher band changes his tier with no tier write.
2. I can move / add / remove a divider and reset a scope to auto; each persists via the tier API.
3. The position filter narrows the board and switches to that position's tiers; "All" restores overall.
4. Task-16 behavior (reorder in All, tags, notes, exclude, window, search, horizon) is intact. No
   backend files changed. `npm test`, `npm run build`, `make test`, `make lint`, and CI all pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed; which branch you cut from; confirm zero backend changes.
- How dividers are edited (move/add/remove) and how cut_ranks are recomputed and kept valid; how the
  position filter drives both the fetch and the tier scope; how player-drag vs divider-drag are kept apart.
- The flow (screenshot if you can run it; else RTL coverage) for a divider move, a position switch, and a reset.
- Tests added; deferred: draft-mode toggle (task 21), in-position reordering, the draft engine (task 19+).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
