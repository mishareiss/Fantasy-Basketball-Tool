# Task 13 — market_line source (backend)

Context: The third source in the ranking-board design ([[ranking-board-design]] / FEATURE_SPEC): a
manually-entered, season-long sportsbook read that becomes a VALUE source on the consensus board.
It builds on task 12 (the source adapters + consensus engine + `GET /sources` / `GET /board/consensus`).

PRECONDITION / branch: task 12 must be merged to `main` first (main will then contain
app/ranking/{horizons,sources,consensus}.py and app/api/consensus.py). `git checkout main`, confirm
those files exist, then `git checkout -b market-line`. (If task 12 is NOT yet merged, branch off
`consensus-board` instead and say so in your report.)

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Read FIRST (paths relative to backend/):
- app/scoring/engine.py (`score_stat_line`, `ScoringEngine`), app/scoring/settings.py
  (`LeagueScoringSettings`, `ScoringCoefficient` = stat_id/stat_name/points), app/scoring/stats.py
  (stat name<->id). The league scores per-game stat lines into fantasy points; the coefficients are
  synced from ESPN and STORED (app/db/models/league_settings.py) — load the stored coefficients the
  same way projections do; do NOT hardcode the formula.
- app/ingest/adp.py — the CLOSEST mirror: an import kind that UPSERTS by a key (it upserts one
  adp_entry per (source, season, player)). Copy its shape. Also app/ingest/registry.py (the `KINDS`
  registry, `ImportKind` = name/label/columns[field+aliases]/required_fields/upsert/accept,
  `UpsertContext`, `ResolvedRow`, `accept_matcher_threshold`), app/ingest/parser.py (column detection),
  app/ingest/pipeline.py (`run_import`), app/api/imports.py (kinds endpoint + options plumbing).
- app/db/models/{projection,adp}.py — Projection stores per (player, source, kind, season):
  raw_stats (JSON), per_game_stats (JSON), fantasy_points_per_game, fantasy_points_total,
  projected_games, per_game_basis. app/api/players.py `ranked_board(source=ESPN_SOURCE, kind=..., ...)`
  — note it FILTERS `Projection.source == source, Projection.kind == kind` and ranks on
  `fantasy_points_per_game`; and `_best_per_game()` (the missing-players fallback) maxes over EVERY
  projection with no source filter.
- app/ranking/sources.py (task 12) — the projection adapter is "one source per distinct
  `Projection.source`" and reuses `ranked_board(tiers=off)`.

DECISION TO CONFIRM WITH MISHA (odds -> value model). Implement this as the documented default; he
may adjust the dispersion dial:
- Each stat line is a per-game number L for a scored stat, with OPTIONAL American over/under odds.
- Implied prob from American odds: negative -O -> O/(O+100); positive +O -> 100/(O+100).
- De-vig: if BOTH sides given, p_over = p_over_raw / (p_over_raw + p_under_raw). If only one side or
  neither is given, treat as EVEN -> p_over = 0.5.
- Fair per-game value under a normal model: value = L + sigma * PHI_INV(p_over), where PHI_INV is the
  standard-normal quantile. Even odds -> PHI_INV(0.5)=0 -> value = L (the line stands, exactly what
  Misha asked for). Skewed odds shift the value toward the favored side. Clamp value >= 0.
- sigma (per-stat dispersion) is a CONFIG DIAL like DYNASTY_*/TIER_* (from Settings, .env). Default:
  sigma = MARKET_SIGMA_FRAC * max(L, 1.0) with MARKET_SIGMA_FRAC default 0.25. Keep it in ONE place,
  documented, unit-tested; do NOT scatter magic numbers.

Scope (backend only — the tailored entry/edit UI is task 14):
1) `MarketLine` model (app/db/models/market_line.py, register in app/db/models/__init__.py): one row
   per (source, season, player_id, stat_id) with line: float, over_odds: int|None, under_odds: int|None.
   UniqueConstraint on (source, season, player_id, stat_id) — so a single stat's odds can be UPDATED
   in place (Misha: "update odds if they change"). Alembic migration; must run on SQLite (create_table
   is fine; use batch_alter_table only if you ALTER).
2) De-vig + scoring (pure, unit-tested; put the odds math in e.g. app/ranking/market.py or
   app/scoring/): American-odds->prob, de-vig, the normal-model value with the sigma dial, clamp.
