# Task 30 — Waiver plan backend: moves with backup pickups, chained drops, rule warnings, the plan-applied roster, ESPN auto-detect

Context: Tasks 27–29 (merged) give us the schedule, matchup calendar, rosters, per-day lineups, ESPN
transactions, waiver status (`app/team/waivers.py`, incl. the pure `waiver_clears_at`), the Team API
(`app/team/views.py`, `grid.py`, `schemas.py`, `api/team.py`) and the `/team` page. This task is the
**waiver plan backend**. Task 31 is its UI. **Backend only** (plus TS types in `frontend/lib/api.ts` if
convenient; no components).

Misha's agreed spec, the source of truth:
- **One plan**, shared by both co-managers (it lives in the DB, not the browser).
- A **move** = on day D (a scoring period), **drop X, pick up Y**. Every pickup is 1-for-1 with a drop.
- A move's pickup has **ordered backups**: Y1 (primary), Y2, Y3, … "if Y1 isn't available, take Y2". The drop
  side has no backups.
- **Chained drops**: a later move may drop *"whoever move N picked up"* rather than a named player. Until
  move N is settled, that drop reads as *Y1 (or Y2/…)*. Once move N resolves to Y2 (by auto-detect or by
  hand), every move chained to it follows Y2. If a backup of one move is also a pickup in another move,
  that's a conflict warning.
- **Rules warn, never block.** Nothing is ever refused for a rule. Warnings:
  1. **Acquisition limit**: ESPN's used count for the matchup + planned (not-yet-done) pickups in that matchup
     > `matchupAcquisitionLimit` (7). There's a **manual override** of the used count per matchup "for
     unforeseen circumstances".
  2. **Waivers**: the pickup is (or will be) on waivers at the move's time, with when he clears, so it's
     clear when he's off waivers. This covers players ESPN says are on waivers now, and players **I drop
     earlier in my own plan** (held >24h) and re-add later. A player can be added, dropped and re-added
     within a plan; that's allowed, it just warns when the re-add lands inside his waiver period.
  3. **Drop conflicts**: dropping someone not on the plan roster that day (already dropped earlier in the
     plan, or never rostered); picking up someone already on the plan roster; a chained drop pointing at a
     move that's later, skipped, or deleted; two moves picking up the same player with no drop of him in
     between.
  4. **Backup overlap**: a backup of move N is also a pickup/backup in move M.
  5. **Info (not a warning)**: the pickup has no game on the move day; the move day is in the past and the
     move isn't done ("overdue").
- **Auto-detect**: when ESPN shows the move was made, it's marked done and resolved to whichever candidate
  was actually added. Manual mark done / skip / choose the candidate are available as a fallback.
- **The roster's Plan view**: for any day, show my roster *as if the plan had been executed*, including
  players I haven't picked up yet. **A planned pickup takes the dropped player's slot.** I can also move
  players between slots in Plan view (position-legal, same rules as Actual), and that never touches the
  Actual lineup.

Read first:
- `backend/app/team/` (all of it: `calendar.py`, `lineup.py` `resolve_lineup`, `grid.py`, `waivers.py`,
  `views.py`, `schemas.py`, `store.py`), `backend/app/api/team.py`, `backend/app/db/models/team.py`
- `backend/app/espn/sync.py` (`sync_league` order; transactions are synced near the end)
- `backend/app/espn/league.py` (transaction parsing: item types ADD/DROP, statuses, effective time =
  processDate else proposedDate)
- the task 27–28 tests and fixtures (`espn_transactions.json`, conftest pins)
- `docs/prompts/27-team-data-backend.md` § Facts (league rules + ESPN shapes)

Branch: confirm `team-tab` (task 29) is merged to `main` and `main` is clean. If not, STOP and tell me.
Create `waiver-plan` off `main`.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere. Before staging, check for Finder/iCloud duplicate files
(`find . -name "* 2.*" -not -path "*/node_modules/*" -not -path "*/.venv/*"`). Don't stage any; list them in
the report.

