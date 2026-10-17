# Task 34 — Complete the projections: impute DD/TD, OREB and PF; stop ranking on stale projections

Context: ESPN's projected split (`statSourceId=1`) carries PTS/REB/AST/STL/BLK/TO/FTM/FTA/3PM… but **no DD (37),
TD (38), OREB (4), DREB (5) or PF (9)**. Verified on the fixture: our score equals ESPN's own `appliedTotal`
exactly for every player checked, so ESPN's projections omit these too. Our league pays **DD +10, TD +30,
OREB +0.5 (on top of REB 4), PF −0.2** (`LeagueSettings` scoring rules). So every projection-based number in the
app (draft/dynasty boards, consensus, master board, Team tab FPTS/G, waiver ranking) undervalues stat-stuffers
badly. From last season's actuals in the fixture: Jokic 55 DD in 65 GP (~+8.5 FPTS/G before TDs), Towns 56/75
(~+7.5), Wembanyama 42/64 (~+6.6). Last-season ACTUAL splits (`kind=actual_season`) DO carry DD/TD/QD/OREB/
DREB/PF, which gives us per-player rates to impute from.

Second issue: for players ESPN hasn't projected for 2026-27, the Team tab's `projected` basis falls back to the
newest projection held, which is the **2025-26 projection**. On Misha's DB, 3 of the top 40 free agents are
like this, including the #1 (Cam Whitmore, 89.1).

Read first: `backend/app/espn/{statsplits,sync}.py` (`sync_projections`, `ProjectionSplit`, `COUNTING_STAT_IDS`),
`backend/app/scoring/{engine,projections,stats}.py`, `backend/app/db/models/projection.py`,
`backend/app/team/views.py` (`stat_lines`, the `projected` basis), `backend/app/api/players.py` (board,
`ranked_board`, player detail), `backend/app/ranking/sources.py`, the projection/board/consensus tests, and
`docs/HANDOFF.md`.

Branch: `main` clean. Create `projection-completeness`. It is independent of tasks 32/33, so if they're
unmerged, branch off `main` anyway and tell me. Check for `* 2.*` duplicates before staging, and stage none.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Scope
1. **Pure imputation module** `backend/app/projections/impute.py` (no DB; unit-tested). Input: one
   projected per-game line (+ projected GP), the player's last-season actual per-game line (may be None), his
   primary position, and league baselines. Output: the same line with `DD`, `TD`, `OREB`, `DREB`, `PF` filled
   **only where the projection lacks them** (never overwrite a stat the source gave), plus an `imputed:
   {stat: method}` record.
   - **OREB/DREB**: projected REB × the player's last-season OREB/REB share. Without his own history (or with
     fewer than `IMPUTE_MIN_GP` = 15 GP last season), use the league position baseline share (computed from
     all last-season actuals by primary position, GP-weighted). DREB = REB − OREB.
   - **PF**: projected MIN × his last-season PF per minute, else the position baseline.
   - **DD and TD rates**: a blend of (a) his empirical last-season rate (DD/GP, TD/GP) and (b) a distribution
     model from the projected line: model PTS, REB, AST, STL, BLK as independent per-game normals with mean =
     projected per-game and sd = `IMPUTE_SD_K` × sqrt(mean) (a dial). Then P(≥2 of them ≥ 10) = DD and
     P(≥3 ≥ 10) = TD, computed exactly over the 5 categories (no sampling). Blend weight
     w = GP_last / (GP_last + `IMPUTE_PRIOR_GP`) (dial, default 20). Rookies/no history → pure model.
     **Calibrate `IMPUTE_SD_K`** on last season: pick the k that minimizes the GP-weighted squared error between
     the model's DD rate (run on each player's ACTUAL per-game line) and his actual DD rate, over players with
     ≥ 20 GP. Commit the fitted default with a comment giving the fit and the date, and add a test pinning it
     roughly. Report the fit quality.
   - Clamp everything to sane ranges (0 ≤ TD ≤ DD ≤ 1, OREB ≤ REB).
   - QD: ignore (leave absent).
2. **Wire it into the ESPN projection sync**: before scoring, run imputation on every ESPN `projected_season`
   split. The stored `Projection.per_game_stats`/`raw_stats` include the imputed stats (season totals =
   per-game × GP), and add `Projection.imputed_stats` JSON (migration, `batch_alter_table`-safe) recording
   which stats were imputed and how. `fantasy_points_total/per_game` are then scored by the existing engine
   over the completed line. `source_fantasy_points_total` stays ESPN's raw `appliedTotal`, untouched, so the
   difference is visible. Market and imported projections are NOT imputed in this task (note it as a
   follow-up). A setting `IMPUTE_ESPN_PROJECTIONS=true` toggles this, so Misha can compare.
3. **Stale projections**: a projection is *stale* if its `season` < the synced season.
   - Team tab `stat_lines(basis='projected')`: if the newest projection is stale and the player has a
     last-season actual line (≥ `IMPUTE_MIN_GP` GP), use **last season's actual** per-game line (already priced
     under our scoring) instead, labelled `projection_source: "last_season_actual"`. Else use the stale
     projection, labelled `"stale_projection"`. Fresh ones are `"espn"`. Expose `projection_source` +
     `projection_season` on `StatLine` (schemas) wherever `projected` is served (day view, free agents, card,
     auto-fill value if task 32 has landed).
   - The draft/consensus boards keep their own season logic (they already pick by season). Don't change
     which rows they read, beyond the imputed stats now being in the fresh ESPN projections.
4. **Frontend**: a tiny tag next to FPTS/G when `projection_source` isn't `"espn"`: `25-26 actual` or `stale
   proj`, with a tooltip. In the player card, show imputed stats in the stat table with a subtle "est." marker
   and a one-line note: "DD/TD/OREB/PF estimated: ESPN doesn't project them". Add `DD` to the Team tab's
   `DISPLAY_STATS` (it's in the backend list already; it now has data).
5. **Impact report**: a script `scripts/impute_report.py` (and a `make impute-report` target) that prints,
   for the top 150 by projected FPTS/G, before vs after (rank and FPTS/G), sorted by biggest change. Run it on
   Misha's DB and include the top 15 movers each way in your report.

## Testing
- Impute:
  - OREB/DREB from own share vs baseline
  - PF
  - the DD/TD exact probability (hand-checked small cases: all means 0 → 0; two stats at mean 30 with tiny
    sd → ~1)
  - the blend weights
  - rookies
  - never overwrites present stats
  - clamps
- Calibration: the fitter recovers a known k on a synthetic population.
- Sync:
  - imputed stats stored + flagged
  - FPTS/G rises for a big-man fixture
  - `source_fantasy_points_total` unchanged
  - the toggle off reproduces the old numbers exactly
  - idempotent re-sync
- Stale logic: fresh → `espn`, stale + history → `last_season_actual`, stale without history →
  `stale_projection`.
- Boards: still green. Update expectations that legitimately move because values changed, and explain each in
  the report. Don't loosen assertions blindly.
- Frontend: the tags and the est. marker.
- All green: `make test`, `make lint`, `npm test`, `npm run lint`, `npm run build`; migration round-trip.

## Report back with
- Branch, files, and confirmation it's STAGED, not committed.
- The calibrated k and fit quality.
- 5 example players: before/after FPTS/G with imputed DD/TD/OREB/PF (include Jokic, Towns, Gobert,
  Wembanyama, a guard).
- The impact report's top movers.
- Stale-projection counts on the real DB (how many free agents switched to last-season actual).
- Decisions/deviations, and test counts.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
