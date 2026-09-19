# Task 17 — My Board: tiers + position filter (backend)

Context: First of five tasks for the draft-plan builder ([[docs/HANDOFF.md]] roadmap). This one adds
TIERS and a POSITION FILTER to the Master Ranking board (tasks 15/16). Backend + data only; the UI is
task 18. Tiers: auto-seeded from value gaps, then hand-adjustable, BOTH overall and per-position.

PRECONDITION / branch: tasks 15-16 on `main`. `git checkout main`, confirm app/api/master.py,
app/ranking/master.py, app/db/models/master_rank.py exist, then `git checkout -b board-tiers`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Read FIRST (paths relative to backend/):
- app/valuation/tiers.py — `assign_tiers(values: Sequence[float], params: TierParams)` and the `Tiering`
  result (pure gap-cluster tierer). app/config.py `Settings.tier_params()` (TIER_* dials). app/api/
  valuation.py (how tiers are shaped for a response today).
- app/api/master.py — `GET /master/board`, `MasterBoardResponse`/`MasterPlayerRow`, `consensus_positions`,
  and how the ordered board + reference are built. app/ranking/master.py — the reconcile engine + the
  seed-on-read pattern (mirror it for tier-break seeding).
- app/api/players.py — `ranked_board`/`value_player` and `dynasty_value` (the per-player value to tier
  on), and the `position` Query param + POSITIONS (PG/SG/SF/PF/C) filtering pattern.
- app/db/models/__init__.py, an existing migration (create_table + batch_alter_table for SQLite).

DESIGN (defaults chosen with Misha — flagged; don't silently change):
- TIERS ARE RANK BANDS. A tier is a contiguous band of the board defined by CUT-RANKS (a new tier
  starts at rank R). Stored as cut-ranks, NOT tied to player ids — so when Misha reorders, a player
  who moves up into the tier-1 band simply becomes tier 1. This reflows naturally with the order.
- AUTO-SEED ON DYNASTY VALUE. Seed the cut-ranks by running `assign_tiers` over each player's
  dynasty value (from the valuation engine) taken IN BOARD ORDER; open a tier where a value gap does.
  A player with no value (rank-only imports have no dynasty_value) CARRIES the current tier — never
  strand him in his own tier. Seed-on-read + persist the first time a scope has no breaks (mirror the
  board's own seed-on-read), so thereafter they are Misha's to move.
- SCOPES: 'overall' plus one per position (PG/SG/SF/PF/C). Overall tier = bands over the full board;
  position tier = bands over that position's sub-order. Every row carries BOTH its overall_tier and
  its position_tier.

Scope
1) `MasterTierBreak` model (app/db/models/…; register in __init__): rows of (scope, cut_rank) with a
   unique (scope, cut_rank); scope in {'overall','PG','SG','SF','PF','C'}. Alembic migration
   (create_table; SQLite-ok). Keep the tier math in ONE module (e.g. extend app/ranking/master.py or a
   sibling) — cut-ranks → per-player tier number, for a given ordered list.
2) Seed-on-read: when a scope has no stored breaks, compute them via assign_tiers over the dynasty
   values in that scope's order (carry rule for valueless players), persist, and return them — so the
   board always comes back tiered, and it's a stable starting point Misha then edits.
3) Extend `GET /master/board?horizon=&position=`:
   - `position` (PG|SG|SF|PF|C, optional) filters the ranked rows to that position (keep each player's
     true overall rank; the response is the filtered subset in board order). 422 on a bad position.
   - Every `MasterPlayerRow` gains `overall_tier: int` and `position_tier: int | None` (null for a
     player whose position has too few to tier / not applicable). Include the tier structure
     (per scope: the cut-ranks + tier count) in the response so the UI can draw dividers and labels.
4) `PUT /master/tiers` — save Misha's adjusted cut-ranks for a scope: body {scope, cut_ranks:[...]}.
   Validates scope and that cut-ranks are within 1..N and sorted/unique (422 otherwise). This is what a
   dragged divider saves. `POST /master/tiers/reseed?scope=` (optional) re-derives a scope's auto
   breaks (throw away manual edits for that scope).
5) GUARDS: `GET /players/board`, `/board/consensus`, `/sources`, and the T15/16 master order/entries
   endpoints stay behavior-identical (the board with no position filter returns the same order + rows,
   now merely carrying tier fields). Add a test asserting the untouched endpoints are unchanged and that
   adding/moving tier breaks never changes the ORDER or the ranks.

Testing (backend offline; ADD tests; keep make test + make lint + npm test + CI green):
- auto-seed: an un-tiered board seeds sensible cut-ranks from value gaps; a valueless (rank-only) player
  carries the tier above him rather than opening his own; second GET does not reseed.
- rank-band semantics: moving a player up into the tier-1 band makes his overall_tier 1 without any tier
  edit; reordering never changes stored cut-ranks.
- per-position: position_tier is computed over the position sub-order; ?position=PG returns only PGs in
  board order with their overall rank intact; bad position → 422.
- PUT /master/tiers persists adjusted cut-ranks (422 on out-of-range/unsorted/dup); reseed restores auto.
- guard: /players/board + /board/consensus unchanged; order/ranks unchanged by any tier operation;
  migration applies on SQLite.

Constraints: reuse assign_tiers + Settings.tier_params() + the valuation dynasty value + the master
board's ordering/consensus — do NOT fork a second tierer, value path, or board builder. Tier math pure
and unit-tested separately from the DB. No new deps. SQLite-testable. Config from Settings. macOS;
Postgres 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `GET /master/board` returns every row with overall_tier (and position_tier where applicable) and the
   tier structure per scope; it's auto-seeded on first read and stable after.
2. `?position=` filters to one position with tiers intact; bad position → 422.
3. `PUT /master/tiers` saves dragged dividers; reordering the board reflows tiers without touching the
   stored breaks; reseed restores the auto split.
4. Order, ranks, and every other board/endpoint are unchanged. `make test`, `make lint`, `npm test`, CI pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed; which branch you cut from.
- The MasterTierBreak schema + the cut-rank → tier mapping and the auto-seed rule (incl. the valueless
  carry) and how per-position tiers are computed.
- A REAL example from the synced DB: the seeded overall tiers (top ~30 with tier numbers + where the
  cliffs fall), and the PG-only view with position tiers.
- Tests added; confirm the untouched endpoints are byte-identical. Deferred: the tier UI + position
  filter UI + draft-mode toggle (task 18); the draft engine (task 19+).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
