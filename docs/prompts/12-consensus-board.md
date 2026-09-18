# Task 12 — Multi-source consensus board

Context: Builds on task 11 (importer + ranking horizon tag), merged to `main` (= cdf6914). The
working checkout is currently on branch `importer-ui`, so FIRST `git checkout main`, then
`git checkout -b consensus-board`. This is the CORE of the ranking-board design: make the board show
MULTIPLE ranking sources side by side with a consensus, instead of only ESPN's projection.

Read FIRST (paths are relative to `backend/` and `frontend/`):
- backend/app/api/players.py — the board: `ranked_board()`, `BoardResponse`/`BoardRow`, the
  horizon/position/limit params, the `HORIZONS` constant, and the draftable-pool logic. The board
  route is `GET /players/board` (players router prefix `/players`).
- backend/app/valuation/ — `value_player` + `DynastyCurve` (engine.py, curve.py) and `assign_tiers`
  (tiers.py). (`ranked_board()` itself lives in players.py, not here — it CALLS valuation.)
- backend/app/db/models/{projection,adp,ranking,player}.py — note: `Projection.source` (also has
  `kind` = 'projected_season' and `season`; today there is one kind, so treat a value source as one
  per distinct `Projection.source`); `AdpEntry.adp`/`source`/`season`; `RankingSet.horizon`
  ('dynasty'|'redraft') and `RankingEntry.rank` (ordered).
- backend/app/api/rankings.py, backend/app/ranking/__init__.py (the stub package to build in),
  backend/app/scoring/.
