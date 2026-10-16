# Task 32 — "Auto-fill lineup": start everyone who plays, best players first (Actual and Plan)

Context: The Team tab (tasks 27–31a, merged; `main` = fede1ca) shows my roster per day in ESPN-style slots, in
an Actual view and a Plan view. Each view has its own lineup storage (`LineupAssignment` vs
`PlanLineupAssignment`) and its own save endpoint (`PUT /team/lineup/{sp}` vs `PUT /team/plan/lineup/{sp}`),
both validated by the shared lineup validator in `app/team/grid.py`. Misha wants a one-click **Auto-fill
lineup** in BOTH views, as two separate actions: in Actual it writes the Actual lineup, in Plan the Plan
lineup.

The rule (Misha's words): move every player who plays that day into the lineup. **Tiebreak by fantasy points
per game: actual 2026-27 FPTS/G if he has played games this season, else projected FPTS/G.**

Read first: `backend/app/team/{grid,lineup,views,plan,plan_views,calendar,display}.py`,
`backend/app/api/team.py`, `backend/app/team/schemas.py`, `frontend/components/team/{TeamPage,RosterTable}.tsx`,
`frontend/lib/team.ts`, `frontend/lib/api.ts`, the team tests.

Branch: `main` clean, then create `auto-fill-lineup`. Check for `* 2.*` duplicates before staging, and stage
none.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Scope
1. **Pure optimizer** `backend/app/team/autofill.py` (no DB, no ESPN; a test enforces it):
   `best_lineup(players, slot_counts, locked) -> {player_id: slot_id}`.
   - Inputs per player: `id`, `eligible_slots`, current slot, `plays` (bool), `value` (float), `injury_status`.
   - Starting slots = PG, SG, SF, PF, C, UT×2 (from `slot_counts`). IR players stay on IR, untouched. Everyone
     else not chosen goes to BE.
   - **Objective, lexicographic**: (1) maximize the number of starting slots filled by players who **play**,
     then (2) maximize the sum of `value` of those starters. Solve it **exactly**, not greedily (a greedy
     PG-first fill can strand a C-only player). With 7 slots, a DP over slot bitmasks × players is tiny; any
     exact method is fine. No new dependencies. Deterministic ties: higher value, then lower player id.
   - A player **plays** if he has a game that day AND his injury status isn't `OUT` / `SUSPENSION` (treat
     them as not playing). DAY_TO_DAY counts as playing.
   - Players who don't play never take a starting slot while a playing eligible player is benched. If fewer
     than 7 play, the remaining starting slots get the best-`value` non-players who fit (harmless, and it
     matches ESPN's look), unless `leave_empty=true`, which leaves them empty.
   - `locked`: players whose slot must not change (see 3). They keep their slot, and the optimizer fills
     around them.
2. **Value** = actual-season (`actual_current`) FPTS/G if he has a row with GP > 0 for this season, else
   projected FPTS/G, else 0. Reuse `stat_lines` from `views.py`. Don't re-query per player.
3. **Locks (Actual view, today only)**: a player whose game today has already started (`starts_at <= now`)
   is locked in his current slot, as ESPN locks him. So is any player sitting in a starting slot whose game
   has started. Other days have no locks. The Plan view never locks.
4. **Endpoints**:
   - `POST /team/lineup/auto` `{sp, through_sp?, leave_empty?}` writes the Actual lineup.
   - `POST /team/plan/lineup/auto` (same body) writes the Plan lineup, optimizing over the **plan roster**
     for each day.
   - With `through_sp`, compute **each day separately** (each day's own games). Don't copy one day's lineup
     across.
   - Write through the existing save paths, so validation is reused and the rows land as `source='manual'`
     (Actual) or plan rows (Plan). Return the day view for `sp` plus `days_written` and, per day, a short
     summary `{sp, starters_playing, slots: 7, benched_playing: [names]}`. `benched_playing` is non-empty only
     when more players play than there are slots, which tells Misha who sits.
5. **UI**: an **Auto-fill** button in the roster header, in both views (labelled "Auto-fill lineup", with
   the Plan view's version clearly acting on the plan). Beside it, a small "through end of matchup"
   checkbox, matching the slot-move one. After the call, refetch the day (house rule) and show a one-line
   result such as "7 of 7 starters playing. Benched with games: Ty Jerome, Rudy Gobert". If the result
   equals the current lineup, say "Lineup already optimal".

## Testing
- Optimizer:
  - more players than slots: the best values start and the rest bench
  - a position puzzle where greedy fails, e.g. a C-only player plus a PF/C player with one C and one UT
    open: both must start
  - fewer players than slots: fill with non-players, or leave empty with `leave_empty`
  - OUT with a game → treated as not playing; DTD → playing
  - IR untouched
  - locked players keep their slots and the rest fill around them
  - deterministic ties
  - value source: actual over projected when GP > 0
- Endpoints: Actual writes only `LineupAssignment`, Plan writes only `PlanLineupAssignment`; `through_sp`
  computes per day (a fixture where days differ); locks only for today in Actual (freeze `now`); the summary's
  `benched_playing`.
- Frontend: the button in both views calls the right endpoint, the through checkbox works, the result line
  shows, and it refetches.
- All green: `make test`, `make lint`, `npm test`, `npm run lint`, `npm run build`.

## Acceptance
On Misha's DB: Actual view, Wed 10/21 (14 players with games), Auto-fill → 7 of 7 starters playing, the 7
highest-value players with games in legal slots, and the rest listed as benched with games. Plan view on the
same day uses the plan roster. Undo by "Sync lineup from ESPN" for Actual, and clear the plan rows for Plan
(report how).

## Report back with
Branch, files, confirmation it's STAGED, not committed, the acceptance result (the 7 starters + the benched
list), decisions/deviations, and test counts.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
