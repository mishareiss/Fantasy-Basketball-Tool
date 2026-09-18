# Task 14 — market entry/edit UI + per-kind import placeholders

Context: Task 13 shipped the market_line SOURCE (MarketLine model, de-vig -> fair value ->
`Projection(source='market', kind='projected_season')`, import kind `market_line`) but left entry to
the CSV/paste importer only, with NO way to see, edit one odds, or DELETE a stored line. This task adds
a tailored market board-side UI for that, plus a small importer-UX fix. Builds on task 13.

PRECONDITION / branch: task 13 must be merged to `main` first (main will contain app/db/models/
market_line.py, app/ingest/market_line.py, app/ranking/market.py). `git checkout main`, confirm those
exist, then `git checkout -b market-ui`. (If task 13 is NOT yet merged, branch off `market-line` and
say so.)

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Read FIRST:
- backend/app/ingest/market_line.py — `upsert_market_line`, `derive_market_projections(db, effective,
  context, engine)`, `price_lines`, `resolve_stat`, `UnknownStatError`, `MARKET_PROJECTION_KIND`. REUSE
  these primitives; do NOT reimplement pricing or derivation.
- backend/app/db/models/market_line.py — `MarketLine` (key (source, season, player_id, stat_id); fields
  line: float, over_odds/under_odds: int|None, `stat_name` property).
- backend/app/api/imports.py (POST /import/{kind}) and backend/app/api/players.py (the alias mutation
  endpoint `POST /players/{espn_player_id}/aliases`, and how player identity — name/team/positions/age —
  is shaped for responses). app/matching (the name matcher + candidates the importer uses).