- frontend/components/board/* — the real board lives here: `BoardPage.tsx`, `BoardView.tsx`,
  `BoardControls.tsx`, `BoardTable.tsx`, `BoardStates.tsx`, `BoardMeta.tsx`,
  `CalibrationInspector.tsx` (app/page.tsx just mounts `BoardPage` under Suspense). Also
  frontend/lib/api.ts (the `api` client, `Horizon` type, `HORIZONS`, `BoardRow`), lib/board.ts,
  lib/format.ts. Vitest tests live in frontend/__tests__/ (board.test.tsx, importer.test.tsx,
  api.test.ts, fixtures.ts) with frontend/vitest.setup.ts — mirror those patterns.

Private ESPN fantasy basketball dynasty tool, POINTS scoring. `market_line`, per-player overrides,
and per-source WEIGHTING are LATER tasks — this is EQUAL-WEIGHT consensus over the sources that exist
today (ESPN projection, ESPN ADP, imported rankings).

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

TWO HORIZON VOCABULARIES — do not conflate them (this is the one trap here):
- The BOARD's VALUE horizon is `current_year` | `dynasty` (players.py `HORIZONS`, lib/api.ts
  `Horizon`). `value_player`/`DynastyCurve` speak this: `dynasty` = curve-adjusted value, `current_year`
  (win-now) = raw value.
- A RANKING SET's tag is `dynasty` | `redraft` (RankingSet.horizon) — a SEPARATE vocabulary, on
  purpose (see the comment in ranking.py).
- DECISION for the new endpoints: the `horizon` query param uses the BOARD vocabulary
  `current_year|dynasty` (reuse the existing `Horizon` type — do NOT invent a third vocabulary).
  Internally MAP to the rank-set tag when filtering rank sources: `dynasty` → tag `dynasty`,
  `current_year` (win-now) → tag `redraft`. Value sources use the horizon directly (dynasty =
  curve-adjusted, current_year = raw). Put this mapping in ONE place and unit-test it.

Core concept — a uniform "ranking source". Everything that ranks players is a source of one of two kinds:
- VALUE source: has a per-player number → a `Projection` (one source per `Projection.source`, e.g.
  'espn') and later market lines. Ranked best→worst by value; the dynasty age curve applies (`dynasty`
  = age-adjusted value via `value_player`/`DynastyCurve`, `current_year` = raw value).
- RANK source: an ordered list with no underlying stat → an imported `RankingSet`
  (`RankingEntry.rank`), TAGGED dynasty|redraft. ESPN ADP is also a rank/market source (lower ADP =
  better).
Each source, for a given horizon, yields per player a RANK (1..N) and a PERCENTILE (0-100) over the
shared draftable pool.

PART A — backend: source adapters + consensus engine + API
1) A `RankingSource` adapter layer (build it in the backend/app/ranking/ stub package): one adapter
   per storage type that, given (db, horizon), returns its players as (player_id, rank, percentile) +
   a stable `id`, human `label`, and `kind`:
   - projection adapters — one per distinct `Projection.source`; value = the horizon's value (dynasty
     = curve-adjusted, current_year = raw), ranked desc → rank + percentile.
   - adp adapters — one per distinct `AdpEntry.source`; order by adp ASC (better = lower) → rank +
     percentile. (Redraft-natured; available under BOTH horizons — note that.)
   - ranking adapters — one per `RankingSet`, ELIGIBLE ONLY when its horizon tag == the MAPPED board
     horizon (dynasty→dynasty, current_year→redraft); rank = `RankingEntry.rank`.
   Percentile is computed over the shared draftable pool so sources of different lengths compare fairly.
2) Consensus engine (pure, unit-tested): given selected source ids + method ('rank'|'percentile'),
   per player average (EQUAL weight) the selected sources' ranks (or percentiles), over players present
   in >=1 selected source. A player MISSING from a source is excluded from that source's average and
   flagged (document the rule — don't treat missing as last place silently). Also compute a per-player
   DISAGREEMENT spread across the selected sources (e.g. max-min percentile, or stdev) for color-coding.
3) API:
   - `GET /sources?horizon=` (horizon in current_year|dynasty) → available sources for that horizon:
     [{id, label, kind: projection|adp|ranking, source, season?, horizon?, player_count}] (rank
     sources filtered by the MAPPED tag).
   - `GET /board/consensus?horizon=&sources=<comma ids>&method=rank|percentile&position=&limit=` → rows
     ordered by consensus, each with player identity (name, team, positions, age), per-source {rank,
     percentile} for each selected source, the consensus value, and the spread. One row per player.
     Reuse the existing pool/age/position logic from `ranked_board`.
   - Leave `GET /players/board` as-is (the single-source value/tiers view keeps working).

PART B — frontend: source selector + consensus columns on the board
- Add a SOURCE panel to the board (extend the components/board/* set + BoardControls): fetch
  `GET /sources` for the current horizon and show the sources as selectable chips/checkboxes (ESPN
  projection, ESPN ADP, imported rankings like the Dizzle dynasty set). Select 1, 2, or all.
- With >=1 selected, fetch `GET /board/consensus` and render: a column per selected source (that
  player's rank, or percentile per the toggle), a CONSENSUS column, plus player identity columns. A
  rank<->percentile TOGGLE drives both the consensus and the per-source cell display. Sort by
  consensus or by clicking any source column.
- DISCREPANCY color-coding: shade each row (or a dedicated spread cell) by how much the selected
  sources disagree — big disagreement stands out (that's the edge). Colorblind-safe scale; function
  over polish.
- The horizon toggle (current_year<->dynasty) still governs eligibility: switching refetches
  `/sources` (rank sources appear/disappear by mapped tag) and `/board/consensus` (value sources
  reshape). Reuse the existing `Horizon` type/`HORIZONS` from lib/api.ts.
- Keep the existing single-source value/tiers view reachable — don't delete what works (e.g. it's the
  view when only the ESPN projection source is active, or a small mode switch). Function-first; the
  design pass comes later.
- Loading / error / empty (no sources selected → prompt to pick one) states.

Testing (backend offline + frontend Vitest; ADD tests; keep make test + make lint + npm test + CI green):
- Backend: each adapter's rank+percentile on a fixture; the horizon->tag mapping (dynasty→dynasty,
  current_year→redraft); a ranking source is eligible only under its mapped horizon; a projection
  source orders differently for dynasty vs current_year; ADP inverts correctly; consensus average
  correct for both methods INCLUDING a player missing from one selected source; the spread metric;
  `GET /sources` lists the right sources per horizon; `GET /board/consensus` orders by consensus and
  returns per-source cells; one row per player.
- Frontend (RTL, api mocked, mirror __tests__/board.test.tsx): sources load and render selectable;
  selecting two sources shows two source columns + a consensus column; the rank<->percentile toggle
  changes the numbers; discrepancy coloring renders; sorting by a source column works; changing
  horizon refetches; empty/error states.

Constraints: reuse backend/app/valuation (value_player/DynastyCurve + the board's pool/age/position
logic in ranked_board), the models, and the board/api + frontend lib/api.ts + the components/board +
__tests__ Vitest patterns — don't fork a second value or pool path. EQUAL-WEIGHT consensus only
(weighting is a later task). SQLite-testable; no new deps; secrets/config from Settings. macOS;
Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `GET /sources?horizon=dynasty` lists the ESPN projection + ESPN ADP + the imported dynasty
   ranking(s); `GET /sources?horizon=current_year` swaps in redraft-tagged rank sources instead (the
   dynasty ranking drops out; a redraft-tagged list, if present, appears), with value sources reshaped
   to raw value.
2. `GET /board/consensus` with 2+ sources returns per-source rank+percentile, a consensus, and a
   spread, one row per player, ordered by consensus.
3. On the board you can select sources, see a column per source + consensus, flip rank<->percentile,
   sort by any column, and see disagreement color-coded; the horizon toggle changes which sources are
   eligible.
4. The existing single-source board still works. `make test`, `make lint`, `npm test`, and CI all pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed.
- The source-adapter design, the horizon->tag mapping, and the consensus + spread rules (esp. how a
  player missing from a source is handled).
- A REAL example from the synced DB: ESPN projection vs the imported Dizzle dynasty ranking — the top
  ~15 by consensus, with the biggest disagreements called out (that's the payoff we want to eyeball).
- How the horizon filters sources; the tests added, front and back.
- Anything deferred (market_line source, per-source weighting, overrides) and confirm the old board
  still works.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
