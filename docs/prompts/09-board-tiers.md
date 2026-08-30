# Task 9 — Draft tiers: auto-tier the value board by score-gap clustering

Context: Builds on task 8 (branch `dynasty-valuation`, merged to `main`). Branch off `main`. Read
docs/FEATURE_SPEC.md section 6 (tiers) and section 4, docs/PLAN.md (Phase 1), app/valuation/
(curve.py `DynastyCurve`, engine.py `current_year_value`/`dynasty_value`/`value_player`, the
`__init__` surface), app/api/players.py (the board — how `horizon` + the values are computed and
what `BoardRow` carries), app/config.py (Settings and how the DYNASTY_* tunables + `dynasty_curve()`
are done), and app/api/valuation.py (`GET /valuation/curve`) FIRST. Private ESPN fantasy basketball
dynasty tool, H2H points, CUSTOM scoring. The board now ranks players by dynasty or current-year
value; TIERS are what turn that flat list into a draft plan — "draft anyone still on the board in
this tier." This task adds transparent, tunable AUTO-tiering by score-gap clustering on the selected
horizon, surfaced on the board and on an inspection endpoint. Manual/drag tier breaks are a UI
action -> DEFERRED to the frontend task; per-position and per-imported-ranking-set tiers are deferred
too (imported sets already carry their source's tier column from task 7). Create a new branch
`board-tiers`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope:

1) Tiering engine (app/valuation/tiers.py):
- A pure, deterministic function `assign_tiers(values, params) -> list[int|None]` over the
  DESCENDING-sorted values (or (player_id, value) pairs) that opens a new tier where the gap down
  to the next value is "significant": a break where `gap > tier_gap_multiple * typical_gap`, with
  `typical_gap` = the MEDIAN gap across the tiered pool (median, so the few huge gaps at the very
  top don't inflate the threshold and swallow real breaks). Enforce a minimum tier size (no
  singleton tiers unless the gap is genuinely huge) and a maximum tier count (if exceeded, merge at
  the least-significant breaks). Return a tier index per player, 1 = top tier.
- Tier over the top-N draftable players (a param), not the whole ~1,095 pool, so boundaries are
  stable and meaningful; players below N get tier None.
- Record, per break, the gap that opened it (for the inspection endpoint and tests). No DB; fully
  unit-tested.

2) Tunable params in Settings (app/config.py), mirroring the DYNASTY_* pattern:
- `TIER_GAP_MULTIPLE` (default ~2.0 — a break is a gap > 2x the typical gap), `TIER_MIN_SIZE`
  (default 2), `TIER_MAX` (default 15), `TIER_POOL` (default 150 — how many top players to tier
  over). Validate sane (positive, min_size >= 1, max >= 1). A `Settings.tier_params()` helper.

3) Board integration (app/api/players.py):
- Compute tiers over the top `TIER_POOL` players of the SELECTED horizon (the same value the board
  already ranks by — do NOT recompute value a second way), then annotate each returned `BoardRow`
  with `tier` (int, or None for players outside the tiered pool). Add `?tiers=auto|off` (default
  auto). Tiers are computed on the OVERALL horizon ranking; the existing `position` filter still
  only selects which rows show — a PG keeps his overall tier. One row per player, ordering unchanged.
- Include a small tier summary in the board response (tier -> size, value high/low) so a client can
  draw dividers without recomputing.

4) Inspection endpoint:
- `GET /valuation/tiers?horizon=dynasty|current_year` — the tier structure over the tiered pool:
  each tier's number, size, value range, and the break gap that opened it. Put it in the existing
  valuation router next to /valuation/curve.

Migrations & wiring:
- None (compute-on-read; NO Tier table — persistence + manual/drag breaks land with the UI). No new
  router if /valuation/tiers goes in the existing valuation router.

Testing (offline: fixtures + in-memory SQLite; add tests; keep make test/lint + CI green):
- Engine: a values fixture with obvious gaps -> expected breaks; raising TIER_GAP_MULTIPLE yields
  fewer/larger tiers; TIER_MIN_SIZE prevents singletons; TIER_MAX caps the count (least-significant
  breaks merged); TIER_POOL bounds the tiered set; tier never decreases as value decreases.
- Board: rows carry `tier`; tiers are identical whether you request limit=20 or limit=200 (computed
  over the pool, not the page); `tiers=off` -> tier None; the position filter keeps overall tiers;
  dynasty vs current_year produce different tiers (different value order).
- `GET /valuation/tiers` returns the structure with per-break gaps.

Constraints:
- Reuse the board's existing horizon value computation — tier the SAME numbers the board ranks by,
  don't introduce a second value path. Settings-driven (nothing hardcoded). SQLite-testable; no new
  deps. macOS; Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `GET /players/board?horizon=dynasty&tiers=auto` annotates rows with tiers; `tiers=off` omits them.
2. Tiers are stable across `limit` and reflect the horizon (dynasty vs current_year differ).
3. `GET /valuation/tiers` shows tiers with sizes, value ranges, and the gap opening each.
4. Changing a TIER_* env var changes the tiering (proven by a test).
5. `make test` and `make lint` pass; CI stays green.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed.
- The tier breakdown at default params for the DYNASTY board — tier #, size, value range, and the
  top couple of names in each — so we can eyeball whether the tiers feel right and how many it made.
- Where the big gaps landed (the breaks) and how many tiers resulted.
- How players outside the tiered pool / with missing age are handled.
- Anything you decided/deviated on, and confirmation that manual/drag breaks + per-position tiers are
  the deferred pieces (UI task).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