## Scope

1. **Models** (one migration, `batch_alter_table`-safe, autogenerate clean afterwards):
   - `PlanMove`: `id`, `season`, `team_id`, `scoring_period_id`, `sequence` (order among same-day moves),
     `drop_player_id` (nullable FK) **xor** `drop_from_move_id` (nullable FK → `PlanMove`, `ondelete`
     RESTRICT), `status` (`planned` | `done` | `skipped`), `resolved_player_id` (nullable: which candidate was
     actually added), `espn_transaction_id` (nullable), `note`, `created_at`, `updated_at`. A DB-level or
     validator-level check that exactly one drop reference is set.
   - `PlanCandidate`: `move_id` (FK, cascade delete), `rank` (1 = primary), `player_id`; unique
     `(move_id, rank)` and `(move_id, player_id)`.
   - `PlanLineupAssignment`: same shape as `LineupAssignment` (season, team, sp, player, slot, updated_at)
     but for Plan view edits only. Separate table, so Actual and Plan can never bleed into each other.
   - `AcquisitionOverride`: `(season, team_id, matchup_id)` PK, `used` int, `updated_at`.
2. **Pure plan engine** in `backend/app/team/plan.py` (no DB, no ESPN; dataclasses in, dataclasses out),
   fully unit-tested. This is the heart of the task:
   - `effective_pickup(move)`: `resolved_player_id` if set, else rank-1 candidate. `pickup_alternatives(move)`:
     the remaining candidates in rank order (empty once resolved).
   - `resolve_drop(move, moves_by_id)`: a named player, or the referenced move's effective pickup, plus that
     move's alternatives so the UI can show "A (or B)". Chains can nest (move 3 drops move 2's pickup,
     move 2 dropped move 1's pickup) and resolve transitively. Detect and warn on cycles; never loop.
   - `simulate(actual_roster_ids, moves, today_sp, through_sp)` → per day `sp`: the plan roster (ids), plus
     for each player whether he's `actual` or `planned` (with move id + alternatives), plus who was
     `planned_drop` that day. Rules: apply only `planned` moves (a `done` move is already in the actual
     roster ESPN gave us, so applying it again would double-count; a `skipped` move is ignored), in
     `(scoring_period_id, sequence, id)` order. A move dated before `today_sp` still applies from today
     (it's overdue, not gone). A drop that can't be applied (player not on the plan roster) still applies
     its pickup and produces a conflict warning (don't silently swallow the move).
   - `slot_substitutions(day)` → `{added_id: dropped_id}` for moves landing on or before that day, so the
     lineup can put the pickup in the dropped player's slot.
   - `warnings(...)`: every rule above, each as `{code, severity: warning|info, move_id?, matchup_id?, sp?,
     message, detail}`. Codes are stable strings (`acq_limit`, `on_waivers`, `drop_not_rostered`,
     `pickup_already_rostered`, `double_pickup`, `bad_chain`, `chain_cycle`, `backup_overlap`, `no_game`,
     `overdue`). Messages are plain English with names and dates, e.g. "Matchup 2: 8 of 7 acquisitions
     (1 used + 7 planned)" or "Jerami Grant is on waivers until Thu 10/22, but the move is Wed 10/21".
   - **Waivers in the plan**: the move's time is the move day at `plan_move_local_time` in Pacific (new
     setting, default `"09:00"`). For a currently-WAIVERS player, compare against his known `clears_at` (or
     warn "clear time unknown"). For a player **I drop in the plan**, compute his clear time with
     `waiver_clears_at(dropped_at=that move's time, acquired_at=when he joined me (RosterEntry
     acquisition_date, or the planned pickup's move time if he was a planned pickup), waiver_hours,
     tz)`. Re-adding him (by me in a later move) before that time → `on_waivers` warning with the clear date.
   - **Acquisitions**: per matchup, `used` = the override if set, else ESPN's
     `FantasyTeam.matchup_acquisition_totals` (reuse task 28's key-tolerant read). `planned` = `planned`-status
     moves in the matchup dated today or later (a done move is already in ESPN's count). Warn when `used +
     planned > limit`.
3. **Plan lineup** (pure, in `plan.py` or `grid.py`): the Plan view lineup for day `sp` = stored
   `PlanLineupAssignment` rows for that day if any (carry forward from an earlier plan-edited day, like
   `resolve_lineup`), else the **Actual** resolved lineup for that day with `slot_substitutions` applied (each
   pickup takes its dropped player's slot; if the dropped player had no slot that day, the pickup goes to
   BE). Then reconcile with the plan roster (gone → removed, new → BE). Reuse `resolve_lineup` and the
   `grid.py` slot-grid builder. Don't fork them.
4. **Auto-detect** (`backend/app/team/plan_detect.py`, called at the end of `sync_league` after
   transactions; idempotent; counts in the summary as `plan_moves_detected` etc.):
   - For each `planned` move of my team, find an EXECUTED FREEAGENT/WAIVER transaction of my team whose
     items ADD one of the move's candidates, with effective time on or after the move's `created_at`
     minus one day. Prefer a transaction that also DROPs the resolved drop player. If several match, take
     the earliest.
   - On match: `status=done`, `resolved_player_id` = the added candidate, `espn_transaction_id` set. If the
     transaction dropped someone other than the planned drop, keep it done and emit an info warning
     ("you dropped Z, not X") until Misha acknowledges it by editing the move. Chained moves automatically
     follow the resolved pickup, because they resolve through `resolve_drop`.
   - A PENDING WAIVER transaction for a candidate → the move shows `claim_pending: true` (no status change).
   - Never un-do a manual `done`/`skipped`. Never touch other teams' data.
5. **API** (extend `api/team.py`; thin endpoints over `app/team/`; all under my team):
   - `GET /team/plan`: every move (with candidates + names, resolved drop "A (or B)", status,
     `claim_pending`, `espn_transaction_id`), all warnings, and `acquisitions: [{matchup_id, label, used,
     used_source: espn|override, planned, limit, over}]` for every matchup from the current one through the
     last matchup any move touches.
   - `POST /team/plan/moves` `{sp, drop_player_id | drop_from_move_id, candidate_ids: [...], note?}`
     (`sequence` defaults to last-of-day). `PATCH /team/plan/moves/{id}` (any of: sp, sequence, drop ref,
     candidate_ids (replaces the ordered list), status, resolved_player_id (must be one of its candidates),
     note). `DELETE /team/plan/moves/{id}`: **409** with the dependent move ids if a chained move points at
     it (the UI offers to repoint those first). Add `?repoint=true`, which turns each dependent's chained
     drop into a named drop of the deleted move's effective pickup, then deletes.
   - Validation that IS enforced (422; these are data-shape rules, not league rules): sp in range; ≥1
     candidate; no duplicate candidates; exactly one drop reference; a referenced move exists and belongs to
     my team; `resolved_player_id` ∈ candidates. Everything about league rules is a warning, never a 422.
   - `PUT /team/plan/acquisition-override` `{matchup_id, used: int | null}` (null clears it).
   - `GET /team/day?view=actual|plan` (default `actual`, existing behaviour byte-for-byte). With `plan`: the
     plan roster + plan lineup in the same slot grid; each player carries `plan: {status: actual|planned,
     move_id?, alternatives?: [players], claim_pending?}`; the response gains `planned_drops: [players]`
     (dropped that day), `plan_warnings` for that day/matchup, and `lineup_source` ∈ `plan_edit` |
     `plan_carried` | `from_actual`. `acquisitions` includes `planned` + `used_source` in both views.
     **Also add `carried_from_sp` / `carried_from_date`** to the day response when the source is
     carried, in both views. Task 29 had to say "an earlier day" for want of it.
   - `PUT /team/plan/lineup/{sp}`: same body/validation as `PUT /team/lineup/{sp}` (reuse the validator),
     validated against the PLAN roster for that day, writing `PlanLineupAssignment`. It never touches
     `LineupAssignment`. `POST /sync/lineup` never touches plan rows.
   - `GET /team/free-agents` rows gain `in_plan: [{move_id, sp, rank}]` (one extra batched query; adjust the
     query-count guard deliberately and say so).
6. **Sync interplay**: when a move becomes `done`, the next `make sync` updates RosterEntry, so the
   simulate() starting roster already contains the pickup. Prove there's no double-count with a test.

## Testing (offline, deterministic; pure engine heavily, endpoints for the wiring)
- **Pure engine**:
  - a single move
  - two moves on one day (sequence)
  - an overdue move
  - **the chain case Misha raised**: move 1 picks up A (backup B), move 2 drops "move 1's pickup". Unresolved,
    move 2 shows A (or B). Resolve move 1 to B and move 2 now drops B. B as a candidate elsewhere →
    `backup_overlap`.
  - a nested chain and a cycle (warns, terminates)
  - a drop of a player already dropped earlier in the plan → `drop_not_rostered`, but the pickup still
    applies
  - a double pickup
  - add → drop → re-add within the waiver period → `on_waivers` with the right clear date (use the real-rule
    example from task 27, and a re-add just after the clear → no warning)
  - a currently-WAIVERS pickup with a known/unknown clear time
  - acquisitions: ESPN count + planned > 7 → warning, override replaces ESPN's count, done moves not
    double-counted
  - `no_game` info using a seeded schedule
- **Plan lineup**: the pickup takes the dropped player's slot; a dropped bench player → pickup on BE; plan
  edits carry forward; reconcile after a move is deleted; Actual lineup unchanged by plan edits (and vice
  versa).
- **Auto-detect**: a matching ADD+DROP → done + resolved; backup added → resolves to the backup and the
  chained move follows; a different drop → done + info; PENDING → `claim_pending`; manual done/skipped never
  overwritten; idempotent second run; another team's transaction ignored.
- **API**: CRUD happy paths; each 422 shape rule; DELETE 409 + `?repoint=true`; `GET /team/day?view=plan`
  (planned players flagged, `planned_drops`, warnings, `carried_from_*`); `view=actual` byte-identical to
  before (snapshot); the plan lineup PUT; the free-agent `in_plan`; the acquisition override.
- Migration round-trip on SQLite. The CI Postgres job must pass too.

## Constraints
- Reuse `waiver_clears_at`, `waiver_statuses`, `resolve_lineup`, the grid builder, the lineup validator, the
  calendar helpers and the key-tolerant acquisition read. No parallel implementations.
- The pure engine has no DB/ESPN imports (test enforces it, as the draft engine does).
- No new dependencies. Don't change Actual-view behaviour, the draft, boards, or the importer.

## Acceptance criteria
1. `make test`, `make lint`, frontend `npm test` + `npm run build` green. CI-equivalent Postgres migration
   passes.
2. On Misha's DB, via curl or `/docs`: create move 1 (sp 2: drop a bench player, pick up A with backup B),
   move 2 (sp 4: drop "move 1's pickup", pick up C). `GET /team/day?sp=3&view=plan` shows A in the dropped
   player's slot flagged planned with alternative B. `GET /team/plan` shows move 2's drop as "A (or B)".
   PATCH move 1 `resolved_player_id=B` and move 2 now drops B.
3. Planning 8 pickups in Matchup 1 produces the `acq_limit` warning. An override of `used` changes the
   math.
4. `GET /team/day?view=actual` is unchanged versus `main`.

## Report back with
- Branch, file tree, and confirmation it's STAGED, not committed. Any `* 2.*` duplicates found (not staged).
- The acceptance-2 responses (trimmed), and the acceptance-3 warning text.
- An auto-detect demo against fixtures (no real ESPN moves needed): before/after move state.
- Decisions/deviations, and backend test counts before → after.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
