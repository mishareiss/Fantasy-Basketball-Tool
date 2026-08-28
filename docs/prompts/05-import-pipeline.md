# Task 5 — Generic CSV/paste import pipeline (first kind: ADP) + AdpEntry season key

Context: Builds on task 4 (branch `player-age-matching`). Assumes task 4 is merged into `main`
(or branch off `player-age-matching`). Read docs/PLAN.md, docs/FEATURE_SPEC.md, docs/prompts/
02..04, and especially the `app/matching/` package (build_matcher, PlayerMatcher.match,
record_match, MatchResult), the `app/ingest/` stub, `app/espn/sync.py` (sync_adp),
`app/api/players.py` (the board + `/players/unresolved` + `POST /players/{id}/aliases`), and
`app/db/models/adp.py` first. Private ESPN fantasy basketball dynasty tool, H2H points, CUSTOM
scoring. Almost all external ADP/projection/ranking data has no clean free API, so the
architecture is import-first: a generic CSV/paste importer that resolves foreign player names to
our canonical, ESPN-keyed Players (via `app/matching`, already built in task 4) and upserts
them. This task builds that pipeline and wires the FIRST kind end-to-end — ADP — and fixes
`AdpEntry` so dynasty ADP history survives across seasons. The projection / ranking /
market_line kinds are DELIBERATELY DEFERRED to task 6 (each needs a new model and/or
stat-mapping / de-vig); build the core generic so they slot in as registry handlers. Create a
new branch `import-pipeline`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope:

1) AdpEntry season key + rewiring (do this first — it's a schema change the rest builds on):
- Add `season` (int, NOT NULL) to `AdpEntry`; change the unique key from (player_id, source) to
  (player_id, source, season). ONE Alembic migration that BACKFILLS existing rows before
  enforcing NOT NULL — every AdpEntry today is ESPN redraft ADP for the currently-synced season,
  so backfill with ESPN_SEASON. Verify from-scratch apply + downgrade.
- Update `sync_adp` (app/espn/sync.py) to stamp `season = client.season` and upsert on the new
  3-part key.
- Update `GET /players/board`: the AdpEntry outer-join must constrain season, or a player with
  ADP in more than one season fans out into duplicate board rows. Default to the newest AdpEntry
  season for the chosen ADP source, and add an optional `adp_source` query param (default
  'espn') so you can rank by one source's projection but display another source's ADP. Still
  exactly one row per player.
- Update the task-3 tests that assume single-season ADP.

2) Generic import core (app/ingest/):
- A CSV/paste parser (stdlib `csv`): accept either an uploaded file or a pasted string.
  Forgiving column detection — locate the player-name column, optional team and position
  columns, and the kind's value column(s) via a small header-alias table (name/player, tm/team,
  pos, adp/rank/...), with an optional explicit column-map override. Trim and skip blank rows.
- A matching pass reusing `app/matching`: `build_matcher(db, source=...)`, then
  `match(name, team=..., positions=...)` per row. Partition into matched (result.matched and not
  needs_review), review (needs_review — fuzzy/ambiguous, keep candidates), and unmatched.
- A per-kind handler registry keyed by kind: a handler declares its value column(s), how to
  build the stored row from a matched (player, value), and its auto-accept policy. Implement the
  `adp` handler -> AdpEntry (source, season, adp / auction_value / percent_owned). Leave
  projection / ranking / market_line as documented registry stubs.
- TWO-PHASE by design: a DRY-RUN that parses + matches and returns a full summary (counts + per-
  row results with candidates) WITHOUT writing, and a COMMIT that persists. Commit does:
  `record_match(db, source, result)` for accepted rows (writes the PlayerAlias, so re-imports are
  instant), upserts the kind's rows, and skips/queues review + unmatched per policy. Idempotent:
  re-importing the same file writes 0 duplicate aliases and 0 duplicate rows.
- Per-kind auto-accept policy: `adp` auto-accepts at or above the matcher threshold; a stricter
  kind can hold fuzzy rows for confirmation (MatchResult.needs_review is already there). Sensible
  default, overridable per call.

3) Endpoints + CLI (mirror sync's dual path):
- `scripts/import_data.py` (`make import KIND=adp SOURCE=... SEASON=... FILE=...`) that prints the
  dry-run preview, with a `--commit` flag to write.
- `POST /import/{kind}` taking the CSV/paste body + source + season + optional column-map +
  `dry_run` (default true) -> preview or commit result. Mount the router in app/api/__init__.
- The importer response carries its own review + unmatched rows (foreign name + candidates) as
  the hand-resolve worklist; those are fixed with the existing
  `POST /players/{espn_player_id}/aliases`, after which a re-import resolves them. Also extend
  `GET /players/unresolved` to accept `need=adp` (our board players with no AdpEntry for a
  source) — the endpoint already anticipates new needs.

Migrations & wiring:
- One migration (AdpEntry season). No new model expected for ADP (AdpEntry already exists); if
  you add one, import it in app/db/models/__init__.py.

Testing (offline: fixtures + in-memory SQLite, no network):
- A small SYNTHETIC ADP CSV fixture with exact, needs-review (accent/fuzzy), and unmatched names.
- Parser tests (aliased headers, quoted fields, messy whitespace, explicit column-map). Match-
  partition tests. Dry-run writes nothing; commit writes; re-commit is idempotent (0 dupes). Two
  seasons of ADP for one player coexist and the board shows the newest (and honors `adp_source`).
  Migration backfill is correct and the board returns one row per player after the change.
  `record_match` writes an alias that makes a second import resolve a previously-fuzzy name as
  'alias'.
- `make test` + `make lint` green.

Constraints:
- Reuse `app/matching` — do NOT reimplement matching. Stdlib `csv` only (no new deps).
- SQLite-testable: generic sa types, hand-written upserts (no PG-only ON CONFLICT), matching in
  Python. Don't store an ADP row without a season. Secrets/IDs from Settings. macOS; Postgres
  host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `make db-up && make migrate` applies (from-scratch + downgrade) and backfills existing ADP with
   a season.
2. `make import KIND=adp SOURCE=... SEASON=... FILE=...` dry-run previews matched/review/unmatched;
   `--commit` writes; a re-run creates 0 duplicates.
3. Two seasons of ADP coexist; the board shows one row per player with the right season, and
   `adp_source` selects which source's ADP is displayed.
4. A fuzzy-matched name, once hand-aliased via POST /players/{id}/aliases, resolves as 'alias' on
   re-import.
5. `make test` and `make lint` pass.

Report back with:
- Branch + file tree of changes; confirm STAGED, NOT committed.
- A dry-run preview of the sample CSV (matched / review / unmatched counts + a few of each).
- Proof two ADP seasons coexist and the board is unaffected (one row per player, correct season).
- The per-kind handler registry shape, and exactly what a task-6 projection / ranking /
  market_line handler will need to add (new models, stat-mapping, de-vig) so we can scope it.
- Anything you decided/deviated on.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
