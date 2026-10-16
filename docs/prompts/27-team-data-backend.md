# Task 27 — Team data backend: NBA schedule, matchup calendar, rosters + lineups, transactions, waiver clears, current-season actuals

Context: the draft is over and the season tips off **Tue 10/20/2026**. This is task 1 of 5 for the new
in-season **Team** tab, which has a day-by-day roster in ESPN-style slots, a waiver list filtered by who
plays on chosen days, and later a waiver plan. This task is **data + sync only**. No new pages and no
user-facing endpoints except one status endpoint. Tasks 28–31 build the Team API, Team UI, plan backend
and plan UI on top of it, so get the shapes right.

Read first:
- `backend/app/espn/client.py` (`ESPNClient.fetch_view`, `fetch_player_pool_pages`, `_normalise_swid`)
- `backend/app/espn/sync.py` (`sync_league`, `SyncSummary`, `_PLAYER_FIELDS`, `sync_projections`, `sync_actuals`, `ACTUAL_SEASON_KIND`)
- `backend/app/espn/statsplits.py` (split ids, `_select_split`, `select_actual_split`, `COUNTING_STAT_IDS`)
- `backend/app/espn/players.py` (`parse_player_entry`, `_positions`, `POSITION_MAP`/`PRO_TEAM_MAP` from `espn_api.basketball.constant`)
- `backend/app/scoring/settings.py` (`roster_slots` from `lineupSlotCounts`)
- `backend/app/db/models/` (esp. `player.py`, `projection.py`, `league_settings.py`, `__init__.py`), `backend/app/config.py`
- `backend/app/api/sync.py`, `backend/app/api/players.py` (`actual_season_ppg`, `_newest_by_player`: read paths that filter by `kind`)
- `backend/tests/conftest.py`, `backend/tests/test_sync.py`, `backend/tests/test_espn_statsplits.py`, `backend/tests/test_espn_live.py`, the `fixtures` target in the root `Makefile`

Branch: `main` must be clean and current. If there is staged or uncommitted work, STOP and tell me. Otherwise
create `team-data` off `main`.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Facts verified against the live league (2026-10-15). Build on these, don't re-guess them
League 131739, season 2027. 9 teams. **Our team is id 8 "Emzep" (abbrev EMZ)**. It has two owners (Misha and
co-manager); `mTeam.teams[].owners` is a list of SWIDs in braces.

- **NBA schedule**: `GET {season_url}?view=proTeamSchedules_wl` (season-level, not league-level) →
  `settings.proTeams[]` with `{id, abbrev, proGamesByScoringPeriod: {"<scoringPeriodId>": [game]}}`, where
  game = `{id, date (epoch ms UTC), homeProTeamId, awayProTeamId, scoringPeriodId, startTimeTBD,
  statsOfficial, validForLocking}`. proTeam id 0 is a placeholder. Skip it. Every game appears under
  both teams, so dedupe by game id.
- **Scoring period = one calendar day, in US/Eastern.** Period 1 = Tue 10/20/2026, period N = 10/20 + (N−1)
  days, with no exceptions across all 156 periods that have games. `mStatus.firstScoringPeriod`=1,
  `finalScoringPeriod`=160 (= Sun 3/28/2027, the end of the fantasy playoffs).
- **Matchup date ranges are NOT in the API.** `mSettings.scheduleSettings.matchupPeriods` maps matchup →
  internal week ids (`{"1":[1],…,"21":[21,22]}`) but has no dates, and some "weeks" are two weeks long.
  The ranges below come from ESPN's league schedule page and are the source of truth for 2027:
  ```
  1 Oct 20–25 | 2 Oct 26–Nov 1 | 3 Nov 2–8 | 4 Nov 9–15 | 5 Nov 16–22 | 6 Nov 23–29 | 7 Nov 30–Dec 6
  8 Dec 7–13 | 9 Dec 14–20 | 10 Dec 21–27 | 11 Dec 28–Jan 3 | 12 Jan 4–10 | 13 Jan 11–17 | 14 Jan 18–24
  15 Jan 25–31 | 16 Feb 1–7 | 17 Feb 8–14 | 18 Feb 15–28 (All-Star, 2 wks)
  19 Playoff R1 Mar 1–7 | 20 Playoff R2 Mar 8–14 | 21 Playoff R3 Mar 15–28 (2 wks)
  ```
