# Task 3 — Parse ESPN projections + ADP already in the payload → first custom-scoring values + ADP data

Context: Builds on task 2 (branch `espn-foundation`). Assumes `espn-foundation` has been merged into
`main` (or branch off the current state). Read docs/PLAN.md, docs/FEATURE_SPEC.md, docs/prompts/
02-espn-foundation.md, and the existing backend/app/espn + app/scoring code first. Private ESPN
fantasy basketball dynasty tool, H2H points, CUSTOM scoring (already pulled + stored as ScoringRules).
The kona_player_info payload we already fetch in sync contains, per player, BOTH projected stats and
ESPN ADP — we currently discard them. This task parses and persists them. Create a new branch
`espn-projections` off `main` and work there.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Background on the data (already in the kona_player_info entries the sync fetches):
- `stats[]`: each entry is a split identified by statSourceId (0 = actual, 1 = projected) and
  statSplitTypeId (0 = full season). We want the PROJECTED FULL-SEASON split for the configured
  ESPN_SEASON — season totals in `stats` and per-game in `averageStats`.
- `ownership`: `averageDraftPosition`, `auctionValueAverage`, `percentOwned` — this is ESPN
  REDRAFT ADP (not dynasty, not tuned to our scoring). Store as-is; we'll dynasty-adjust later.

Scope:

1) ESPN projection parsing (app/espn/statsplits.py, next to players.py):
- Add a parser that, given a kona_player_info player entry, extracts the projected full-season split:
  a dict of stat_name -> value (season totals) using the existing statId->name map in
  app/scoring/stats.py, plus projected games played (GP) and the per-game averages.
- Be defensive: players with no projection (e.g. rookies/inactive) → no projection row, not an error.

2) Projection model + custom-scoring value (app/db/models + app/scoring):
- New `Projection` model: player_id FK, source (str, 'espn'), kind (str, 'projected_season'),
  season (int), raw_stats (JSON: stat_name->value season totals), projected_games (nullable float),
  fantasy_points_total (float), fantasy_points_per_game (float), as_of (timestamp).
- Compute fantasy_points_total via the existing score_stat_line on the season-total stats, and
  fantasy_points_per_game = total / projected_games (fall back to total/ (per-game stats scored)
  if GP missing — document the choice). Unique on (player_id, source, kind, season) so re-sync
  upserts, not duplicates.

3) ESPN ADP model (app/db/models):
- New `AdpEntry` model (per-source so more sources can be added later): player_id FK, source
  ('espn'), adp (nullable float), auction_value (nullable float), percent_owned (nullable float),
  as_of. Unique on (player_id, source). Upsert on re-sync.

4) Wire into sync:
- Extend the existing sync (app/espn/sync.py + POST /sync/league) to also parse and upsert
  Projections and AdpEntries during the same player pass. Keep it idempotent (re-run: 0 dup rows).
- Update the /sync/league summary to include projection count and adp count.

5) Read API to eyeball results (app/api):
- GET /players/board — returns players ordered by projected fantasy_points_per_game (desc) under
  our custom scoring, each with: name, nba_team, positions, fantasy_points_per_game,
  fantasy_points_total, projected_games, espn_adp. Support ?limit= (default 50) and optional
  ?position= filter. This is our first data-driven "board".

Migrations & wiring:
- Add Projection and AdpEntry to app/db/models/__init__.py (Alembic autogenerate needs them).
- Generate ONE Alembic migration for the new tables; eyeball it; verify from-scratch apply.

Testing & fixtures:
- Extend backend/scripts/record_fixtures.py so the kept players (currently 60) retain their `stats`
  and `ownership` blocks, so projection + ADP parsing has fixture coverage. Re-record the sanitized
  fixtures (keep them sanitized — player + projection + adp data only, no account identifiers).
- Fixture-based tests (no live creds, in-memory SQLite): projection parser extracts the projected
  split correctly; fantasy_points_total matches score_stat_line on the stats; a player with no
  projection yields no row; ADP parsed; /players/board returns correctly ordered rows.
- Keep the live-marked tests working (add a live test asserting real projections + ADP come back).

Constraints:
- No new third-party deps (data is already in the fetched payload). httpx/espn-api already present.
- Generic sa.JSON (not JSONB), hand-written upserts (keep SQLite-testable), per the task-2 pattern.
- Don't hardcode secrets/IDs. macOS; Postgres on host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `make db-up && make migrate` applies the new migration cleanly (and from-scratch).
2. With real .env creds, `make sync` populates Projections + AdpEntries; second run creates 0 dups.
3. `make test` (fixtures, no creds/DB) and `make lint` pass; `make test-live` passes.
4. GET /players/board returns a sane ranked list with projected value + ADP.

Report back with:
- Branch name + file tree of changes; confirm STAGED, NOT committed.
- Counts: players, projections, adp entries synced.
- The TOP 20 players by projected fantasy_points_per_game under our custom scoring, each shown next
  to their ESPN ADP — so we can see where our big-man scoring diverges from ESPN's redraft ADP.
- How you computed per-game points (GP source / fallback), and how many players had no projection.
- Anything you decided/deviated on, and follow-ups for task 4 (age source via nba_api + PlayerAlias
  fuzzy matcher + generic CSV/paste import pipeline).
