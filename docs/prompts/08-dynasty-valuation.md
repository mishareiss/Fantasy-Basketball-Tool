# Task 8 — Dynasty valuation: age-curve value engine + current-year vs dynasty horizons on the board

Context: Builds on task 7 (branch `ranking-import`, merged to `main`). Branch off `main`. This
starts the DRAFT-CRITICAL track — turning the projections and ages we already store into dynasty
value. Read docs/PLAN.md (Phase 1, "dynasty-aware value"), docs/FEATURE_SPEC.md section 4 (the two
always-computed value horizons + the Current-Year <-> Dynasty toggle), app/valuation/__init__.py
(the stub that describes exactly this), app/api/players.py (the board — BoardRow + the
`player_board` query params), app/db/models/projection.py + player.py, app/config.py (Settings and
`resolved_age_as_of`), and app/ages (compute_age) FIRST. Private ESPN fantasy basketball dynasty
tool, H2H points, CUSTOM scoring. We already have, per player: a projection priced under our
scoring (`Projection.fantasy_points_per_game` / `_total`) and an age (`Player.age`, computed at
`Settings.age_as_of` from nba.com birthdates). This task adds the engine that produces TWO value
horizons per player — current-year (win-now, age-agnostic) and dynasty (the same value run through
a transparent, tunable age/longevity curve) — and puts both on the board behind a horizon toggle.
No market_line this task (deferred). Create a new branch `dynasty-valuation`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope:

1) Age/longevity curve + value engine (app/valuation/):
- `age_multiplier(age: int | None) -> float`: a transparent, piecewise curve. A prime age band
  multiplies 1.0; younger players get an uplift (future value); older players a discount, floored.
  `age is None` -> 1.0 (no data means no adjustment) AND the engine flags that the value is
  un-aged rather than silently trusting it.
- `current_year_value(fppg) -> float` = the projected fantasy points per game itself (win-now,
  age-agnostic). `dynasty_value(fppg, age) -> float` = current_year_value * age_multiplier(age).
  Per-game is the primary basis (the board ranks per-game); add a total variant only if trivial.
- Pure functions, fully unit-tested, no DB. This is deliberately ONE multiplier on the current
  projection (FEATURE_SPEC 4), not a multi-year model — transparent and tunable now; a richer
  longevity model can come later.

2) Tunable curve params in Settings (app/config.py), documented, with sensible DEFAULTS:
- Add fields read from env, the way `age_as_of` is handled. Proposed defaults (a MODERATE dynasty
  curve — a starting point we will calibrate together, so make them trivially overridable):
    DYNASTY_PRIME_START = 24, DYNASTY_PRIME_END = 27   (multiplier 1.0 across the band)
    DYNASTY_YOUTH_BONUS_PER_YEAR = 0.04   (each year under prime start adds this: age 20 -> ~1.16)
    DYNASTY_DECLINE_PER_YEAR   = 0.05     (each year over prime end subtracts this: age 32 -> ~0.75)
    DYNASTY_MIN_MULTIPLIER     = 0.40     (floor)
- The curve reads ONLY from Settings so it's tunable without code changes (a small
  `dynasty_curve()` params object / helper on Settings is fine). Validate params are sane
  (prime_start <= prime_end, non-negative rates, 0 < floor <= 1).

3) Board integration (app/api/players.py):
- Add a `horizon` query param: 'current_year' | 'dynasty', default 'dynasty' (it's a dynasty
  startup). Unknown value -> 400.
- Extend BoardRow with `current_year_value`, `dynasty_value`, `age_multiplier`, and `age_adjusted`
  (False when age is None). Order the board by the SELECTED horizon's value desc (dynasty_value for
  dynasty; current_year_value — which equals fppg — for current_year), tie-break by full_name.
  Everything else (adp columns, position filter, age, age_as_of, one-row-per-player) unchanged.
- `horizon=current_year` must reproduce the existing fppg ordering byte-for-byte — a built-in
  regression check that the refactor didn't move the base board.

4) Transparency surface:
- `GET /valuation/curve` — returns the active curve params plus a small sample table (age ->
  multiplier for roughly ages 19..40), so the curve is inspectable and calibratable without
  reading code. Mount its router in app/api/__init__.py.

Migrations & wiring:
- None. Value is computed on read; NO Valuation table this task — storage/history (for value
  trends over time) is deferred until we actually need it. Just mount the new router.

Testing (offline: fixtures + in-memory SQLite; add tests; keep make test/lint + CI green):
- Curve: multiplier at sample ages — a young player (>1.0), the prime band (==1.0 at both ends),
  an aging player (<1.0), the floor holds at extreme age, None -> 1.0. Monotonic uplift below prime
  and monotonic decline above it. Changing a DYNASTY_* setting shifts the curve as expected.
- Value engine: current_year == fppg; dynasty == fppg * multiplier; missing age -> multiplier 1.0
  and age_adjusted False.
- Board: horizon=current_year reproduces the existing ordering exactly; horizon=dynasty reorders
  (a 21-year-old rises above a 33-year-old of equal raw value; an aging star drops); age_multiplier
  and age_adjusted correct; one row per player; unknown horizon -> 400.
- GET /valuation/curve returns params + the sample table.

Constraints:
- Reuse Projection, the board query, and Settings — compute value on READ, don't store it this
  task. SQLite-testable (no PG-only SQL). No new deps. All tunables from Settings, nothing
  hardcoded at call sites. macOS; Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `GET /players/board?horizon=dynasty` ranks by dynasty value; `?horizon=current_year` matches the
   old board ordering exactly.
2. Each board row carries current_year_value, dynasty_value, age_multiplier, age_adjusted.
3. `GET /valuation/curve` shows the tunable params + an age->multiplier table.
4. Overriding a DYNASTY_* env var moves the curve (proven by a test).
5. `make test` and `make lint` pass; CI stays green.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed.
- The age->multiplier table at the default params (so we can eyeball the curve's shape).
- The top ~15 players by DYNASTY value shown next to their CURRENT-YEAR rank, so we can see who the
  youth weighting moves (expect Wembanyama / Flagg / young risers up, aging stars down).
- How missing-age players are handled on the board.
- Anything you decided/deviated on, and confirmation market_line is the one remaining import stub.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
