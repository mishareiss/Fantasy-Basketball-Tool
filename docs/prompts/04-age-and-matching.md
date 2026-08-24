# Task 4 — Player ages via nba_api + the reusable PlayerAlias fuzzy matcher (unblock dynasty value)

Context: Builds on task 3 (branch `espn-projections`). Assumes task 3 is merged into `main`
(or branch off `espn-projections`). Read docs/PLAN.md, docs/FEATURE_SPEC.md, docs/prompts/
02-espn-foundation.md, docs/prompts/03-espn-projections.md, and the existing app/espn +
app/db/models + app/scoring code first. Private ESPN fantasy basketball dynasty tool, H2H
points, CUSTOM scoring. The dynasty age curve is the next big lever and it is BLOCKED: ESPN
publishes no birthdate/age anywhere, so `Player.birthdate` / `Player.age` exist but are always
null. This task lands an authoritative age source (nba_api's CommonPlayerInfo) and the reusable
PlayerAlias fuzzy matcher that resolves any external source's player names to our canonical,
ESPN-keyed `Player` rows. Build the matcher as SHARED INFRASTRUCTURE, not an nba-only helper:
task 5's generic CSV/paste importer (adp | projection | ranking | market_line) will reuse it.
Create a new branch `player-age-matching`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

Scope:

1) Name normalization + fuzzy matcher (new package app/matching/, source-agnostic):
- `normalize_name(name) -> str`: NFKD-strip accents (Dončić -> doncic), lowercase, replace
  punctuation/periods/apostrophes/hyphens with spaces (De'Aaron -> "de aaron", O.G. -> "o g",
  Karl-Anthony -> "karl anthony"), drop generational suffixes (Jr, Sr, II, III, IV, V), collapse
  whitespace. Pure and unit-tested.
- A `PlayerMatcher` built from our canonical Players (espn_player_id, full_name, first/last,
  nba_team, positions). `match(source_name, *, team=None, positions=None, source=None,
  source_id=None) -> MatchResult{ player_id | None, confidence: float, method:
  'alias'|'exact'|'normalized'|'fuzzy'|'ambiguous'|'unmatched', candidates: [...] }`.
  Resolve order: an existing PlayerAlias for (source, source_name) or (source, source_id) ->
  'alias' (confidence 1.0). Then exact normalized-name hit. If several canonical players share a
  normalized name, disambiguate by team (and/or position); if still >1, method='ambiguous',
  player_id=None. Then fuzzy over normalized names, accepted only above a threshold (default
  ~0.88) AND unambiguous; otherwise 'unmatched'.
- The matcher NEVER writes. It returns results; callers decide whether to persist an alias.

2) PlayerAlias match metadata + one migration:
- Add nullable `confidence` (Float) and `match_method` (String) to `PlayerAlias`, so a recorded
  match carries how it was made (for review, and so a shaky auto-match is spottable). Keep the
  existing (source, source_name) unique constraint. Generate ONE Alembic migration; eyeball it;
  verify from-scratch apply. (Player already has birthdate/age — no Player schema change.)

3) nba_api age source (app/ages/ or app/nba/):
- Add the dep: `uv add nba_api`.
- Build the nba player list from the OFFLINE static roster
  (`nba_api.stats.static.players.get_players()` — bundled, no network, no rate limit): id,
  full_name, first/last, is_active. Match each to our canonical Players via the matcher (the
  static list has no team, so rely on normalized-name uniqueness + fuzzy; prefer is_active
  players on ties). Record `PlayerAlias(source='nba_api', source_name=<nba full_name>,
  source_id=str(nba_id), confidence, match_method)` for each confident match. Idempotent: an
  existing alias short-circuits.
- Fetch birthdates from `CommonPlayerInfo(player_id=nba_id)` — one network call per player, so:
  fetch ONLY for players that have an nba alias but no birthdate yet (incremental + resumable),
  a polite delay between calls, a real `timeout` plus a couple of retries with backoff, and a
  `--refresh` flag to force. Parse the BIRTHDATE column (ISO) and set `Player.birthdate`.
- Compute `Player.age` from birthdate at a deterministic AGE_AS_OF date (add `AGE_AS_OF` to
  Settings, default = start of the ESPN season, e.g. Oct 1 of the season's starting year), NOT
  `date.today()`, so ages are reproducible and correct for draft day. Birthdate is the source of
  truth; age is derived. Document the choice.

4) CRITICAL — stop the ESPN sync clobbering ages:
- `app/espn/sync.py` `_PLAYER_FIELDS` currently includes `birthdate` and `age`, and the ESPN
  parser always yields None for both. Once nba_api populates them, the next `make sync` would
  overwrite them back to None. REMOVE `birthdate` and `age` from the ESPN sync's owned player
  fields (they are never sourced from ESPN). Add a test asserting a full league re-sync leaves a
  Player's nba-populated birthdate/age intact.

