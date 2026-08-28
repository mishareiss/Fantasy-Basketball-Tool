# Task 7 — the `ranking` import kind: RankingSet / RankingEntry models + wholesale-replace import

Context: Builds on task 6 (branch `projection-import`, merged to `main`). Branch off `main`. Read
docs/PLAN.md, docs/FEATURE_SPEC.md (esp. sections 5 and 10), docs/prompts/05..06, and the
`app/ingest/` package — `registry.py` (ImportKind, ResolvedRow, UpsertContext with its `options`
mapping, UpsertCounts, accept_matcher_threshold, register), `adp.py` and `projection.py` (the two
built handlers, your templates), and `pipeline.py`. Private ESPN fantasy basketball dynasty tool,
H2H points, CUSTOM scoring. This adds the THIRD import kind — `ranking` — an ordered list of
players (consensus board, an expert's Top-200, or a list we like) with an optional tier and
score. Unlike adp/projection it needs its own storage, because a ranking is a *set* with order,
not a per-player number. `market_line` stays deferred (task 8). Create a new branch
`ranking-import`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope:

1) New models (app/db/models/) + one migration:
- `RankingSet` — one named, ordered list from one source for one season: id, source (str),
  name (str, the human label e.g. "Hashtag Dynasty Top 200"), season (int), as_of (timestamp).
  Unique on (source, name, season). Register in app/db/models/__init__.py.
- `RankingEntry` — id, ranking_set_id (FK -> ranking_set, ondelete CASCADE, indexed),
  player_id (FK -> player.espn_player_id, CASCADE, indexed), rank (int), tier (str, nullable),
  value (float, nullable, a score the source printed). Unique on (ranking_set_id, player_id).
  Relationship RankingSet.entries (cascade all, delete-orphan), ordered by rank.
- One Alembic migration (create_table for both). Verify from-scratch apply + downgrade.

2) The `ranking` handler (app/ingest/ranking.py), mirroring adp.py/projection.py:
- Columns (ValueColumn): `rank` (aliases: rank, rk, "#", overall, ovr, pos — NOT required),
  `tier` (aliases: tier, tr, grp, group), `value` (aliases: value, score, rating, proj). Name /
  team / positions come from the parser as usual, for matching.
- Set identity comes from `context.options`: read `name` (the set label), defaulting to
  `context.source` when absent — so `NAME="Hashtag Dynasty"` names the set and re-importing the
  same (source, name, season) targets the same set. An unknown option should be rejected the way
  `projection` rejects a bad `basis` (ImportParseError), not silently ignored.
- Rank resolution: use the parsed `rank` value when present; otherwise fall back to the row's
  ORIGINAL position in the file (use the ParsedRow's source line/index, not the index among
  accepted rows), so rows the pipeline holds for review leave a gap rather than renumbering
  everyone below them. Store rank as int.
- **Wholesale replace** (this is the point of the kind): the upsert find-or-creates the
  RankingSet for (source, name, season), then REPLACES its entries — a ranking's composition
  changes between versions (players drop off, order shifts), so upserting row-by-row would leave
  stale entries behind. Delete the set's existing RankingEntry rows and insert the new ones.
  Report counts honestly (entries written; and that the set was replaced — e.g. old N -> new M);
  keep it dry-run-safe (a dry run computes and writes nothing, like the other handlers). Re-import
  yields exactly the imported set, no duplicate entries.
- accept policy: `accept_matcher_threshold`, same as adp — a ranking is read by eye and a wrong
  row costs a double-take, not a bad bet; fuzzy names the matcher won't place still come back as
  the review list. (accept_only_certain stays reserved for market_line.)
- Move `ranking` out of `PLANNED_KINDS` and `register(...)` it (import the module in
  app/ingest/__init__.py next to adp/projection). PLANNED_KINDS should end as just
  ['market_line'].

3) Read API (app/api/, e.g. rankings.py mounted in app/api/__init__.py):
- `GET /rankings` — list stored sets: id, source, name, season, entry_count, as_of.
- `GET /rankings/{id}` — the set's entries ordered by rank, each joined to the player
  (name, nba_team, positions, rank, tier, value). Optional `?limit=`.

4) CLI + endpoint wiring:
- `make import KIND=ranking SOURCE=... SEASON=... NAME="..." FILE=...` — add `NAME=` passthrough
  into the options mapping alongside the existing `BASIS=` (Makefile + scripts/import_data.py).
- `POST /import/ranking` already routes through the generic endpoint; confirm `name` flows via
  the `options` body and the dry-run preview shows the parsed set name + rank source
  (column vs file-order).

Migrations & wiring:
- One migration (the two new tables). Register both models in app/db/models/__init__.py. Mount the
  rankings router.

Testing (offline, fixtures + in-memory SQLite — keep `make test`/`make lint`/CI green, ADD tests):
- A synthetic ranking CSV fixture WITH an explicit rank column (+ tier + a couple review/unmatched
  names), and a second WITHOUT a rank column (to exercise file-order fallback and the gap-on-held
  behavior).
- Tests: rank from column vs file-order fallback (a held/unmatched row leaves a gap, doesn't
  renumber); tier + value optional and parsed; set create then WHOLESALE REPLACE (re-import a set
  whose composition changed drops the players no longer present and re-orders the rest — assert no
  stale entries, no duplicates); the (source, name, season) uniqueness (a different NAME makes a
  separate set, same NAME replaces); GET /rankings and /rankings/{id} ordered by rank; dry-run
  writes nothing; alias recording is idempotent.

Constraints:
- Reuse app/ingest (parser, pipeline, registry) and app/matching — do NOT reimplement parsing,
  matching, or the two-phase flow. Stdlib `csv`; no new deps. SQLite-testable (generic sa types,
  hand-written writes, no PG-only SQL). Secrets/IDs from Settings. macOS; Postgres host port 5433
  — don't change it.

Acceptance criteria (verify before reporting done):
1. `make db-up && make migrate` applies the new migration cleanly (from-scratch + downgrade).
2. `make import KIND=ranking SOURCE=... SEASON=... NAME="..." FILE=...` dry-run previews
   matched/review/unmatched with the resolved set name + rank source; `--commit` writes a
   RankingSet + its ordered RankingEntry rows.
3. Re-importing a changed version of the same set REPLACES it wholesale — dropped players gone, no
   duplicates, ranks correct.
4. `GET /rankings` lists sets; `GET /rankings/{id}` returns them ordered by rank with player info.
5. `make test` and `make lint` pass; CI stays green.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed.
- Dry-run preview of both sample CSVs (with-rank and order-fallback), showing the set name and how
  rank was resolved.
- Proof of wholesale replace: a set imported, then re-imported with a dropped player + reordered
  ranks, and the resulting entries.
- The rank/tier/value alias handling and any decisions/deviations.
- Confirmation `market_line` (task 8) is the last remaining registry stub.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