3) Derivation: for a player, gather their MarketLine rows -> fair per-game value per stat -> a stat_line
   dict keyed by stat name/id -> `ScoringEngine(stored_coefficients).score(...)` -> fantasy_points_per_game.
   UPSERT a `Projection(source='market', kind='market_line', season=...)` row for that player:
   raw_stats = the fair per-game stat line, per_game_stats = same, fantasy_points_per_game = the score,
   fantasy_points_total = per_game * games (games = that player's ESPN projected_games if present,
   else a config default, e.g. 70 — total is not what the board ranks on, so this is a display value),
   per_game_basis set appropriately. Re-derive the affected player(s) on EVERY import/update. This makes
   the market source surface through the EXISTING task-12 projection adapter as `projection:market`
   with ZERO adapter change (age curve, pool, percentile all reused) — "acts as its own rankings".
   Built only from the stats Misha enters (a partial projection if he enters few stats — document this).
4) Import kind `market_line` (app/ingest/market_line.py, mirror adp.py; register in `KINDS`): columns
   player, stat, line, over_odds, under_odds (with aliases); required = player, stat, line. Reuse the
   parser column-detect + the name matcher/candidates (same as adp/ranking). Its upsert writes/updates
   MarketLine rows by the (source, season, player_id, stat_id) key, then triggers derivation (3) for
   the touched players. `stat` cell -> stat_id via app/scoring/stats.py; an unknown stat is a clean
   per-row error, not a crash. Options: `source` (label, default 'market'), season as usual.
5) GUARDS — do not disturb what works:
   - `GET /players/board` (source='espn') must be byte-for-byte unchanged. Add a test asserting the
     ESPN board output is identical whether or not market rows exist.
   - Decide `_best_per_game`: the missing-players fallback should not be polluted by market rows —
     scope it to real projection sources (exclude 'market') OR justify leaving it; test the choice.
   - Market appears automatically in `GET /sources` (both horizons — it's a value source; dynasty
     applies the curve, current_year is raw) and is selectable in `GET /board/consensus`. Verify.

Testing (backend offline; ADD tests; keep make test + make lint + npm test + CI green):
- odds math: even/absent odds -> value == line; a two-sided juiced line de-vigs (vig removed, prob
  sums to 1); a one-sided line falls back to even; +150 vs -150 shift the value in the right
  directions; clamp at 0; sigma dial changes magnitude.
- scoring assembly: a known stat line scores to the expected fantasy points under the stored
  coefficients (mirror the worked example in [[league-scoring]]); unknown stat -> row error.
- storage/upsert: importing the same (player, stat) again UPDATES its odds/line in place (no dup
  row) and RE-DERIVES that player's market projection; a multi-stat player derives one Projection row.
- integration: `GET /sources?horizon=dynasty` and `=current_year` both list `projection:market`;
  `GET /board/consensus` including market returns its per-source cells; `GET /players/board`
  unchanged; migration applies on SQLite.

Constraints: reuse app/scoring (ScoringEngine/score_stat_line + the STORED league coefficients),
app/valuation (value_player/DynastyCurve via ranked_board), and the task-12 adapter/pool/percentile —
do NOT fork a second value, pool, or scoring path. sigma + games defaults from Settings/.env, not
magic numbers. SQLite-testable; no new deps; secrets/config from Settings. macOS; Postgres host port
5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. I can import a table of (player, stat, line, [over_odds], [under_odds]); a MarketLine row exists per
   (player, stat); re-importing one player's changed odds updates in place and re-derives.
2. Even/absent odds leave value == line; skewed odds shift it (normal model + sigma dial), clamped >=0.
3. `projection:market` appears in `GET /sources` under both horizons and is selectable in
   `GET /board/consensus`; the age curve applies under dynasty.
4. `GET /players/board` is unchanged. `make test`, `make lint`, `npm test`, and CI all pass.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed; and which branch you cut from (main or
  consensus-board).
- The odds->value implementation (formula, de-vig, the sigma dial + default) and the derivation
  (MarketLine -> Projection(source='market')), incl. the partial-projection behavior and the
  `_best_per_game` decision.
- A REAL example from the synced DB (after `make sync-ages`): enter a few market lines for 3-4 players
  and show market vs ESPN projection vs the Dizzle ranking on the consensus board.
- Tests added; anything deferred (the tailored entry/edit UI = task 14; per-source weighting; overrides).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
