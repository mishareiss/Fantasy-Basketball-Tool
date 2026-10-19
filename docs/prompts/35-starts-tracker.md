# Task 35 — Starts tracker: filled vs possible starts per day and per matchup, plus a games-left column

Context: H2H points is won on **starts**: 7 starting slots (PG, SG, SF, PF, C, UT, UT) × every day of the
matchup. The Team tab (tasks 27–34, merged) shows a day strip with `my_games` (how many of my players have a
game). That count misleads: Wed 10/21 shows 14, but only 7 can start (7 wasted), while Thu 10/22 shows 4 (3
empty starts, which is exactly where a streamer helps). Misha needs to see, per day and per matchup, how many
starts he's filling versus how many are possible, in both Actual and Plan, so he knows where to stream. This is
also the foundation of the future Matchup tab (day-by-day and full-week projections).

Building blocks that exist: `app/team/autofill.py` (`best_lineup`: the exact optimizer; its first objective is
"starters who play"), `resolve_lineup` + the Plan-view lineup resolution, `games_index`, `stat_lines` (with
task 34's completed projections and `projection_source`), the calendar/matchup helpers, and the Plan
simulation (`simulate`, `lands_sp`, after-tip-off timing).

Read first: `backend/app/team/{autofill,autofill_views,views,plan,plan_views,lineup,grid,calendar}.py`,
`backend/app/api/team.py`, `backend/app/team/schemas.py`, `frontend/components/team/{TeamPage,DayPicker,RosterTable}.tsx`,
`frontend/lib/{team,api}.ts`, and the team tests.

Branch: `main` clean (task 34 merged). Create `starts-tracker`. Check for `* 2.*` duplicates before staging,
and stage none.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Definitions (per day, per view)
- **plays**: has a game that day and isn't OUT/SUSPENSION (same rule as auto-fill). IR-slotted players never count.
- **filled**: starting slots occupied, in that day's lineup *as set* (Actual: the resolved Actual lineup; Plan:
  the resolved Plan lineup), by a player who plays.
- **possible**: how many starting slots the optimizer could fill with players who play from that day's roster
  (Actual roster, or the plan roster in Plan view). Reuse `best_lineup` and take its count. Don't re-implement.
- **empty** = 7 − possible: no roster player can fill those starts. These are **streaming opportunities**.
- **left_on_bench** = possible − filled: starts lost to the lineup as set. Auto-fill would fix them.
- **benched_with_games**: playing players who can't start because slots are full (count + names; informational).
- **projected_points**: sum of the value (the auto-fill value rule: actual FPTS/G if GP > 0, else projected)
  of the optimizer's starters who play. Also return `projected_points_as_set` for the lineup as set.

## Scope
1. **Pure aggregation** in `app/team/starts.py` (no DB): given per-day rosters, lineups, plays flags and values,
   produce the per-day numbers above plus matchup totals: `filled`, `possible`, `max = 7 × days`, `empty`,
   `left_on_bench`, `projected_points`, `projected_points_as_set`. For days already played (sp < today), mark
   `past: true`; the numbers stay computed the same way (actual box-score starts are a later task).
2. **Endpoint** `GET /team/starts?matchup_id=&view=actual|plan` (default: the current matchup, actual) →
   `{matchup {id,label,start,end}, view, days: [{sp, date, weekday, past, filled, possible, empty,
   left_on_bench, benched_with_games: [{id,name}], projected_points, projected_points_as_set}], totals: {...}}`.
   One batched load for the matchup's days. Add a query-count guard (constant in the number of days).
3. **Day strip**: each matchup day shows **`filled/7`**, with an amber marker + count for `empty > 0` ("3 open")
   and a red marker for `left_on_bench > 0` ("2 benched"). It follows the roster's Actual/Plan toggle. The
   tooltip lists the benched-with-games names. Keep `my_games` available in the tooltip.
4. **Matchup summary bar** above the roster: `Starts 38/49 · 6 open · 1 benched · Proj 2,140 pts
   (as set 2,095)`. In Plan view, also show the delta vs Actual, e.g. `+5 starts, +412 pts vs Actual`. That's
   the number that tells Misha whether his streaming plan is worth it.
5. **Roster "Wk G" column**: for each roster player, his **games remaining in the matchup from the selected day
   (inclusive)**. In Plan view, count only days he's on the plan roster: a planned pickup counts from
   `lands_sp`, and a player leaving counts through his last day. Narrow, numeric, between Game and the stat
   columns. Keep no horizontal scroll at 1280px (re-verify; tighten if needed).
6. **Waiver panel**: add the same "Wk G" (games remaining in the roster's current matchup from today)
   next to `games_in_days`, so drop/add comparisons use one yardstick. One batched query.

## Testing
- Pure:
  - possible vs filled vs empty on fixtures (14 playing → possible 7, benched 7; 4 playing → empty 3)
  - an ineligible-position case where possible < players playing (e.g. 8 guards with games, no forwards:
    possible 5 → empty 2)
  - OUT excluded; IR excluded
  - left_on_bench when the set lineup benches a player who plays
  - totals; projected points for the optimizer vs as set
- Plan:
  - a planned after-tip-off pickup adds a start from `lands_sp` and none before
  - a leaving player counts through his last day
  - the Plan-vs-Actual delta
- Endpoint: shape, default matchup, the query guard, past flags.
- Frontend:
  - the strip's `filled/7` + the open/benched markers in both views
  - the summary bar + the Plan delta
  - the Wk G column on the roster (Plan semantics) and on the waiver list
  - no horizontal scroll (existing width test or a new assertion)
- All green: `make test`, `make lint`, `npm test`, `npm run lint`, `npm run build`.

## Acceptance (Misha's DB, Matchup 1)
Report the per-day `filled/possible/empty/left_on_bench` for 10/20–10/25 in Actual and in Plan, the matchup
totals and the Plan-vs-Actual delta. Sanity-check Wed 10/21 (14 with games → possible 7, ~7 benched) and Thu
10/22 (4 with games → about 3 open).

## Report back with
Branch, files, confirmation it's STAGED, not committed, the acceptance table, decisions/deviations, and test
counts.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
