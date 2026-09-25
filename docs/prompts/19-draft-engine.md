# Task 19 — Draft engine (backend, pure + tested)

Context: This is the first of three draft-plan-builder tasks (19 engine → 20 targets/plan
backend + live drafted-state persistence → 21 draft-room UI). You are building the PURE,
in-memory draft engine only: the config, the mutable draft state, the need-aware opponent
auto-pick, and the Monte-Carlo availability simulation. NO HTTP endpoints, NO DB models, NO
migrations — persistence and routes are Task 20. The board this feeds already exists.

Read these first (do not skip — you will reuse their names verbatim):
- `backend/app/ranking/sources.py` — `SourceCatalog`, `load_catalog`, `SourceSpec`, `catalog.select`.
- `backend/app/ranking/consensus.py` — `consensus_board`, `METHOD_RANK`.
- `backend/app/ranking/master.py` — `consensus_positions(sources, names)` returns
  `{player_id: 1-based consensus rank}`. This is EXACTLY the field-ranking primitive you reuse.
- `backend/app/api/consensus.py` — `_load(db, horizon)` (returns catalog + player identities);
  mirror how it resolves a horizon and builds names.
- `backend/app/config.py` — `Settings` / `get_settings()`; add the `DRAFT_*` settings here.
- `backend/app/db/models/league_settings.py` — `LeagueSettings.roster_slots` / `.team_count`
  (ESPN's `lineupSlotCounts`, keyed 'PG'/'SG'/'SF'/'PF'/'C'/'UT'/'BE'). Reference only; the
  engine takes roster slots as a plain dict, it does NOT query this.
- `backend/app/db/models/player.py` — `Player.positions` (JSON list of 'PG'/'SG'/'SF'/'PF'/'C').
- `backend/app/draft/__init__.py` — the stub you are filling in.
- `backend/tests/conftest.py` — the offline fixtures (`synced`, `make_ranking_set`,
  `players`, in-memory SQLite `db`). Your ONE db-touching test reuses these.

Domain in two sentences: it's a 10-team SNAKE startup dynasty draft, 20 rounds, Misha drafts at
slot 2 (his picks are 2, 19, 22, 39, 42, …). Every team's roster is 20 slots: 5 dedicated
starters PG/SG/SF/PF/C + 2 UT + 13 BE. "Availability" = the Monte-Carlo answer to "what % chance
is player X still on the board when my next pick comes up", given who's been taken so far.

Branch: create `task-19-draft-engine` off `main` (main is at the merged Task 18 head).

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT. Misha commits/merges/pushes
himself. NO AI attribution / Co-Authored-By / session trailers anywhere.

---

## Design decisions already made (do not re-litigate — implement these)

1. **Field ranking = consensus over a CONFIGURABLE subset of sources.** Opponents draft from the
   equal-weight rank consensus (`consensus_positions`) — deliberately NOT Misha's master board.
   The subset of source ids is a parameter: default = every available source under the horizon,
   but the caller can pass a list of source ids (validated with `catalog.select`, raising a
   `ValueError` on an unknown id — Task 20's route will turn it into a 400). The PURE engine never
   computes this itself — it receives `field_ranks: Mapping[int, int]` (player_id → 1-based rank,
   lower = better). A thin db-touching adapter builds that mapping; see step 5.

2. **Opponent auto-pick = soft need tilt (weighted random).** For the team on the clock:
   - Take the top-K still-available players by field rank (default K = `DRAFT_AUTOPICK_TOPK` = 12).
   - Base weight ∝ `exp(-field_rank / T)` (T = `DRAFT_AUTOPICK_TEMPERATURE`, default 8.0). Document
     the exact formulation you implement.
   - Multiply a candidate's weight by `DRAFT_AUTOPICK_NEED_MULT` (default 1.5) IF he is eligible
     at an **open dedicated starter position** for that team (see need rule below).
   - Sample ONE player from the normalized weights using an injected `random.Random` (seeded).
   - No hard positional constraints: with 13 bench slots and 20 rounds every team fills a legal
     20 regardless, so the only "legality" is total roster size = 20 and no player drafted twice.

3. **UT is positionless.** A team's positional NEED is its set of open DEDICATED starter slots
   (the 5 PG/SG/SF/PF/C not yet filled by an eligible player). UT slots (2) and bench (13) are
   filled by anyone and therefore do NOT create a positional tilt: once a team's 5 dedicated
   starters are filled, no candidate gets the need multiplier and picks become pure best-available.
   Make the "does this player fill an open dedicated slot" test a small documented function; a
   multi-position player fills a slot if ANY of his positions is an open dedicated slot.
   (Assigning a drafted player to a concrete slot for roster accounting: fill an eligible open
   dedicated slot first, else a UT slot, else bench. A simple greedy assignment is fine — it only
   feeds the need computation, it is not a lineup optimizer.)

4. **The sim does NOT auto-pick for Misha.** This is the key modeling choice. The availability
   Monte-Carlo projects OPPONENT picks forward from the current committed state; at any of
   Misha's own pick slots it removes NOBODY (his picks are made by hand in the real flow). So
   availability at his *next* pick is exact given committed state, and availability at a *later*
   pick is computed as if he takes nobody in between — a deliberate, documented simplification.
   The engine must therefore support two usage shapes, both as pure primitives (the mode TOGGLE
   itself is Task 21 UI — you only build the primitives):
   - **simulation step**: auto-pick opponents forward until the next Misha pick (or draft end),
     returning the picks made — this is how "simulation mode" advances to Misha's clock.
   - **manual apply**: commit an explicit (team, player) pick — this is how "manual mode" enters
     every team's pick by hand, and also how a completed Misha selection is recorded.

5. **Iterations = `DRAFT_SIM_ITERATIONS` (default 1000).** Deterministic under a passed seed.

---

## Scope — build these in `backend/app/draft/`

Keep the PURE engine (config, state, needs, autopick, availability) free of any `Session` /
`app.db` / `app.api` imports, so it is unit-testable on hand-built dicts. Put the one db-touching
adapter in its own module.

1. **`config.py` — `DraftConfig`** (frozen dataclass):
   - fields: `team_count` (10), `rounds` (20), `roster_slots` (dict like
     `{"PG":1,"SG":1,"SF":1,"PF":1,"C":1,"UT":2,"BE":13}`), `my_slot` (1-based, 2), and the
     starter/dedicated-position derivation.
   - derive: the full **snake pick order** as a list of team slots (round 1: 1..N, round 2:
     N..1, …), `total_picks = team_count * rounds`, `pick_slot(pick_number) -> team_slot`, and
     `my_pick_numbers -> list[int]` (all pick numbers belonging to `my_slot`). Validate
     `1 <= my_slot <= team_count` and that slot counts are sane; raise `ValueError` on nonsense.
   - a classmethod/factory to build defaults from `Settings` (team_count, rounds, my_slot from
     `DRAFT_*`; roster_slots defaulting to the agreed startup roster, overridable).

2. **`needs.py` (or fold into state):** given a team's drafted players + their positions +
   `roster_slots`, compute the **open dedicated starter positions** (per decision 3) and a
   `fills_need(player_positions, open_dedicated) -> bool`. Greedy slot assignment as noted.

3. **`state.py` — `DraftState`** (mutable is fine, but keep methods explicit and side-effect-clear):
   - holds: the ordered picks made (`list[(pick_number, team_slot, player_id)]`), and cheap
     derived views — `available` player set, `roster(team_slot) -> list[player_id]`,
     `on_the_clock -> team_slot | None`, `next_pick_number`, `is_complete`.
   - constructor takes `config` + the draftable universe (the set of player_ids that have a field
     rank) + `positions: Mapping[int, list[str]]`.
   - `apply_pick(player_id)` (drafts for the team currently on the clock) and/or
     `apply_pick(team_slot, player_id)` — pick the ergonomics, but reject: a player already
     drafted, a player not in the universe, or a pick when the draft is complete (`ValueError`).
   - a `copy()`/clone so the Monte-Carlo can fork state cheaply without mutating the caller's.

4. **`autopick.py` — `auto_pick(state, field_ranks, rng, *, top_k, temperature, need_mult) ->
   player_id`** implementing decision 2. Also a `simulate_opponents_until(state, field_ranks, rng,
   *, stop_slot=my_slot or a predicate, ...)` that repeatedly auto-picks the team on the clock and
   stops when the next pick belongs to `my_slot` or the draft ends (decision 4, simulation step).
   Auto-pick must be no-op-safe on an empty candidate set (draft essentially over) — document it.

5. **`availability.py` — the Monte-Carlo:**
   `simulate_availability(state, field_ranks, my_pick_numbers, *, iterations, seed, top_k,
   temperature, need_mult, candidates=None) -> dict[int, dict[int, float]]` mapping
   `player_id -> {my_pick_number: probability_still_available}` for the requested upcoming picks.
   - Per iteration: clone the committed state, seed a `Random(seed + i)`, and walk pick numbers
     forward. On an OPPONENT pick, auto-pick and remove that player. On one of MY pick numbers,
     remove NOBODY (decision 4) but record, for each requested target pick, whether each tracked
     player is still available at the moment that pick comes up. Availability = fraction of
     iterations in which the player was still on the board when that pick came up.
   - Only project as far as the last requested target pick (don't simulate the whole draft if only
     the next two picks are asked for).
   - `candidates` defaults to the currently-available players that have a field rank (so the
     result is bounded and meaningful); allow narrowing to a target list (Task 20 will pass one).
   - Deterministic: same `(state, seed, params)` → identical dict. Probabilities in [0, 1].

6. **`field.py` — the ONE db-touching adapter (kept separate from the pure engine):**
   `field_ranks(db, horizon, source_ids=None) -> dict[int, int]` that calls the consensus path
   (`load_catalog` → `catalog.select(source_ids)` when given, else all sources →
   `consensus_positions`) and returns player_id → rank. Reuse `app.api.consensus._load` /
   `consensus_positions` rather than re-deriving consensus. Raise a `ValueError` naming unknown
   source ids. Also a `positions_for(db, player_ids)` helper (or reuse existing identity loading)
   so Task 20 doesn't re-query positions.

7. Add `DRAFT_*` to `Settings` in `config.py` with the defaults above
   (`draft_team_count=10`, `draft_rounds=20`, `draft_my_slot=2`, `draft_autopick_topk=12`,
   `draft_autopick_temperature=8.0`, `draft_autopick_need_mult=1.5`, `draft_sim_iterations=1000`),
   documented in the same commentary style as the existing `DYNASTY_*` / `TIER_*` / `MARKET_*`
   blocks, and pin them in `conftest.py`'s `pinned_settings` fixture alongside the others so a
   local `.env` can't move a test's expected numbers.

8. Export the public engine surface from `app/draft/__init__.py` (config, state, auto_pick,
   simulate_availability, field_ranks) with an `__all__`, mirroring `app/ranking/__init__.py`.

## Testing — required, offline, deterministic (this is the point of the task)

Add `backend/tests/test_draft_engine.py` (pure) and a few db cases in the same file or
`test_draft_field.py` using the existing fixtures. Everything offline (no network); the pure
tests need NO database at all — build tiny hand dicts.

Cover at least:
- **Config / snake math:** a 10-team/20-round config yields `my_pick_numbers` starting
  `[2, 19, 22, 39, 42, …]` (assert the first five exactly) and 20 total; `pick_slot` round-trips;
  a 4-team/3-round toy gives the hand-computable snake `[1,2,3,4, 4,3,2,1, 1,2,3,4]`; bad `my_slot`
  raises.
- **State:** `apply_pick` removes from `available`, advances the clock through a snake correctly,
  rejects a double-draft / unknown player / pick-past-end; `copy()` is independent.
- **Needs:** open dedicated positions shrink as eligible starters are drafted; a multi-position
  player fills whichever dedicated slot is open; once 5 dedicated starters are filled, `fills_need`
  is False for everyone (UT/bench positionless).
- **Auto-pick:** deterministic under a fixed `Random`; a needed-position player at a slightly worse
  field rank beats an off-position better-ranked one materially more often than the un-tilted
  softmax would (assert over many seeded draws, or assert the weight arithmetic directly on a
  hand case); empty candidate set is handled.
- **Availability:** probabilities in [0,1] and deterministic under a seed; a consensus-#1 player is
  less available at pick 19 than at pick 2 (non-increasing across later target picks); a player
  nobody ranks near the top is ~100% available early; MY pick slots remove nobody (construct a
  case where auto-picking me would have taken a player and assert he's still counted available at
  my later pick). Keep fixtures SMALL (a dozen players, 4 teams) so it's fast on CI.
- **Field adapter (db):** on the `synced` fixture (optionally `+ make_ranking_set`), `field_ranks`
  returns a non-empty mapping matching `consensus_positions` over the same sources; passing an
  explicit source-id subset changes the ranking; an unknown source id raises `ValueError`.

Keep `make test` + `make lint` green (backend is uv/py3.12, ruff). No frontend changes in this
task, so `npm test` / `npm run build` are untouched — say so in your report.

## Constraints

- Pure engine imports NOTHING from `app.db`, `app.api`, or SQLAlchemy — only stdlib + dataclasses.
  Only `field.py` touches the session, and it REUSES the consensus path, it does not reimplement
  consensus.
- Reuse `consensus_positions` / `load_catalog` / `catalog.select` — do NOT grow a parallel ranking.
- No new heavy dependencies (stdlib `random` is the RNG; no numpy). DB-portable: the adapter runs
  on the same in-memory SQLite the suite uses.
- All tunables come from `Settings` defaults, overridable per call; nothing hardcoded at call sites.
- Deterministic under a seed everywhere randomness appears — the tests depend on it.

## Acceptance criteria (verify before reporting done)

1. `cd backend && uv run pytest -q` passes (old + new tests).
2. `make lint` clean.
3. `my_pick_numbers` for the default config begins `[2, 19, 22, 39, 42]` and has length 20.
4. The pure engine modules import with no `app.db` / `app.api` / sqlalchemy imports (grep them).
5. `simulate_availability` is deterministic under a fixed seed (same call twice → identical dict),
   returns probabilities in [0,1], treats Misha's own pick slots as no-ops, and never simulates
   past the last requested target pick.
6. `field_ranks(db, horizon)` on the `synced` fixture equals `consensus_positions` over all sources,
   and a source-id subset / unknown id behave as specified.

## Report back with

- Branch name + a file tree of what you added under `app/draft/` and `tests/`.
- Confirm everything is STAGED, NOT committed, on `task-19-draft-engine`.
- The exact `my_pick_numbers` list your config produces (all 20), and a 2–3 line dry-run of
  `simulate_availability` on a small synthetic state showing a few players' % at picks 2 and 19,
  so the numbers can be sanity-checked.
- Any decision or deviation from this brief, and why.
- What you deliberately left for Task 20 (state persistence + endpoints + target lists).

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
