# Task 2 — ESPN integration foundation: auth + custom scoring settings + canonical player identity

Context: Builds on the scaffold. Assumes `scaffold` has been merged into `main` (or you're
branching off the scaffold state). Read docs/PLAN.md, docs/FEATURE_SPEC.md, and the existing
backend/app structure first. This is a private ESPN fantasy basketball dynasty tool (H2H points,
CUSTOM scoring). The ESPN league is fully set up with custom scoring configured. Real ESPN
credentials are in .env (ESPN_S2, SWID, ESPN_LEAGUE_ID, ESPN_SEASON). Create a new branch
`espn-foundation` off `main` and work there.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope (three parts):

1) ESPN client + auth (app/espn/):
- Add `espn-api` as a dependency (uv add espn-api).
- Build a client wrapper that constructs an espn_api.basketball.League from Settings
  (espn_s2, swid, espn_league_id, espn_season). Handle the private-league cookie auth.
- Add a thin raw-request helper (httpx) for the ESPN v3 `mSettings` view, since the library may
  not expose the full per-stat scoring coefficients. Base:
  https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{id}?view=mSettings
  Send the espn_s2/SWID cookies. Parse scoringSettings.scoringItems.
- Credentials are required only for live sync, not for app boot: validate them at sync time and
  raise a clear error if missing; the app and existing /health must still start without them.

2) Custom scoring settings (app/scoring/ + models):
- Model the league scoring: a LeagueSettings row (league_id, season, name, scoring_type, roster
  slots if easily available) and ScoringRule rows (stat_id, stat_name, points) — one per scored
  stat. Map ESPN statId -> stat name using espn-api's STATS_MAP (or an equivalent local map).
- Implement score_stat_line(stat_line: dict[stat_name|stat_id, value]) -> float that applies the
  stored ScoringRules to produce fantasy points under OUR custom formula.
- Persist the pulled scoring settings via the sync command below.
- Tests: given a fixture of our league's scoringItems, assert the rules parse correctly and that
  score_stat_line reproduces expected points for a sample stat line.

3) Canonical player identity (app/db/models + app/espn):
- Player model keyed by espn_player_id (PK or unique): full_name, nba_team, positions (list/JSON),
  status/injury, and any age/birthdate ESPN provides (nullable — authoritative age comes later).
- PlayerAlias model (player_id FK, source, source_name, source_id) — scaffold it for future
  external-source matching; can be empty for now.
- A sync routine that pulls the full player universe (rostered + free agents) and upserts Players
  idempotently (re-running sync must not duplicate).

Sync entrypoint:
- POST /sync/league that runs: pull scoring settings -> upsert LeagueSettings + ScoringRules;
  pull players -> upsert Players. Returns a summary (counts, scoring rule count).
- A `make sync` target that calls it (or a small CLI). Idempotent.

Migrations & wiring:
- Remove the placeholder HealthCheck example model now that real models exist (keep GET /health and
  GET /health/db working — /health/db can just SELECT 1, no model needed).
- REMEMBER: app/db/models/__init__.py must import every new model module, or Alembic autogenerate
  won't see them. Add Player, PlayerAlias, LeagueSettings, ScoringRule there.
- Generate one Alembic migration for the new schema (autogenerate, then eyeball it).

Testing strategy:
- Deterministic unit tests run against RECORDED FIXTURES (sanitized sample JSON of the ESPN
  players + mSettings responses committed under tests/fixtures/), so CI needs no live cookies.
- Provide a small script/target to record fresh fixtures from a live pull (e.g.
  scripts/record_fixtures.py), and SANITIZE it: strip any personal/account identifiers, keep only
  player + scoring data.
- Any test that hits the live ESPN API must be marked and skipped when cookies aren't set.

Constraints:
- Only the dependencies needed (espn-api; httpx is already present). No unrelated additions.
- Don't hardcode secrets or IDs; everything from Settings/env.
- macOS; Postgres runs on host port 5433 (see .env); don't change the port.

Acceptance criteria (verify before reporting done):
1. `make db-up && make migrate` applies the new migration cleanly.
2. With real .env creds, `make sync` (or POST /sync/league) pulls and stores the scoring rules and
   the player pool; re-running it does not duplicate rows.
3. score_stat_line reproduces correct fantasy points for a sample stat line under the pulled rules.
4. `make test` (fixture-based, no live creds needed) and `make lint` pass.
5. /health and /health/db still work.

Report back with:
- Branch name and file tree of changes; confirm changes are STAGED, NOT committed.
- The parsed scoring rules (stat_name -> points) exactly as pulled, so we can eyeball that the
  custom formula is correct.
- Player pool count synced, and 3-5 sample player rows (name, team, positions, age if present).
- A worked example of score_stat_line on a sample stat line (inputs -> points).
- Anything you decided/deviated on, and follow-ups for task 3 (projections ingestion + import
  pipeline).
