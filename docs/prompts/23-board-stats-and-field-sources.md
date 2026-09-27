# Task 23 — My Board: last-year & market columns + player stats popup; draft-setup field sources

Context: Three additions. (A) Ingest last season's ACTUAL production and expose it; add two columns
to My Board (last-year fantasy PPG under our scoring, and the market-line projection) plus a
player-detail popup with a full last-season stat line + market projections. (B) A draft-setup
control to pick which ranking sources the simulated field drafts from. Backend part + frontend part,
one task. Branch `task-23-board-stats` off `main` (at c43438c). STAGE, DO NOT COMMIT. No AI trailers.

Read first:
- `backend/app/espn/statsplits.py` — parses the ESPN `kona_player_info` split. It already defines
  `PROJECTED_SOURCE_ID=1`, `ACTUAL_SOURCE_ID=0`, `FULL_SEASON_SPLIT_ID=0`, and
  `select_projected_split`/`parse_projection_entry`/`parse_projections` (statSourceId=1). The SAME
  payload entries carry the ACTUAL split (statSourceId=0). You mirror the projected parsers for actuals.
- `backend/app/scoring/projections.py` — `score_projection(engine, season_totals, *, per_game_stats,
  projected_games)` is SOURCE-AGNOSTIC; reuse it verbatim to score actuals.
- `backend/app/espn/sync.py` — `SEASON_PROJECTION_KIND="projected_season"`; `sync_projections(db,
  splits, engine, summary, kind=...)` ALREADY takes a `kind` (line ~217/224) and the full sync calls
  it at ~394. You add an actuals call with a new kind. `sync_scoring_settings`/`load_scoring_engine`
  give the engine.
- `backend/app/db/models/projection.py` — the `Projection` table: unique (player, source, kind,
  season); `raw_stats`/`per_game_stats` JSON, `fantasy_points_total`/`_per_game`, `projected_games`,
  `source_fantasy_points_total`. Actuals reuse THIS table (a new `kind`), NO new model/migration.
- `backend/app/db/models/market_line.py` + `app/api/market.py` (MarketPlayer shape) — the market
  lines and the derived market projection (source='market', kind='projected_season').
- `backend/app/api/master.py` — `MasterPlayerRow` + `_row`/`_response`; you add two fields + lookups.
- `backend/app/api/players.py` — where a `GET /players/{id}/detail` endpoint fits; `_best_per_game`
  and the board query show the query patterns.
- `backend/app/api/draft.py` — `DraftCreate` (already has `field_horizon`/`field_source_ids`),
  `PUT /draft/config` (`DraftConfigWrite`), `field_ranks`/`UnknownSources` in `app/draft/field.py`,
  and how `_load` resolves sources — for validating a chosen source set.
- `backend/app/scoring/stats.py` — `STAT_ID_TO_NAME` / `STAT_LABELS` for stat names (per-game dicts
  are keyed by stat NAME already: 'PTS','REB','AST','MIN', ...).
- Frontend: `lib/api.ts` (`MasterPlayerRow`, `api.masterBoard`, `api.sources`, `DraftCreateBody`,
  `DraftConfigBody`, `SourceInfo`), `components/masterboard/MasterBoardPage.tsx` + `MasterRow.tsx`
  (row layout + the existing per-row controls — drag handle, tag, note, exclude, move-to),
  `components/draft/DraftSetup.tsx` (+ the seats/config panel that calls `updateDraftConfig`),
  `lib/masterboard.ts`, `components/board/BoardControls.tsx` (`Segmented`/`Segment`).

Keep all green: `make test`+`make lint` (backend), `npm test`+`npm run build`+`npm run lint` (frontend).
CI rule: small Vitest fixtures / explicit timeouts.

---

## Part A — backend

