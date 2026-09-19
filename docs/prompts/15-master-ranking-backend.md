# Task 15 — Master Ranking board (backend: model + API)

Context: A NEW, personal, PERSISTED board that is separate from the consensus board. The consensus
board aggregates other people's opinions; the Master Ranking is MISHA'S own draft board — a stable,
explicitly-ordered list he arranges by hand, seeded once from the consensus and then owned. This task
is the backend/data model + API only; the drag-to-reorder page is task 16.

The interaction (decided with Misha): REORDER. His explicit order IS the board and it is STABLE — a
player stays where he was last put even as the consensus underneath him moves. Per-player he can also
tag (target/fade), note (free text), and exclude (set aside). One board (NOT per-horizon) — horizon
only drives the consensus REFERENCE column. (Flagged as a default; do not build per-horizon orders.)

PRECONDITION / branch: tasks 12-14 must be on `main`. `git checkout main`, confirm
app/ranking/{sources,consensus}.py and app/api/consensus.py exist, then `git checkout -b master-ranking`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Read FIRST (paths relative to backend/):
- app/ranking/sources.py — `load_catalog(db, horizon) -> SourceCatalog` (all available sources over one
  shared pool), `available_specs`, `SourceCatalog.sources`, `percentile_for`.
- app/ranking/consensus.py — `consensus_board(...)` (the pure engine → ordered `ConsensusRow`s).
- app/api/consensus.py — `_load`, `consensus_view` (note how, with no sources requested, it selects
  ALL of `catalog.sources` and runs `consensus_board` at method='rank' — reuse that exact path for the
  reference column AND the seed), and how player identity (name/team/positions/age) is shaped.
- app/valuation/horizons.py — `HORIZON_DYNASTY`, `HORIZON_CURRENT_YEAR`.
- app/db/models/{player,adp}.py (a model + upsert-by-key example) and app/db/models/__init__.py
  (register new models here). app/api/__init__.py (router registration). An existing alembic migration
  for the create_table + batch_alter_table (SQLite) pattern.

Scope
1) `MasterRankEntry` model (app/db/models/master_rank.py; register in __init__): ONE row per player Misha
   has a board for. Fields: player_id (FK player.espn_player_id, ondelete CASCADE, UNIQUE — one board,
   one entry per player), rank: int | None (his 1-based place; NULL iff excluded), excluded: bool
   (default False), tag: str | None (constrained to 'target' | 'fade'; extensible), note: str | None,
   updated_at. Alembic migration; must run on SQLite.
2) Seed-from-consensus (once): when the board is empty, seed an entry for EVERY player in the consensus
   pool for the seed horizon (default HORIZON_DYNASTY), rank = his consensus position (method='rank',
   ALL available sources — reuse load_catalog + consensus_board exactly as consensus_view's default
   does). After seeding the order is EXPLICIT and STABLE — later consensus changes never re-order it.
3) Reconcile-on-read (this is what keeps a stable board correct as data changes): `GET /master/board`
   must return a COMPLETE board. A player now in the consensus pool with no entry is a NEW player:
   insert him at his consensus-implied slot (shifting the rest down), persist him, and flag `is_new` so
   the UI can highlight him — a stable board must still admit this year's rookies. A player who has an
   entry but has dropped out of the pool keeps his entry and is flagged `is_stale` (on your board, no
   current consensus). Do NOT silently drop or re-order existing entries.
4) API (register a new `market`-style router in app/api/__init__.py):
   - `GET /master/board?horizon=` (horizon in current_year|dynasty, default dynasty) → the ranked
     entries in rank order, each with: player identity, rank, tag, note, is_new, is_stale, and the
     CONSENSUS REFERENCE for that horizon — consensus_rank + delta (rank − consensus_rank; + = you have
     him higher than the field) — plus a separate `set_aside` list of the excluded players. Reuse the
     consensus computation; do not fork it.
   - `PUT /master/order` body {ordered_player_ids: [...]} → persist rank = index+1 for exactly the
     current NON-excluded set (validate it's a permutation of that set → 422 on a mismatch, naming what's
     missing/extra). This is what a drag-drop saves. Return the refreshed board.
   - `PUT /master/entries/{player_id}` body {tag?, note?, excluded?} → upsert those fields. Setting
     excluded=true removes him from the ranked order and reflows the remaining ranks; excluded=false
     re-inserts him at his consensus-implied slot (flagged is_new-style). Unknown player → 404; bad tag
     → 422. Return the refreshed board (or the entry + affected ranks).
   - (Optional) `POST /master/seed?reset=true` → reseed from consensus (guard: only when empty unless
     reset=true).
5) GUARDS: `GET /players/board` and `GET /board/consensus` / `GET /sources` stay byte-identical — this
   task only ADDS a table and a router. Add a test asserting those are unaffected.

Testing (backend offline; ADD tests; keep make test + make lint + npm test + CI green):
- seed: an empty board seeds one entry per pool player in consensus order; a second GET does not reseed
  or reorder.
- reorder: PUT /master/order persists; GET returns the new order; the order is STABLE across a later
  consensus change (change a projection/import in the fixture → the master order is unchanged, only the
  reference column/delta move).
- exclude/restore: exclude removes from the ranked order + reflows + appears in set_aside; restore
  re-inserts at the consensus slot; tag/note upsert + a bad tag → 422; unknown player → 404.
- new player: add a player to the pool (a new ranking/projection row in the fixture) → GET flags him
  is_new at his consensus slot without disturbing existing ranks; is_stale for a dropped-out player.
- reference: consensus_rank + delta correct for a couple of players under both horizons.
- guard: /players/board and /board/consensus unchanged; migration applies on SQLite.

Constraints: reuse app/ranking (load_catalog/consensus_board/percentile_for) and the consensus player
shaping — do NOT fork a second consensus, pool, or percentile path. One board, not per-horizon. No new
deps. SQLite-testable. secrets/config from Settings (seed horizon + any depth cap as a setting if you
add one). macOS; Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `GET /master/board` returns a complete, consensus-seeded board on first call; the order is explicit
   and survives later consensus changes.
2. `PUT /master/order` saves a reorder; `PUT /master/entries/{id}` sets tag/note/exclude and reflows.
3. A newly-imported player shows up flagged is_new at his consensus slot; excluded players sit in
   set_aside and restore correctly.
4. Existing boards are unchanged. `make test`, `make lint`, `npm test`, and CI all pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed; which branch you cut from.
- The MasterRankEntry schema + the reconcile-on-read rule (how new/stale players and the stable order
  are handled) and how the consensus reference is computed (which sources/horizon).
- A REAL example from the synced DB: the seeded top ~15 with their consensus reference + delta, then a
  reorder + an exclude + a tag, showing the board persists.
- Tests added; confirm /players/board and /board/consensus are untouched. Deferred: the drag-reorder
  PAGE (task 16), the draft-plan builder, per-source weighting.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