- frontend/components/import/* — `ImportPage`, `KindPicker`, `ImportConfig`, `TableInput` (its textarea
  `placeholder`), `ImportPreview`, `ImportStates`; frontend/lib/importing.ts (`KIND_ADP/PROJECTION/
  RANKING`, `KIND_HINT`, `EMPTY_FORM`, option builders — note there is NO market_line entry yet);
  frontend/lib/api.ts (the `api` client, `ImportKindInfo`, `BoardRow`/source types); frontend/
  components/board/SourcePanel.tsx (how sources are listed). Mirror these patterns; reuse `request()`/
  `ApiError`.

THE DERIVATION LANDMINE (most important). `derive_market_projections` is UPSERT-ONLY: it creates or
updates a player's market `Projection` but NEVER deletes it. So deleting a player's LAST market line
must ALSO remove that player's market `Projection` row, or he keeps a phantom market projection and
stays in `projection:market` with no lines under him. Fix this in the derivation path (e.g. when a
player's effective lines are empty, delete his market Projection for (source, kind, season)) and TEST
it: delete the last line -> the player leaves `GET /sources` player_count and `GET /board/consensus`
for that source.

PART A — backend: market CRUD (small; reuse task-13 primitives)
1. `GET /market/lines?source=&season=` -> the stored lines for that (source, season), grouped by player:
   player identity (espn_player_id, name, team, positions, age) + his lines [{id, stat_id, stat (name),
   line, over_odds, under_odds, as_of}] + his derived market fantasy_points_per_game. Default source
   'market', season resolved as the importer does. Empty list is a clean 200.
2. `PUT /market/lines` -> upsert ONE line: body {source, season, player_id, stat (name or id), line,
   over_odds?, under_odds?}. Resolve stat via `resolve_stat` (unknown -> 422, not 500). Reuse the
   task-13 upsert + `derive_market_projections` so the player is re-priced. Return the updated line +
   the player's new derived fppg.
3. `DELETE /market/lines/{id}` -> delete that line, then re-derive that player (PART-landmine cleanup
   included). 404 on a missing id. (Optional convenience: `DELETE /market/lines?source=&season=&
   player_id=` to clear a player's whole set.)
- GUARD: `GET /players/board` and the ESPN/consensus paths stay byte-identical; adding these endpoints
  changes no existing route. Register the new router in app/api/__init__.py.

PART B — frontend: a market page to see/add/edit/delete lines
- A dedicated route `/market` (mirror app/import/page.tsx -> a `MarketPage` under components/market/*).
  DEFAULT CHOICE, flag it: a separate page reads cleaner than a mode on /import because market lines are
  a standing editable SET, not a one-shot paste. Reachable from the same nav as the board/importer.
- Pick source (default 'market') + season. Show the current lines grouped by player (from GET
  /market/lines): each player with his stat lines, each line's odds, and his derived market fppg + a
  "partial (N stats)" note.
- ADD a line: choose a player + a stat (dropdown of the league's scored stats) + line + optional
  over/under odds -> PUT /market/lines -> the list refreshes with the re-derived value. Resolve the
  player through the EXISTING importer name-match/candidate path (and `POST /players/{id}/aliases` to
  fix a miss) — do NOT build a second matcher.
- EDIT a line in place (change line or either odds) -> PUT -> refresh. DELETE a line (with a confirm)
  -> DELETE -> refresh; when it was his last line he disappears from the list and from the market
  source. Loading / error / empty (no lines yet -> prompt to add one) states.

PART C — per-kind import input placeholder (the small UX fix)
- In `TableInput`, make the textarea `placeholder` show an EXAMPLE ROW in the SELECTED kind's format,
  so the expected columns are obvious before pasting. Drive it from a per-kind example map in
  lib/importing.ts (alongside `KIND_HINT`). Examples (match each kind's real columns/aliases):
  - adp: `Nikola Jokic, 1.2`
  - projection: `Nikola Jokic, 27.4, 12.7, 10.1, 1.3, 0.7, 3.1, ...` (its stat columns)
  - ranking: `1, Victor Wembanyama`
  - market_line: `LeBron James, PTS, 24.5, -115, -105`
- Add `KIND_MARKET_LINE = "market_line"`, its `KIND_HINT` line, and wire market_line through the
  importer form the same way the others are (it takes no options). This makes the bulk paste path for
  market lines first-class too, next to the tailored /market page.

Testing (backend offline + frontend Vitest; ADD tests; keep make test + make lint + npm test + CI green):
- Backend: GET groups a multi-stat player into one entry with his derived fppg; PUT upserts + re-derives
  (new value reflects the odds); PUT unknown stat -> 422; DELETE removes a line and re-derives; DELETE
  the LAST line removes the market Projection and drops the player from `GET /sources`/consensus;
  `GET /players/board` unchanged.
- Frontend (RTL, api mocked, mirror components/import + __tests__ patterns): the market page lists
  players+lines; adding a line calls PUT and shows the new value; editing odds re-requests; deleting a
  line removes it (and an emptied player disappears); the per-kind placeholder text CHANGES when the
  kind changes in the importer; empty/error states.

Constraints: reuse app/ingest/market_line (price_lines/derive_market_projections/resolve_stat), the
matcher/candidate path, and frontend lib/api.ts + components/import + __tests__ Vitest patterns — do NOT
fork a second pricing, derivation, or name-match path. No new deps. SQLite-testable. secrets/config from
Settings. macOS; Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. On `/market` I can pick source/season, see stored lines grouped by player with derived values, ADD a
   line (player+stat+line+optional odds), EDIT a line/odds, and DELETE a line.
2. Deleting a player's last line removes him from the market source (no phantom projection); the derived
   value updates on every add/edit/delete.
3. The importer's input placeholder shows a correct example for each kind (adp/projection/ranking/
   market_line); market_line is a first-class kind in the importer.
4. `GET /players/board` and the consensus board are unchanged. `make test`, `make lint`, `npm test`,
   and CI all pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed; which branch you cut from.
- The new endpoints + how delete re-derives and cleans up the phantom projection.
- The /market page (a screenshot or the flow) and the per-kind placeholders shipped.
- Tests added; anything deferred (per-source weighting, overrides).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
