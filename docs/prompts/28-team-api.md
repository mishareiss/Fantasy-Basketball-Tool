# Task 28 — Team API: the day view, lineup edits, the free-agent list with day filters, and the player card

Context: Task 27 (`team-data`, merged) stores the NBA schedule (`NbaGame`), the matchup calendar
(`app/team/calendar.py`), fantasy teams and my team (`FantasyTeam.is_mine`, `my_team()`), rosters
(`RosterEntry`), per-day lineups (`LineupAssignment` + pure `resolve_lineup`), ESPN transactions and waiver
status (`app/team/waivers.py`), and current-season actual splits (`Projection.kind` `actual_current` /
`actual_last_7` / `_15` / `_30`). This task is the **read/write API the Team tab will call**. The UI is
task 29. **Backend only**: no frontend changes beyond regenerating/adding TS types if the repo does that.

What the Team tab shows (Misha's spec, already agreed):
- A calendar picks a **day** (scoring period). My roster shows in ESPN-style slots PG, SG, SF, PF, C, UT,
  UT, BE×n, IR×n. Each player has a stat line and a **game column** in ESPN's format: `@ LAL 7:00 PM` (away),
  `vs LAL 7:00 PM` (home), times in **Pacific**, `TBD` when ESPN has no time, blank when there's no game.
- A stats toggle: **projected** (ESPN 2026-27 projection under our scoring) or **actual 2026-27** with
  Season / L7 / L15 / L30. Actual is unavailable until games have been played.
- Misha can **move players between slots by hand** (only where their position allows). Edits stay local
  until he presses "Sync lineup from ESPN" (`POST /sync/lineup`, already built), which overwrites them.
- A **waiver list**, independent of the roster's day: Player, Game, one FPTS/G column (projected/actual
  toggle). A day filter has four modes: off (everyone); one day; several days where the player plays on
  **any** of them; several days where he plays on **all** of them. Waiver players show when they clear.
- Clicking a waiver player opens a **card**: full stats, plus his games over the **next 7 days**, grouped
  by matchup.

Read first:
- `backend/app/api/team.py` (`GET /team/status`; extend this router), `backend/app/team/*`
  (`calendar.py`, `lineup.py`, `waivers.py`, `store.py`), `backend/app/db/models/team.py`
- `backend/app/api/players.py`: `player_detail` / `PlayerDetailResponse` / `SeasonLine` / `_per_game` /
  `_newest_by_player` (reuse the stat-line shape and the "absent is absent" rule: missing stats are absent,
  not 0)
- `backend/app/api/sync.py` (`POST /sync/lineup`), `backend/app/espn/sync.py` (kind constants),
  `backend/app/scoring/settings.py` (`roster_slots`), `espn_api.basketball.constant` (`POSITION_MAP`,
  `PRO_TEAM_MAP`)
- `backend/tests/test_api_team*.py` / the task-27 fixtures and conftest pins

Branch: confirm `team-data` is merged into `main` and `main` is clean. If not, STOP and tell me. Otherwise
create `team-api` off `main`.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Scope

1. **Shared building blocks** in a new `backend/app/team/views.py` (DB reads) + pure helpers. Unit-test both:
   - `games_index(db, season, sps) -> {(sp, pro_team_id): GameInfo}`. One query for all requested days.
     `GameInfo` = game_id, sp, opponent pro_team_id + abbrev, `is_home`, `starts_at` (UTC), `start_time_tbd`.
     A team plays at most once a day. If the data ever has two, keep the earlier one and log it.
   - Pure `format_game(info) -> str`: `"@ LAL 7:00 PM"` / `"vs LAL 7:00 PM"`, Pacific, no leading zero,
     `"@ LAL TBD"` when TBD. Test: a 19:00Z game in October = 12:00 PM PDT, a game after the Nov 1 fall-back
     shows PST correctly, and a 02:00Z game shows the PREVIOUS Pacific date's evening (7:00 PM).
   - `stat_lines(db, season, player_ids, basis) -> {player_id: StatLine}`. `basis` ∈ `projected | season |
     l7 | l15 | l30`. `projected` = ESPN `projected_season` for the newest projection season held (reuse
     the existing newest-by-player logic). The others = the four `actual_*` kinds for `season`. `StatLine` =
     `fantasy_ppg`, `games`, `per_game` (stat-name map, counting stats only, absent stays absent). A player
     with no row for that basis → absent from the dict (the UI shows an em dash).
   - `DISPLAY_STATS`: the ordered column list the tab prints: `MIN, PTS, REB, AST, STL, BLK, TO, 3PM, FTM,
     DD`. The response carries `stat_columns` = those of `DISPLAY_STATS` that appear in at least one returned
     line, so the UI never hardcodes it.