- **Rosters + daily lineups**: `GET {league_url}?view=mRoster&scoringPeriodId=N` (add `forTeamId=8` for one
  team) → `teams[].roster.entries[]` with `{playerId, lineupSlotId, acquisitionDate (ms), acquisitionType,
  injuryStatus, status, playerPoolEntry.player.eligibleSlots}`. `lineupSlotId` uses POSITION_MAP ids. Our
  league uses PG 0, SG 1, SF 2, PF 3, C 4, UT 11, BE 12, IR 13. Future days can differ from today: ESPN
  stores future lineup changes (`FUTURE_ROSTER` transactions).
- **Team acquisition counter**: `mTeam.teams[].transactionCounter` = `{acquisitions, drops,
  matchupAcquisitionTotals: {}, moveToActive, moveToIR, …}`. `matchupAcquisitionTotals` is ESPN's own
  per-matchup acquisition count. It's empty pre-season, so the key format isn't confirmed. Store it raw.
- **League acquisition settings** (`mSettings.settings.acquisitionSettings`): `matchupAcquisitionLimit: 7`,
  `waiverHours: 24`, `acquisitionType: "WAIVERS_TRADITIONAL"`. It also says `waiverProcessDays: ["SUNDAY"]`,
  `waiverProcessHour: 11`, but real waiver processing is **nightly**: `status.waiverLastExecutionDate` and
  executed WAIVER transactions land at roughly 00:00–01:00 Pacific every night. Don't model a weekly process day.
- **Transactions**: `GET {league_url}?view=mTransactions2` (no scoringPeriodId) returns the whole season,
  ~700 rows incl. DRAFT. Row = `{id (uuid), type (DRAFT|FREEAGENT|WAIVER|ROSTER|FUTURE_ROSTER|TRADE…),
  status (EXECUTED|PENDING|FAILED_*…), teamId, scoringPeriodId, proposedDate, processDate, items: [{type
  (ADD|DROP|LINEUP|…), playerId, fromTeamId, toTeamId, fromLineupSlotId, toLineupSlotId}]}`.
- **Waivers**: a player's `kona_player_info` entry has `status` FREEAGENT | WAIVERS | ONTEAM. **ESPN gives no
  "clears at" time**: no such field on the player or entry (no player was on waivers when checked, so a
  WAIVERS entry itself hasn't been seen; see scope 6 for the probe). Misha's league rule: a player
  **held more than 24h** and then dropped goes on waivers until the **first midnight (Pacific) after drop +
  24h**. One held under 24h goes straight to free agency. Real data agrees: a drop at 2026-10-13T22:54:39Z
  had cleared by the 2026-10-15T08:03Z nightly run (rule says 10/15 00:00 PT = 07:00Z).
- **Current-season actual splits are already in our player-pool payload.** Each player's `stats[]` carries
  ids `002027` / `012027` / `022027` / `032027` (statSourceId 0, statSplitTypeId 0/1/2/3 = season / last 7 /
  15 / 30 days, seasonId 2027) beside `002026` (last season) and `102027` (projection). They're empty
  until games are played.

## Scope

1. **Schedule.** New model `NbaGame` (PK = ESPN game id; `season`, `scoring_period_id`, `starts_at` tz-aware
   UTC, `home_pro_team_id`, `away_pro_team_id`, `start_time_tbd`). `ESPNClient.fetch_pro_schedule()` (season
   url, view above). `sync_schedule(db, payload, season, summary)` upserts deduped games for the season
   and **deletes that season's games no longer present** (postponed/rescheduled games move or vanish).
   Validate that each game's US/Eastern date equals `period_date(scoring_period_id)` (scope 2) and count
   mismatches into the summary. Don't fail the sync over them.
2. **Calendar**: new pure module `backend/app/team/calendar.py` (new package `app/team/`), no DB imports.
   - `period_date(season, sp) -> date` and `period_for(season, d) -> int | None` from a per-season day-one
     constant (`SEASON_DAY_ONE = {2027: date(2026, 10, 20)}`) + `FINAL_PERIOD = {2027: 160}`.
   - `MATCHUPS = {2027: [...]}`, the 21 ranges above as `(matchup_id, start_date, end_date, label)` with
     labels "Matchup 1" … "Matchup 18", "Playoff Round 1/2/3". Derive `matchup_for_period(season, sp)` and
     `periods_in_matchup(season, m) -> range`.
   - `today_period(season, now)`: "today" is the **America/Los_Angeles** date of `now`. NBA starts are at most
     ~10:30pm ET = 7:30pm PT, so a game's ET date equals its PT date, and a Pacific user's "today" is right.
   - An unknown season raises a clear error. Don't guess weeks.
3. **Fantasy teams + "my team".** Model `FantasyTeam` (PK `(season, team_id)`; `name`, `abbrev`, `owners`
   JSON, `is_mine` bool, `acquisitions_total`, `matchup_acquisition_totals` JSON raw, `synced_at`). Setting
   `espn_my_team_id: int | None = None` (env `ESPN_MY_TEAM_ID`, document it in `.env.example`). Resolution:
   the override if set, else the team whose `owners` contains the normalised SWID (reuse
   `_normalise_swid`; compare case-insensitively). Exactly one `is_mine` per season. If none match, the sync
   still completes and the summary carries a warning. Helper `my_team(db, season)`.
4. **Rosters.** Model `RosterEntry` (`season`, `team_id`, `player_id` FK, `lineup_slot_id` as of sync,
   `acquisition_date` UTC, `acquisition_type`; unique `(season, player_id)`). Sync pulls `mRoster` for **all
   teams** at the current scoring period in one request and replaces the season's rows (delete-then-insert
   in the sync transaction is fine). Add `Player.eligible_slots` (JSON list of int slot ids, from
   `player.eligibleSlots`) to the player parse + `_PLAYER_FIELDS`. The Team UI needs raw slot ids to keep
   slot moves position-legal. `positions` stays as is.
