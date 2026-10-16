# Task 33 — Separate when a move is MADE from when the pickup PLAYS (move timing + stream window)

Context: The waiver plan (tasks 30–31a, merged) models a move as one day `D`: on `D` the drop leaves and the
pickup arrives. That's not how Misha streams. His example, Matchup 1: Tuesday has a few games and Wednesday
a full slate. **As soon as Tuesday's games tip off**, the players he streamed for Tuesday (their Tuesday game
already counting) are dropped for players who play **Thursday**. The move is MADE Tuesday (it uses a
Tuesday acquisition, and a dropped player's waiver clock starts Tuesday), but the pickup's games are
Thursday. Today he has to date such moves Thursday, which removes the dropped player from Tuesday's plan
roster, counts the clock from the wrong day, and hides what's really going on.

Read first: `backend/app/team/{plan,plan_store,plan_views,plan_detect,waiver_rule,waivers,calendar}.py`,
`backend/app/db/models/plan.py`, `backend/app/team/schemas.py` (`PlanMoveOut.lands_sp` already exists),
`backend/app/api/team.py`, `frontend/components/team/{PlanTab,MoveComposer,PlanRosterExtras,RosterTable}.tsx`,
`frontend/lib/{plan,team,api}.ts`, and the plan tests.

Branch: `main` clean (task 32 merged if Misha did it first; this task doesn't depend on it). Create
`move-timing`. Check for `* 2.*` duplicates before staging, and stage none.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## The model
- A move keeps `scoring_period_id` = **the day the move is MADE** (transaction day). Add
  `PlanMove.timing` ∈ `after_tipoff` | `before_games`:
  - `after_tipoff` (**the default for new moves**): the move is made after that day's games start. The dropped
    player **stays on the plan roster and in his slot for day D**, so his D game counts. The pickup joins
    that night and is in the plan roster/lineup from **D+1** (`lands_sp = D+1`).
  - `before_games`: today's behaviour. Both changes are effective on D (`lands_sp = D`).
- **Migration**: add the column with server default `before_games` for EXISTING rows, so moves already in
  Misha's plan keep their meaning. New moves default to `after_tipoff` (API default). One migration,
  `batch_alter_table`-safe, autogenerate clean afterwards.
- **Move time** (for waiver math): `before_games` keeps `plan_move_local_time` (09:00 PT). `after_tipoff` uses
  the day's **first game's start time** (from `NbaGame`), or 16:00 PT if the day has no games. Use it for
  `dropped_at` in `waiver_clears_at` and for the "pickup still on waivers at move time" check.
- **Acquisitions** count in the matchup of the transaction day D, not of `lands_sp`. Say so explicitly in the
  meter tooltip/hint ("counted on the day the move is made"). Note: a Sunday-night `after_tipoff` move uses
  the ending matchup's budget for a pickup who plays in the next matchup. That's correct per this model and
  it should be visible, not hidden.
- **Stream window**: for each move, compute the pickup's play window: `lands_sp` through the day before the
  move (if any) that drops him lands (`drop_from_move_id` chains, or a named drop of him), else the end of
  the plan horizon (the last matchup any move touches). Expose
  `stream: {first_sp, last_sp, open_ended: bool, game_days: [{sp, date, game display}], games: n,
  games_by_matchup: [{matchup_id, games}]}` on `PlanMoveOut`. Use the batched schedule lookup; no per-move
  queries.
- **Warnings**: replace the `no_game` info (pickup has no game on the move day, which is now meaningless for
  `after_tipoff`) with:
  - `stream_no_games` (warning): the pickup plays 0 games in his window
  - `stream_info` (info): "plays Thu 10/22 (1 game) before the Sat 10/24 move drops him"
  - `drop_game_lost` (info, `before_games` only): the dropped player has a game on D that you give up by
    moving before tip-off

  Keep every other warning as is. Use 31a's `move_label` in all text.
- `simulate()` / plan roster / plan lineup / `slot_substitutions` honour `lands_sp`: on D with `after_tipoff`,
  the dropped player is still rostered and slotted, and the pickup appears from D+1 taking that slot. Auto-detect
  is unchanged except that the match window must accept a transaction made in the evening of D (Pacific).
- **PATCH** accepts `timing`.

## UI
- **Composer**: "Make the move on [day]" plus a timing toggle "After tip-off (keep his game) / Before games"
  (default After tip-off). Underneath, live from the pickup's schedule, a read-out: **"Plays: Thu 10/22 @ LAL
  7:00 PM, Sat 10/24 vs DEN 5:00 PM · 2 games before the next planned move"**. If the dropped player plays on
  the move day: "Simons still plays Tue 10/20 (his game counts)" for after-tip-off, or "you give up his Tue
  game" for before-games. A **"Stream for" day picker** shortcut: choose the days you want him for, and the
  composer suggests the move day = the evening before the first chosen day, after tip-off. Misha can override.
- **Plan tab rows** read: `Tue 10/20 · after tip-off · Drop Simons → Add Cameron Johnson · plays Thu 10/22
  (1 game)`, plus the stream window's game days as compact chips.
- **Plan view roster** on day D for an `after_tipoff` move: the dropped player shows a "Leaving tonight" tag in
  his slot, and a "Joining tonight" strip lists the pickup (with "or <backup>"). On D+1 the pickup is in the
  slot, tagged Planned.
- The waiver panel stays as is (its day chips are game days, which is exactly what you filter streamers by).

## Testing
- Pure engine:
  - `after_tipoff` keeps the dropped player on D and adds the pickup from D+1
  - `before_games` is unchanged (existing tests still pass)
  - chains with mixed timings
  - stream window bounded by a chained drop, a named drop, and open-ended
  - `stream_no_games`, `stream_info`, `drop_game_lost`
  - waiver clear time computed from the first tip-off time (fixture schedule), including a DST-crossing case
  - acquisitions counted on D across a matchup boundary (a Sunday after-tip-off move)
  - lineup substitution lands on D+1
- Migration: existing rows become `before_games`, the API default for new moves is `after_tipoff`, and the
  round-trip works.
- API: POST/PATCH with timing, the `stream` block shape, a single batched schedule query (keep or adjust the
  query guards deliberately).
- Frontend: the composer timing toggle, the play read-out, the "Stream for" suggestion, the plan-row sentence
  + chips, and the Leaving/Joining tonight tags.
- All green: `make test`, `make lint`, `npm test`, `npm run lint`, `npm run build`.

## Acceptance
On Misha's DB, rebuild his own Matchup 1 idea: Tue 10/20 after tip-off, drop Anfernee Simons → add Cameron
Johnson (backup Jaylon Tyson). Plan view Tue: Simons still in his slot with "Leaving tonight". Plan view Thu:
Johnson in that slot, planned. The plan row reads "plays Thu 10/22 (1 game)…". The meter counts it in
Matchup 1. **Leave Misha's existing moves untouched** (ids 16, 17, 19, 20, now `before_games`), and delete
only what you create.

## Report back with
Branch, files, confirmation it's STAGED, not committed, the acceptance responses (trimmed), decisions/deviations,
and test counts.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