2. **`GET /team/day?sp=&basis=`**: my roster on one day.
   - `sp` defaults to `effective_period(season, now)`. Clamp/validate to `[1, final_period]` and return 422
     outside. `basis` defaults to `projected`. If `basis` isn't projected and `actuals_available` is false,
     still answer (every line absent) and say so with `actuals_available: false`.
   - Response: `season, sp, date, matchup {id,label,start,end}, is_today, team {id,name,abbrev},
     actuals_available, basis, stat_columns, lineup_source` (`espn` | `manual` | `carried` (from an earlier
     day) | `roster` (fallback to RosterEntry)), `acquisitions {used, limit, matchup_id}` and `slots: [...]`.
   - `slots` is the ESPN-style grid, built from `LeagueSettings.roster_slots` counts in the order PG, SG, SF,
     PF, C, UT×2, BE×n, IR×n, where n = the stored counts. Every slot is a row: `{slot_id, slot, player|null}`.
     **Empty slots render as rows with player null**, as ESPN shows them. Placement comes from
     `resolve_lineup` for my team. If more players are resolved into a slot than it has room for (shouldn't
     happen; guard it), the extras go to BE rows appended at the end, with a `warnings` entry. Bench grows
     to fit the roster size even past the BE count, so nobody is ever hidden.
   - Player object: `id, name, nba_team, positions, eligible_slots, injury_status, game` (GameInfo + `display`
     string, or null), `stats` (StatLine or null).
   - `acquisitions.used`: from `FantasyTeam.matchup_acquisition_totals` for this day's matchup. The key format
     is unconfirmed (stored raw, `{}` pre-season), so accept the str or int matchup id, and use 0 when it's
     absent. `limit` from the stored acquisition settings (7). This is read-only here. Task 30 adds planned
     moves on top.
3. **`PUT /team/lineup/{sp}`**: a manual lineup edit for my team. Body `{assignments: [{player_id, slot_id}],
   through_sp?: int}`.
   - Validation (422 with a list of human-readable errors, nothing written on any error):
     - every currently rostered player of my team appears exactly once, and nobody else appears
     - each slot's count ≤ its `roster_slots` count, with BE unlimited only up to roster size
     - `slot_id` is in that player's `Player.eligible_slots`; BE (12) is always allowed
     - IR (13) needs it in `eligible_slots` (ESPN lists IR for everyone, so this is permissive); add a
       non-blocking `warnings` entry if the player's `injury_status` is ACTIVE/None
   - Writes `LineupAssignment` rows with `source='manual'` for day `sp`, and for every day up to `through_sp`
     if given (`through_sp` ≥ `sp`, ≤ final period). It replaces those days' rows for my team. Returns the
     same payload as `GET /team/day` for `sp` (so the UI can re-render) plus `days_written`.
   - Doesn't touch other days, other teams, or ESPN. `POST /sync/lineup` remains the only way back to
     ESPN's lineup. Add an endpoint test that a manual edit inside the sync range is overwritten by
     `POST /sync/lineup` with a stubbed client, and one outside it is kept.
4. **`GET /team/free-agents`**: the waiver list.
   - Params: `days` (comma list of scoring periods; default `[effective_period]`), `mode` = `none | any |
     all` (default `none`; "one specific day" is just `any`/`all` with one day), `basis` (as above), `position`
     (PG/SG/SF/PF/C, matched against `eligible_slots`), `status` = `all | FREEAGENT | WAIVERS` (default all),
     `q` (name substring, case-insensitive), `sort` = `fpts | name | games` (default fpts desc, nulls last),
     `limit` (default 100, max 500), `offset`.
   - Pool: players whose `roster_status` is FREEAGENT or WAIVERS **and** who have no `RosterEntry` this
     season. Exclude players with no NBA team (`pro_team_id` 0/None) unless `include_unsigned=true`.
   - Filter: `any` = has a game on at least one of `days`; `all` = has a game on every one of `days`; `none`
     = no filter.
   - Row: `id, name, nba_team, positions, eligible_slots, injury_status, status` (FREEAGENT/WAIVERS), `waiver`
     (`clears_at` UTC, `clears_at_display` like `"clears Thu 10/22"` in Pacific, `clears_at_known`, from
     `waiver_statuses` in one batch, never per-row queries), `games: [{sp, game|null}]` aligned to `days`,
     `games_in_days`, `stats` (`fantasy_ppg` + `games` only, since the list shows one FPTS column; the card
     has the full line).
   - Response also echoes `days` with their dates + matchup ids, `mode`, `basis`, `total` (pre-pagination
     count) and `actuals_available`.
   - Keep it to a fixed handful of queries regardless of pool size (assert this with a query-count test,
     e.g. SQLAlchemy event counter, ≤ ~8 queries for a 300-player pool).