5) Wire in + read:
- A CLI `scripts/sync_ages.py` (`make sync-ages`) and/or `POST /sync/ages` that builds the
  matcher, matches nba_api players, records aliases, fetches missing birthdates, recomputes age
  at AGE_AS_OF, and returns/prints a summary: players matched / ambiguous / unmatched, aliases
  created, birthdates fetched, ages set, players still missing an age. Idempotent + resumable.
- A hand-resolve path for the long tail (nicknames, name changes, ambiguous duplicates):
  `POST /players/{espn_player_id}/aliases {source:'nba_api', source_id, source_name}` records the
  alias so the next age sync fetches that player's birthdate; surface the unresolved list
  (`GET /players/unresolved?need=age`, or include the names in the sync summary) as the worklist.
- Extend `GET /players/board` to include `age` (now populated) so value and age sit side by side.

Migrations & wiring:
- One migration only (the two PlayerAlias columns). Player already has birthdate/age;
  app/db/models/__init__.py already imports PlayerAlias.

Testing & fixtures:
- Matcher unit tests: normalization (accents Dončić, suffix Jaren Jackson Jr., apostrophe
  De'Aaron Fox, periods O.G. Anunoby, hyphen Karl-Anthony Towns), the exact/normalized/fuzzy
  tiers, an ambiguous same-name pair resolved by team tiebreak, and threshold rejection.
- Offline nba fixtures: a `scripts/record_nba_fixtures.py` (mirroring record_fixtures.py) that
  snapshots a sanitized subset of the static player list + the CommonPlayerInfo birthdate
  responses for the fixture players. Tests parse birthdate, compute age at a FIXED AGE_AS_OF, and
  assert Player.birthdate/age are set; plus the re-sync-preserves-age test from (4), the
  manual-alias path, and the board showing age. All offline by default.
- Mark any test that actually calls nba.com live with a deselected marker (extend `live` to mean
  "hits a real external API", or add an `nbaapi` marker added to `addopts` deselection); it must
  self-skip cleanly if offline / nba_api not installed. `make test` stays green offline.

Constraints:
- Fuzzy matching: `rapidfuzz` (uv add rapidfuzz) is fine and fast; stdlib difflib is acceptable
  if you'd rather not add a dep. Pick one, keep it swappable behind the matcher.
- nba_api hits nba.com stats endpoints: undocumented rate limits, sometimes blocks datacenter
  IPs (Misha runs this locally on his Mac, so fine) — always pass a timeout, back off on failure,
  never hammer, and snapshot for tests.
- Keep the SQLite-testable pattern: generic sa types, hand-written upserts (no PG-only
  ON CONFLICT), matching runs in Python.
- Don't store age without recording what date it's as of. Don't hardcode secrets/IDs. macOS;
  Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `make db-up && make migrate` applies the new migration cleanly (and from-scratch).
2. `make sync-ages` (or POST /sync/ages) matches nba_api players, records aliases, fetches
   birthdates, sets Player.birthdate/age; a second run fetches 0 new and creates 0 duplicate
   aliases (incremental + idempotent).
3. A full `make sync` afterward does NOT wipe the populated birthdate/age.
4. `make test` (offline) and `make lint` pass; the live/nbaapi tests pass when run deliberately.
5. `GET /players/board` shows age alongside projected value.

Report back with:
- Branch name + file tree of changes; confirm STAGED, NOT committed.
- Match results: how many of the ~1,095 players matched confidently, how many ambiguous, how many
  unmatched — with a sample of the unmatched names (that's the manual-alias worklist).
- 5-10 sample players with birthdate + age at AGE_AS_OF (a known vet and a known rookie) so we can
  eyeball correctness.
- The normalization rules + fuzzy threshold you chose, and any tricky matches (accents, suffixes,
  duplicate names) and how they resolved.
- Anything you decided/deviated on, and follow-ups for task 5 (generic CSV/paste import pipeline
  reusing this matcher: adp | projection | ranking | market_line -> resolve to Player -> upsert;
  plus whether AdpEntry/Projection need season history for dynasty ADP-trend tracking).

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
