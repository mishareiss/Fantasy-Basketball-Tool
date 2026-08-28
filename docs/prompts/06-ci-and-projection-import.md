# Task 6 — CI test-safety net + the `projection` import kind

Context: Builds on task 5 (branch `import-pipeline`). Assumes task 5 is merged into `main`
(branch off `main`). Read docs/PLAN.md, docs/FEATURE_SPEC.md, docs/prompts/02..05, and the
`app/ingest/` pipeline + registry, `app/scoring/` (stats.py, engine.py ScoringEngine,
projections.py score_projection), `app/db/models/projection.py`, and `app/espn/sync.py`
(sync_projections) first. Private ESPN fantasy basketball dynasty tool, H2H points, CUSTOM
scoring. This task has TWO parts: (1) stand up automated CI so every push runs the offline test
suite + lint + migration checks — the safety net that tells us a change didn't break anything;
and (2) add the SECOND import kind — `projection` — on the task-5 registry, so projections from
any source (Hashtag / FantasyPros / Basketball Monster / a CSV) get priced under our custom
scoring beside ESPN's. The `ranking` and `market_line` kinds stay deferred (tasks 7 and 8).
Create a new branch `projection-import`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Part 1 — Continuous integration (do this first; it guards everything after):
- Add `.github/workflows/ci.yml`, triggered on push and pull_request. A backend job: checkout,
  install uv (astral-sh/setup-uv, with cache), pin Python 3.12, `uv sync`, then run `make lint`
  (ruff check + format --check) and `make test` (the offline suite, incl. the SQLite migration
  tests). It must run with NO secrets and NO network to ESPN/nba.com — the suite is already
  offline (live/nba markers deselected by default); keep it that way so CI is deterministic.
- A frontend job: in `frontend/`, `npm ci` then `npm run lint` and `npm run build`, so the Next
  app can't silently rot. (Node 20+.)
- Keep it fast (cache uv + npm). Add a CI status badge to README.md.
- OPTIONAL (nice-to-have, only if quick): a second backend job with a Postgres 16 service that
  runs `alembic upgrade head` against real Postgres — the migrations are SQLite-tested via
  batch_alter_table, and this catches Postgres-only issues. Skip if it complicates the run.
- In the report, note that Misha should enable branch protection on `main` in GitHub (require the
  CI check to pass before merge) — that's a repo setting he does, not code.

Part 2 — the `projection` import kind:
- Implement the `projection` handler in app/ingest and move it out of `PLANNED_KINDS` into the
  live registry, exactly mirroring how the `adp` handler plugs in.
- Columns: a stat-name alias table mapping common export headers to our SCORED counting-stat
  names from app/scoring/stats.py (PTS, REB/OREB/DREB, AST, STL, BLK, 3PM, TO, FGM, FGA, FTM,
  FTA, ...), tolerant of variants (3PTM/TPM/"3s" -> 3PM, TOV -> TO, TREB -> REB), plus a
  games-played column (GP). IGNORE derived/unscored columns (FG%, MPG, ranks) — only counting
  stats that a coefficient can multiply, same rule the ESPN parser follows.
- A `basis` option (per_game default | season): projection exports are usually per-game averages
  plus a GP column. Build BOTH season totals (per_game x GP when basis=per_game) and per-game
  rates, and price them through the EXISTING `score_projection` with a `ScoringEngine` loaded from
  the stored `LeagueSettings.scoring_rules` for `ctx.season`. Do NOT write a second scoring path —
  the imported projection must be scored by the identical code that scores ESPN's, or the numbers
  aren't comparable. Handle missing/zero GP with the same per_game_basis fallback the ESPN path
  uses.
- Upsert `Projection(player_id, source=ctx.source, kind='projected_season', season=ctx.season,
  raw_stats, per_game_stats, projected_games, fantasy_points_total, fantasy_points_per_game,
  per_game_basis)` on the existing (player_id, source, kind, season) key — idempotent, 0 dupes on
  re-import. Auto-accept policy same as `adp` (the values are numeric; name-matching is the only
  risk, and the pipeline already surfaces review/unmatched rows).
- No model or migration expected (Projection already exists). The board already takes a `source`
  param, so an imported projection is rankable immediately.

Migrations & wiring:
- None expected for the projection kind. If Part 1's optional Postgres job needs anything, keep it
  in CI config only.

Testing (this is the point of the task — be thorough):
- A synthetic projection CSV fixture: per-game stat columns + GP, with exact, needs-review
  (accent/fuzzy), and unmatched names, mirroring the adp fixture.
- Tests: stat-column alias mapping (header variants + ignored derived columns); per_game x GP ->
  season totals; the scored fantasy points equal what `score_projection` returns for the same
  line under a known scoring fixture (proves single-source-of-truth scoring); idempotent re-import
  (0 dupes); `GET /players/board?source=<imported source>` ranks by the imported projection and
  ESPN's rows are untouched (coexist by source); missing-GP fallback.
- Validate the CI workflow: run the exact commands CI runs, locally, and confirm green
  (`make lint`, `make test`, and the frontend `npm run lint && npm run build`). You can't push, so
  confirm the YAML is valid and the commands succeed as written.

Constraints:
- Reuse app/ingest (pipeline + registry), app/matching, and app/scoring — do NOT reimplement
  parsing, matching, or scoring. Stdlib `csv` only; no new backend deps. SQLite-testable (generic
  sa types, hand-written upserts). Secrets/IDs from Settings. CI uses no secrets. macOS locally;
  Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `.github/workflows/ci.yml` runs backend `make lint` + `make test` and the frontend
   lint+build on push/PR, green, with no secrets. (Confirm locally that each command CI runs
   passes.)
2. `make import KIND=projection SOURCE=... SEASON=... FILE=... [--basis per_game]` dry-run previews
   matched/review/unmatched; `--commit` writes Projections priced under our scoring; a re-run
   creates 0 duplicates.
3. `GET /players/board?source=<that source>` ranks by the imported projection; ESPN's projections
   are untouched (both coexist keyed by source).
4. `make test` and `make lint` pass.

Report back with:
- Branch + file tree of changes; confirm STAGED, NOT committed.
- The CI workflow, and confirmation each command it runs passes locally (since you can't push).
  Note the branch-protection step for Misha.
- A dry-run preview of the sample projection CSV.
- A few players' imported projected fantasy points under our scoring shown next to ESPN's, as a
  sanity check that the shared scoring path produces comparable numbers.
- The stat-name alias table you used and any header variants handled.
- Anything you decided/deviated on, and confirmation ranking (task 7) + market_line (task 8) are
  still the remaining registry stubs.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
