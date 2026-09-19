# Task 16 — Master Ranking page (frontend: the drag-reorder board)

Context: Task 15 shipped the Master Ranking BACKEND (MasterRankEntry + the /master API). This task is
the PAGE — Misha's own draft board, where his hand-ordered list lives. Frontend only; the API is
complete, so do NOT change the backend (if you think you need to, stop and say why in your report).

The interaction was chosen from a mockup: REORDER. His explicit order IS the board; drag a player to a
spot and he stays there while the consensus underneath him moves. The consensus is a REFERENCE column
beside each player, not the order. Design/feel reference (already reviewed with Misha):
https://claude.ai/artifact/BBwmwpkBmn7XytDkECPES5

PRECONDITION / branch: task 15 must be merged to `main` first (main will contain app/api/master.py,
app/ranking/master.py, app/db/models/master_rank.py). `git checkout main`, confirm those exist, then
`git checkout -b master-page`. (If task 15 is NOT yet merged, branch off `master-ranking` and say so.)

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Read FIRST:
- backend/app/api/master.py — the EXACT response/request shapes to mirror in TS: `MasterBoardResponse`
  (horizon, ranking_horizon, seed_horizon, pool_size, total_ranked, seeded, the ranked entries list,
  and the `set_aside` list), `MasterPlayerRow` (rank, espn_player_id, name, nba_team, positions, age,
  tag, note, excluded, is_new, is_stale, consensus_rank, delta, updated_at), the `PUT /master/order`
  body (`ordered_player_ids`), `PUT /master/entries/{player_id}` body (tag?, note?, excluded?),
  and `MASTER_TAGS` (the allowed tags — 'target' | 'fade'). Copy names/nullability, don't invent.
- frontend/components/market/* (MarketPage, PlayerLines, AddLineForm, MarketStates) — the task-14
  full-page-with-mutations pattern to mirror (container fetches, sub-components render, a States file
  for loading/error/empty). frontend/app/market/page.tsx + app/import/page.tsx for the route shape.
- frontend/lib/api.ts — the `api` client and its `request`/`put`/`post` helpers, and the market client
  methods (`marketLines`/`putMarketLine`/...) as the shape to mirror. frontend/lib/board.ts
  (`Horizon`, `HORIZON_LABEL`, `otherHorizon`, `compareBy`) and lib/format.ts. frontend/app/layout.tsx
  (the `<nav>` — add the new page). frontend/components/board/* for table/row/delta styling to stay
  visually consistent. frontend/__tests__/{market,board}.test.tsx + fixtures.ts for the Vitest/RTL
  patterns.

Scope
1) api client (lib/api.ts): TS types for `MasterBoardResponse` / `MasterPlayerRow` mirroring master.py,
   and methods: `masterBoard(horizon?)` → GET /master/board; `putMasterOrder(orderedPlayerIds)` →
   PUT /master/order; `putMasterEntry(playerId, {tag?, note?, excluded?})` → PUT /master/entries/{id};
   `resetMasterBoard()` → POST /master/seed?reset=true. Reuse request/put/post + ApiError.
2) Route `/my-board` (frontend/app/my-board/page.tsx → a `MasterBoardPage` under
   components/masterboard/*). Add it to the nav in app/layout.tsx (label "My Board", after Board).
3) The board (opens populated — task 15 seeds on first GET):
   - One row per ranked player: YOUR rank (big, tabular), the player (name · pos · age · team), the
     CONSENSUS reference (consensus_rank + a colored delta chip — positive/▲ = you have him higher than
     the field, negative/▼ = lower; empty when is_stale), his tag chip, and row controls.
   - REORDER: a drag handle (HTML5 drag) to move a row, PLUS ▲/▼ nudge buttons AND a "move to #" number
     input — because drag alone can't span a 1000-deep board, the number jump and search (below) are how
     big moves happen. On any reorder, optimistically update, then `putMasterOrder` with the FULL
     non-excluded order (not just the visible window), and reconcile from the returned board
     (authoritative — it reflows ranks and may surface is_new).
   - TAG: cycle none → target → fade → none via `putMasterEntry({tag})`. NOTE: an inline editable note
     per player via `putMasterEntry({note})` (save on blur). EXCLUDE: `putMasterEntry({excluded:true})`
     → he moves to a "Set aside" tray; RESTORE from the tray → `putMasterEntry({excluded:false})` (the
     backend re-inserts him at his consensus slot).
   - BADGES: `is_new` (a rookie/arrival the reconcile just placed) and `is_stale` (on your board, no
     source ranks him now) each get a small, distinct badge.
   - HORIZON toggle (dynasty / current_year, reuse `Horizon`/`HORIZON_LABEL`): refetch the board; the
     ORDER must not change (assert this), only the reference column + delta.
4) DEPTH — the board is ~1000+ players. Do NOT render all of them at once. Default to showing the top
   N (make N a small constant, e.g. 175, with a "show more"/expand), plus a player SEARCH/filter box to
   find and jump to anyone by name. Keep the full order in state so `putMasterOrder` always sends the
   whole thing. Keep interactions snappy at this size (a dense, un-virtualized list of ~175 is fine; the
   full list behind search is not all in the DOM at once).
5) States: loading, error (ApiError message), and a subtle "saved" indicator after a successful
   mutation. A "Reset to consensus" action (calls resetMasterBoard) behind a confirm, since it wipes
   manual order — mirror the market page's destructive-action confirm.

Testing (frontend Vitest/RTL, api mocked; ADD tests; keep npm test + npm run build + make test + make
lint + CI green):
- board renders rows with rank, consensus_rank + delta (sign/colour), tag, and badges (is_new/is_stale
  from a fixture).
- reorder: a ▲/▼ (and a "move to #") call `putMasterOrder` with the FULL reordered id list and the UI
  reflects the returned board; drag-drop reorders (fire the drag events RTL-style or test the reorder
  handler directly).
- exclude moves a player to the Set-aside tray and calls putMasterEntry({excluded:true}); restore calls
  ({excluded:false}) and he leaves the tray.
- tag cycles through MASTER_TAGS and back to none; note edits save on blur.
- horizon toggle refetches and the ORDER is unchanged while the reference column changes.
- search filters the list; loading/error/empty states; reset behind a confirm.

Constraints: frontend only — NO backend changes. Reuse lib/api.ts (request/put/post/ApiError),
lib/board.ts (Horizon/HORIZON_LABEL/compareBy), the components/board delta+table styling, and the
components/market page pattern + __tests__ patterns — do NOT fork a second api client, horizon helper,
or table style. No new deps (implement drag with native HTML5 DnD, not a library). Match the existing
Tailwind + light/dark theme. Keep the mockup's feel but ship it in the app's real styling.

Acceptance criteria (verify before reporting done):
1. `/my-board` is in the nav and opens on the seeded board; each row shows your rank, the consensus
   reference + delta, tag, and any is_new/is_stale badge.
2. I can reorder by drag, by ▲/▼, and by "move to #"; the move persists (survives a reload) via
   putMasterOrder sending the full order.
3. I can tag (target/fade), write a note, exclude → Set aside → restore; each persists.
4. The horizon toggle changes the reference column but never the order. Search finds a player.
5. NO backend files changed. `npm test`, `npm run build`, `make test`, `make lint`, and CI all pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed; which branch you cut from; confirm zero backend
  changes.
- How reorder persistence works (full-order PUT + reconcile) and how depth/search is handled at ~1000
  rows.
- The flow (a screenshot if you can run the app; otherwise the RTL coverage) for reorder / exclude /
  tag / note / horizon.
- Tests added; anything deferred (the draft-plan builder is a later task; per-source weighting).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