### A1. Ingest last-season actuals (no migration; reuse `Projection`)
- In `statsplits.py`, add `select_actual_split` / `parse_actual_entry` / `parse_actuals`, mirroring the
  projected ones but with `ACTUAL_SOURCE_ID`. Actual full-season split = `(statSourceId=0,
  statSplitTypeId=0)`; pick the one whose `seasonId` is the season BEFORE the requested season if
  present, else the newest actual split ESPN has (mirror `select_projected_split`'s fallback). Reuse
  the `ProjectionSplit` dataclass; its `projected_games` holds actual games played for an actual split
  (fine — same column). Filter to `COUNTING_STAT_IDS` exactly as the projected parser does.
- In `sync.py`, add `ACTUAL_SEASON_KIND = "actual_season"` and, in the full sync (beside the
  `sync_projections(..., parse_projections(...))` call at ~394), call
  `sync_projections(db, parse_actuals(entries, client.season), engine, summary, kind=ACTUAL_SEASON_KIND)`.
  The engine is the CURRENT league's scoring, which is the point — last year's production valued under
  OUR scoring. Stored as `Projection(source='espn', kind='actual_season', season=<the actual seasonId>)`,
  which cannot collide with the projected row and (because every board/consensus query filters
  `kind==SEASON_PROJECTION_KIND`) can never leak onto the board or into the consensus. Confirm that with
  a test.
- Tests: `parse_actuals` pulls the actual split and drops non-counting stats; the sync stores an
  `actual_season` row with a sane `fantasy_points_per_game`; the board/consensus are UNCHANGED by its
  presence (add a row and assert `/players/board` + `/board/consensus` don't move).

### A2. Two board fields
- Add to `MasterPlayerRow`: `last_year_fantasy_ppg: float | None` (the `actual_season` Projection's
  `fantasy_points_per_game` for that player, newest such season) and `market_fantasy_ppg: float | None`
  (the `source='market', kind='projected_season'` Projection's `fantasy_points_per_game`). Both default
  None. In `_response`, build two `{player_id: value}` lookups with one query each over the board's
  player ids and pass them into `_row`. A player with no actual / no market line → None (the UI shows '-').
- Tests: the fields populate when the rows exist and are None when they don't; existing master tests
  still pass (additive, defaulted).

### A3. Player detail endpoint (for the popup)
- `GET /players/{espn_player_id}/detail` → `PlayerDetailResponse`:
  - player identity (id, name, nba_team, positions, age),
  - `last_season`: `{ season, games, fantasy_ppg, fantasy_total, per_game: {statName: value} }` from the
    `actual_season` Projection (its `per_game_stats`), or `null` if none,
  - `market`: `{ fantasy_ppg, fantasy_total, games, per_game: {statName: value} }` from the market
    Projection, or `null`,
  - `market_lines`: the raw `MarketLine` rows `[{ stat, line, over_odds, under_odds }]` (may be empty).
  404 for an unknown player. Every stat value is present-or-absent — never fabricate; the UI renders '-'.
- Tests: a player with an actual line + market lines returns them; a player with neither returns nulls/
  empty; unknown id → 404.

### A4. Field sources on `PUT /draft/config` (pre-draft)
- Extend `DraftConfigWrite` + `PUT /draft/config` to also accept `field_horizon` and `field_source_ids`,
  applied ONLY when `picks_made == 0` (same rule/422 as `my_slot`; a started draft's field is part of
  what its picks mean). Validate the ids through the same catalog/`UnknownSources` path `POST /draft`
  uses → 400 on an unknown id. (Create already accepts them; this lets them be changed before the first
  pick, next to the seat change.) Tests: set field sources on an empty draft; 422 after a pick; 400 on a
  bad id.

## Part B — frontend

### B1. `api.ts`
- `MasterPlayerRow` += `last_year_fantasy_ppg: number | null`, `market_fantasy_ppg: number | null`.
- Add `PlayerDetailResponse` (mirror A3) + `api.playerDetail(espnPlayerId)`.
- `DraftConfigBody` += `field_horizon?`, `field_source_ids?`. (`DraftCreateBody` already has them, and
  `api.sources(horizon)` already exists.)

### B2. Two columns + player popup on My Board
- Add two columns to the board table (`MasterRow` + the header in `MasterBoardPage`): **Last yr FP**
  (`last_year_fantasy_ppg`) and **Mkt proj** (`market_fantasy_ppg`), each one decimal, showing **'-'**
  when null. Keep the row readable at the page's width; these are compact numeric columns.
- Clicking the player's NAME opens a **detail popup/modal** that fetches `api.playerDetail(id)` and shows:
  last-season per-game box score (MIN, PTS, REB, AST, STL, BLK, TOV/TO, 3PM, FGM/FGA, FTM/FTA, GP — plus
  FG%/FT% derived from makes/attempts when both exist), last-season fantasy PPG and total, the market
  projection (fantasy PPG + its per-game stats), and the raw market lines. Any missing value renders **'-'**.
  The popup is dismissable (Esc / backdrop / close button) and is accessible (focus trap or at least
  `role="dialog"` + labelled). IMPORTANT: opening the popup must NOT fire the row's existing controls —
  the drag handle, tag/note/exclude/move buttons keep their own handlers (stop propagation), and only the
  name (or a dedicated info affordance) opens the popup. A loading + error state inside the popup (a failed
  detail fetch is a small message, not a page error).
- Tests (mocked api, small fixtures): the columns render values and '-' for nulls; clicking a name calls
  `playerDetail` and shows the stat line incl. '-' for missing stats; clicking a row control (e.g. tag)
  does NOT open the popup; popup closes on Esc/close.

### B3. Draft-setup field-source selection
- In `DraftSetup` (and the pre-draft seats/config panel), fetch `api.sources(horizon)` and offer:
  a **horizon** toggle (`Segmented`, dynasty/current_year) and a **multi-select of source ids** (checkbox
  list / chips), each source shown by its `label` + `kind` + `player_count`. Default: ALL sources selected
  (which means "omit `field_source_ids`" — the backend reads absent as all). Passing a subset sets
  `field_source_ids` on `createDraft`; before the draft starts the same control edits them via
  `updateDraftConfig`. Show what the field will draft from ("the room drafts off the consensus of these
  sources"). Re-fetch sources when the horizon changes.
- Tests: the source list renders from a mocked `api.sources`; selecting a subset passes those
  `field_source_ids` to `createDraft`; all-selected omits the field (or passes all) as designed; a
  pre-draft change calls `updateDraftConfig`.

## Constraints
- No migration (actuals reuse `Projection` with a new `kind`). Reuse `score_projection`, the existing
  `sync_projections(kind=...)`, `STAT_ID_TO_NAME`/`STAT_LABELS`, `field_ranks`/`UnknownSources`,
  `Segmented`/`Segment`, the request-key guard, `ApiError`. `api.ts` mirrors the backend exactly.
- The master board must be UNCHANGED where these are absent: new row fields default null, existing tests
  pass, and an `actual_season` row never appears on `/players/board` or `/board/consensus`.
- Never fabricate a stat — missing is null/'-', not 0 or a guess.

## Acceptance criteria
1. Backend `uv run pytest -q` green (old + new); `make lint` clean; `alembic heads` single (NO new migration).
2. `npm test` + `npm run build` + `npm run lint` green.
3. `/players/board`, `/board/consensus` and the master board are unchanged by the actuals ingestion
   (guarded by a test); the two new master fields populate or are null.
4. My Board shows Last-yr-FP and Mkt-proj columns ('-' when absent); clicking a name opens a stats popup
   with the last-season line + market projection + lines, '-' for any missing value; row controls still
   work without opening it.
5. Draft setup lets me choose the field's horizon + a subset of ranking sources; the choice reaches
   `POST /draft` (and `PUT /draft/config` before the draft starts).

## Report back with
- File tree (backend: statsplits/sync/master/players/draft + tests; frontend: api, masterboard, DraftSetup
  + tests). Confirm STAGED not committed on `task-23-board-stats`; single alembic head; four checks green.
- A dry-run: `GET /players/{id}/detail` for a player WITH a last-season line + market lines, and one with
  neither (nulls). A My-Board row's two new values, and one showing '-'.
- Decisions/deviations (e.g. exactly which stats the popup shows, how FG%/FT% are derived).

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