5. **`GET /team/player/{id}?from_sp=`**: the waiver-player card (also usable for my own players).
   - `from_sp` defaults to `effective_period`.
   - `player` (identity + `eligible_slots`, injury, `owner`: `FREEAGENT` / `WAIVERS` / `{team_id, name}`),
     `waiver` (same as the list), and `stats`: `{projected, season, l7, l15, l30}`, each a full StatLine
     or null. **Reuse the `SeasonLine` shape** from `players.py` rather than inventing a new one.
   - `schedule`: the 7 days `from_sp .. from_sp+6` (clamped to final period). Each day: `{sp, date, weekday,
     matchup_id, matchup_label, game|null}`, plus `by_matchup: [{matchup_id, label, start, end, days: [...],
     games}]`, so the UI can mark which games fall in which matchup. E.g. from Sun 10/25: 10/25 is in
     Matchup 1 and 10/26–10/31 in Matchup 2.
   - 404 for an unknown player.
6. **`GET /team/calendar`**: everything the date picker needs in one call: `season, today_period,
   effective_period, final_period, matchups: [{id, label, start, end, first_sp, last_sp}]`, and
   `days: [{sp, date, matchup_id, my_games}]` for the whole season, where `my_games` = how many of my
   rostered players have a game that day (one grouped query). No per-day queries.
7. **Small fix carried over from task 27:** the older `@pytest.mark.live` tests fail because conftest pins
   `ESPN_LEAGUE_ID=999999` for the whole session. Make live tests build their client from a fresh
   `Settings()` read from `.env` (as task 27's new live test does), e.g. via a `live_settings` fixture,
   without loosening the pins for offline tests. Then `make test-live` passes with Misha's `.env`.

## Testing (offline, deterministic, in-memory SQLite, fixtures, no network)
- Pure: `format_game` (home/away, TBD, PDT vs PST, UTC-date-vs-Pacific-date edge), slot-grid builder
  (empty slots, bench growth, overflow guard), day-filter modes (`none`/`any`/`all` with one and three days,
  an empty `days` list → 422).
- `GET /team/day`: default day before tip-off, the slot order and empty slots, a player with and without a
  game, all five bases (actual bases with `actuals_available` false and with seeded actual rows),
  `lineup_source` for each of the four cases, acquisitions with a seeded `{"1": 2}` and `{1: 2}` (and `{}` →
  0), sp out of range → 422.
- `PUT /team/lineup`: happy path; each validation error (missing player, extra player, slot over capacity,
  ineligible slot); IR warning; `through_sp`; replaces only the target days; sync overwrite vs keep
  (stubbed client).
- `GET /team/free-agents`: pool excludes rostered + unsigned players; each mode; position/status/q filters;
  sort + nulls last; pagination + `total`; waiver display string; the query-count guard.
- `GET /team/player/{id}`: full stats with some bases null; the 7-day window crossing a matchup boundary
  (start Sun 10/25: 1 day in Matchup 1, 6 in Matchup 2) and the final-period clamp; 404.
- `GET /team/calendar`: 160 days, 21 matchups, `my_games` counts against a seeded schedule.
- Existing suites untouched and green.

## Constraints
- Reuse task 27's helpers (`resolve_lineup`, calendar functions, `waiver_statuses`, `my_team`) and players.py's
  stat-line shape. Don't duplicate them. Put new logic in `app/team/`; keep endpoints thin.
- Times on the wire are ISO UTC. Every "display" string is Pacific and computed server-side by one helper.
- No schema changes expected. If you find you need one, stop and say why before adding a migration.
- No new dependencies. Don't touch draft/board/consensus/master/market/importer behaviour.

## Acceptance criteria
1. `make test`, `make lint`, frontend `npm test` + `npm run build` green. New tests in place.
2. Against Misha's real DB (after `make sync` + `make sync-lineup`): `GET /team/day?sp=2` shows Emzep's
   slot grid with real games (LAL players show `vs GSW 7:00 PM` on Wed 10/21). `GET /team/free-agents?
   days=1,2,3&mode=all` returns only players whose teams play all three days, and `mode=any` returns more.
   `GET /team/player/{id}?from_sp=6` groups days across Matchups 1 and 2.
3. A `PUT /team/lineup/2` swapping two players round-trips through `GET /team/day?sp=2` (`lineup_source:
   manual`), and `POST /sync/lineup` restores ESPN's lineup.
4. `make test-live` passes with Misha's `.env`.

## Report back with
- Branch, file tree, and confirmation it's STAGED, not committed.
- Real responses (trimmed): `GET /team/day?sp=2`, `GET /team/free-agents?days=1,2,3&mode=all&limit=10`
  (+ the `total` for `mode=any` vs `all` vs `none`), `GET /team/player/<a free agent>?from_sp=6`,
  `GET /team/calendar` (first 3 days + matchups 1–2).
- The lineup round-trip from acceptance 3.
- The free-agent endpoint's measured query count.
- Decisions/deviations, and test counts before → after.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