5. **Lineups, separate from roster membership.** Model `LineupAssignment` (`season`, `team_id`,
   `scoring_period_id`, `player_id`, `lineup_slot_id`, `source` 'espn'|'manual', `updated_at`; unique
   `(season, team_id, scoring_period_id, player_id)`).
   - `pull_lineups(db, client, team_id, first_sp, last_sp)` fetches `mRoster&forTeamId&scoringPeriodId` per
     day and **replaces** all rows for those days with `source='espn'` (this is the future "Sync lineup from
     ESPN" button; it deliberately overwrites manual edits). Default range: today's period through the
     end of the **next** matchup. Clamp to `[1, FINAL_PERIOD]`. Make target `make sync-lineup`.
   - **The general `sync_league` must NOT write `LineupAssignment`.** Misha's rule: edits on the ESPN app don't
     silently overwrite this site's lineup. Only the explicit pull does. Add a test for this.
   - Pure resolver `resolve_lineup(stored_rows_by_day, roster_player_ids, sp) -> {player_id: slot_id}`: use
     day `sp`'s rows if any, else carry forward the nearest earlier day with rows, else the RosterEntry
     `lineup_slot_id`. Then reconcile with the current roster: drop players no longer rostered, and put
     rostered players with no assignment on BE (12). Pure + unit-tested. Task 28 serves it.
6. **Transactions + waiver clears.**
   - Model `EspnTransaction` (PK uuid `id`; `season`, `type`, `status`, `team_id`, `scoring_period_id`,
     `proposed_at`, `processed_at`, `items` JSON). Sync upserts every non-DRAFT row from `mTransactions2`
     (statuses change from PENDING to EXECUTED, so upsert, don't insert-once).
   - Pure `waiver_clears_at(dropped_at, acquired_at, waiver_hours, tz) -> datetime | None`: None if held
     ≤ waiver_hours (goes straight to FA), else the first local midnight strictly after `dropped_at +
     waiver_hours`, returned in UTC. Setting `waiver_clear_tz = "America/Los_Angeles"`. Read `waiver_hours`
     from stored acquisition settings (scope 7). Don't hardcode 24.
   - `waiver_status(db, season, player_id)` → `{on_waivers: bool, clears_at: datetime | None,
     clears_at_known: bool}`. **ESPN's `Player.roster_status == 'WAIVERS'` is authoritative for "on waivers
     now"**. `clears_at` is computed from his most recent EXECUTED DROP item, with `acquired_at` = the
     processed time of his most recent EXECUTED ADD before that drop (else his RosterEntry acquisition date
     if we have it; if neither exists, assume held >24h). A WAIVERS player with no drop transaction found →
     `clears_at=None, clears_at_known=False`. Task 30 reuses `waiver_clears_at` for *planned* drops, so keep
     it pure.
   - **Probe:** when a sync sees any entry with `status == 'WAIVERS'`, put the sorted top-level keys of that
     entry and of its `player` object into the summary (`waiver_entry_keys`) and log them, so we can spot a
     real clear-time field the first time someone is on waivers. If you find one in a live run, report it.
     Don't build on it yet.
7. **League acquisition settings.** Add `LeagueSettings.acquisition_settings` JSON (the raw
   `acquisitionSettings` object) and parse `matchupAcquisitionLimit` / `waiverHours` through
   `parse_league_settings` onto `LeagueScoringSettings` (or a sibling dataclass, your call). Don't change
   scoring behaviour.
8. **Current-season actuals (for the Team tab's Season / L7 / L15 / L30 toggle).** Store four new
   `Projection.kind`s, `actual_current`, `actual_last_7`, `actual_last_15`, `actual_last_30`, with `season`
   = the synced season (2027), priced under OUR scoring through the existing `sync_projections` writer
   (same pattern as `sync_actuals`, which uses a scratch summary).
   - Select the split with **exact** `seasonId == season` and `statSplitTypeId` 0/1/2/3, source 0. **No
     fallback to an older season** (the existing `_select_split` falls back to the newest, which here would
     mislabel last season as this season). Skip splits with no counting stats or GP 0.
   - **Landmine, stale windows:** a player who had an L7 row yesterday and hasn't played in 7 days today
     has no L7 split. The writer only upserts, so his old row would live forever. For these four kinds,
     delete rows for `(source, kind, season)` whose player isn't in this sync's parsed set.
     `actual_season` (last season) and `projected_season` behaviour must stay byte-for-byte unchanged.
   - Confirm and keep the safety property: board/consensus/master/`actual_season_ppg` read paths filter by
     `kind`, so the new kinds can't leak into rankings. Add a test that `/players/board` and the consensus
     board are identical with and without `actual_*` rows present.
   - Summary: one `current_actuals_*` count per kind (seen/created/updated/unchanged/removed), plus
     `actuals_available: bool` (any `actual_current` row exists).
9. **Wiring.** `sync_league` order: settings (+ acquisition settings) → player pool (+ eligible slots) →
   projections → last-season actuals → **current-season actuals** → ADP → **schedule → teams (+ my team)
   → rosters → transactions**. Still one commit at the end. Still no writes if credentials fail. Extend
   `SyncSummary` / `SyncLeagueResponse` with the new counts (`games_*`, `schedule_date_mismatches`,
   `teams_seen`, `my_team_id`, `my_team_warning`, `roster_entries`, `transactions_*`, `waiver_entry_keys`,
   the scope-8 counts). Add a `POST /sync/lineup` endpoint that calls `pull_lineups` for my team with
   optional `first_sp`/`last_sp` and returns `{team_id, first_sp, last_sp, days, assignments}`. Add
   `GET /team/status` returning `{season, my_team: {id, name, abbrev} | null, today_period, today_date,
   current_matchup: {id, label, start, end}, games_synced, roster_size, lineup_days_stored,
   actuals_available, acquisition_limit}` (router `app/api/team.py`, mounted like the others).
   `make sync` stays the single entry point for everything except lineups.

## Migrations & wiring
- One Alembic revision for: `nba_game`, `fantasy_team`, `roster_entry`, `lineup_assignment`, `espn_transaction`,
  `player.eligible_slots`, `league_settings.acquisition_settings`. Use `batch_alter_table` for the two ALTERs
  (tests/CI run migrations on SQLite). Autogenerate must be clean afterwards (`make revision` produces an
  empty diff). Register every new model in `app/db/models/__init__.py`.
- New settings live in `Settings` and are pinned in `tests/conftest.py`, as the draft settings were.

## Testing (offline, deterministic, no network, no secrets)
- **Fixtures**: extend `make fixtures` to also record sanitized `espn_pro_schedule.json` (trim to ~6 teams,
  a few periods incl. a postponed/TBD game if one exists, else hand-add one), `espn_mteam.json`,
  `espn_mroster.json` (current period, all teams) plus one `espn_mroster_day_<N>.json` for a future day that
  differs (hand-edit one slot if ESPN has none), and `espn_transactions.json` (trimmed; include an ADD→DROP
  pair, a PENDING row, a FUTURE_ROSTER row, a FAILED row). **Sanitize owners**: replace every SWID with fake
  GUIDs, and make team 8's list include the fake SWID pinned in conftest. Also update
  `espn_msettings.json` so it carries `acquisitionSettings` + `matchupPeriods` if it doesn't. Hand-craft
  a small player-pool fixture variant with non-empty `002027`/`012027` splits and one WAIVERS entry.
- **Calendar**: period 1 = 2026-10-20, 160 = 2027-03-28; matchups contiguous with no gaps/overlaps; covering
  exactly 1..160; 21 matchups; `matchup_for_period` at every boundary (6→1, 7→2, 119→18 Feb 15, 132→18 Feb 28,
  133→19 Mar 1, 160→21); `today_period` at 11:30pm PT on 10/20 is still period 1; unknown season raises.
- **Schedule**: dedupe across home/away; re-sync idempotent; moved game updates; vanished game deleted; ET
  date check counts a deliberately wrong fixture row.
- **My team**: SWID match (with and without braces / case), override wins, no match → warning, not crash.
- **Lineups**: `pull_lineups` replaces only its days and resets `source` to espn, overwriting a manual row in
  range and leaving one out of range alone. `sync_league` writes zero `LineupAssignment` rows. `resolve_lineup`
  covers exact day, carry-forward, roster-entry fallback, dropped player removed, new player → BE.
- **Waivers**: `waiver_clears_at` on the real data point (drop 2026-10-13T22:54:39Z, held >24h → 2026-10-15
  07:00Z), held < 24h → None, a drop at 23:59 PT, a drop exactly 24h after add (≤ means FA), DST boundaries
  (a drop on 2026-10-31 PT so +24h crosses the Nov 1 fall-back, and one in March before Mar 14 2027
  spring-forward). `waiver_status` with no drop tx → `clears_at_known=False`. Probe keys land in the summary.
- **Actuals**: exact-season selection (a 2026-only actual yields no `actual_current`); all four kinds stored
  and scored under our engine; stale-window removal; GP 0 skipped; last-season actual + projections
  unchanged (compare row-for-row with the pre-task behaviour on the existing fixture); the board-isolation
  test from scope 8.
- **API**: `POST /sync/league` response has the new fields; `POST /sync/lineup` with a stubbed client;
  `GET /team/status` before/after sync. Migration test: upgrade/downgrade round-trip on SQLite.
- Add one `@pytest.mark.live` smoke test that pulls schedule + mTeam + mRoster and asserts our team resolves
  to id 8 and period 1's games are dated 2026-10-20 ET.

## Constraints
- Reuse `ESPNClient.fetch_view`, `sync_projections`, `ScoringEngine`, the existing parse helpers and summary
  pattern. Don't add a second HTTP path or a second projection writer.
- No new heavy deps (`zoneinfo` is stdlib). Times are stored UTC and tz-aware; Pacific/Eastern only in
  pure helpers.
- Don't touch draft, board, consensus, master, market, or importer behaviour.
- Postgres in dev (port 5433), SQLite in tests: keep everything portable.

## Acceptance criteria
1. `make test`, `make lint`, `cd frontend && npm test` and `npm run build` all green. CI-equivalent runs
   locally. New tests in place.
2. A real `make sync` (Misha's .env) completes and reports: games synced (~1,230 for the season), 0 or a
   handful of date mismatches, `teams_seen = 9`, `my_team_id = 8`, roster entries for 9 teams,
   transactions stored, `current_actuals_*` = 0 pre-season (or populated if games have been played),
   `actuals_available` accordingly.
3. `make sync-lineup` stores lineups for today through the end of next matchup for team 8. Running
   `make sync` afterwards leaves them untouched.
4. `GET /team/status` shows team 8 "Emzep", today's period/date, the current matchup with its date range,
   and `acquisition_limit = 7`.
5. `make revision m=check` produces an empty migration (delete it afterwards). `/players/board` output is
   unchanged versus `main` for the same DB.

## Report back with
- Branch, file tree of changes, and confirmation that it's STAGED, not committed.
- The real `make sync` summary (new fields), plus `GET /team/status` output.
- Three sample `NbaGame` rows for 10/20 and 10/21, with `starts_at` and the ET/PT local times.
- The team-8 lineup for today and one future day from `make sync-lineup`, as `slot → player name`.
- `waiver_entry_keys`, if any WAIVERS player was seen, and whether any field looks like a clear time.
- The exact `matchupAcquisitionTotals` content seen (likely `{}`).
- Any schedule date mismatches, listed.
- Decisions/deviations, and test counts before → after (backend + frontend).

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
