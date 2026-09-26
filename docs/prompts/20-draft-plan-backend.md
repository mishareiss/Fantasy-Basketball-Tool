# Task 20 — Draft plan + live drafted-state (backend)

Context: Task 19 shipped the PURE draft engine in `app/draft/` (config, state, needs, autopick,
availability) plus the one db adapter `field.py`. This task persists a LIVE draft, exposes the
HTTP surface over it, builds the round-by-round PLAN on top of the availability engine, and wires
the drafted state into My Board so it can hide/annotate drafted players. After this, Task 21 is
PURE UI — no backend left to add.

Read these first (reuse their names verbatim — do not reinvent any of this):
- `backend/app/draft/__init__.py` — the engine's public surface. You will use `DraftConfig`,
  `DraftState`, `FieldBoard`, `field_ranks(db, horizon, source_ids=None)`,
  `positions_for(db, player_ids=None)`, `simulate_availability(...)`,
  `simulate_opponents_until(...)`, `UnknownSources`, `DEFAULT_SIM_ITERATIONS`, `DEFAULT_SIM_SEED`.
- `backend/app/draft/state.py` — `DraftState.__init__(config, universe, positions)`,
  `apply_pick(player_id, *, team_slot=None)`, `selections`, `roster(slot)`, `open_dedicated(slot)`,
  `on_the_clock`, `next_pick_number`, `is_complete`, `is_my_pick`. Illegal picks raise `ValueError`.
- `backend/app/draft/availability.py` — `simulate_availability(state, field_ranks, my_pick_numbers,
  *, iterations, seed, top_k, temperature, need_mult, candidates=None) -> {player_id: {pick_no: prob}}`.
- `backend/app/draft/autopick.py` — `simulate_opponents_until(state, field_ranks, rng, *, stop_slot=None, ...)`
  MUTATES the state and returns the `Pick`s it made (this is the sim-advance primitive).
- `backend/app/ranking/master.py` — `load_entries(db)` returns `MasterRankEntry` rows, ranked ones
  first in rank order (this is MY board's stored order + tags/notes — the source of the plan's lists).
- `backend/app/api/master.py` — the master-board router + `MasterPlayerRow`; you will add draft-mode
  params + fields here (see step 5). Note `_reference` / `reconcile` — you do NOT need to reconcile
  inside the plan; the master-board GET already maintains the order.
- `backend/app/db/models/master_tier.py` + `alembic/versions/e5c18b7a2f90_master_tier_break.py` —
  the model + migration pattern to mirror (String scope validated at the API edge, named unique
  constraint, pure `create_table` so it runs on SQLite offline and Postgres).
- `backend/app/db/models/__init__.py` (every new model MUST be imported here for autogenerate),
  `backend/app/api/__init__.py` (mount the new router), `backend/app/main.py`.
- `backend/tests/conftest.py` — fixtures `synced`, `aged`, `make_ranking_set`, in-memory `db`.
  For a master board with tags, seed it via the master endpoints or `MasterRankEntry` directly.

Branch: create `task-20-draft-plan` off `main` (main is at the merged Task 19 head, b930441).

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT. Misha commits/merges/pushes himself.
NO AI attribution / Co-Authored-By / session trailers anywhere.

---

## Decisions already made (implement these; do not re-litigate)

1. **ONE active draft (singleton).** Not multiple sessions. The current draft is a single row; a
   second `POST /draft` while one exists is a 409 unless `reset=true`. The draft row stores a
   CONFIG SNAPSHOT so it is self-describing and reproducible even if `Settings` change mid-draft.
2. **The draft log is mode-agnostic and linear.** Every made pick is a `draft_pick` row keyed by
   `pick_number`; manual entry and simulated opponent picks are the same kind of row, distinguished
   only by an `is_auto` flag. `mode` ('simulation' | 'manual') is stored on the draft as a UI
   preference and is NOT enforced by the backend (both `POST /draft/picks` and `POST /draft/simulate`
   work regardless of mode; undo is always available).
3. **The plan returns TWO lists per upcoming pick:** `targets` (players I tagged `target` on my
   master board, still available) and `best_available` (the top of my master-board order, still
   available) — each row joined with its availability % AT THAT PICK. Also surface my open needs.
4. **My Board draft mode is a param on the EXISTING `GET /master/board`,** not a new board endpoint.
   `?draft_mode=true` ANNOTATES every row with drafted status (so the UI can style available vs.
   drafted even when nothing is hidden); `?hide_drafted=true` additionally omits drafted rows.
   Both default false — with them off, `GET /master/board` is byte-for-byte what it is today.
5. **Availability stays the ephemeral Monte-Carlo** (never persisted); the sim-ADVANCE that commits
   opponent picks is a single seeded draw (a live mock), separate from it.

## Scope

### 1. Models + migration (`app/db/models/draft.py`, one Alembic revision from `e5c18b7a2f90`)
- `Draft` — the singleton current draft. Columns: `id` PK; config snapshot `team_count`, `rounds`,
  `my_slot`, `roster_slots` (JSON); the field view `field_horizon` (str) and `field_source_ids`
  (JSON list, NULL = all sources); `mode` (String(16), validated at the API edge against a
  `DRAFT_MODES = ("simulation", "manual")` tuple on the model — same pattern as `MASTER_TAGS`);
  `created_at` / `updated_at`. Snapshot defaults come from `DraftConfig.from_settings` /
  `Settings` at create time.
- `DraftPick` — `id` PK; `draft_id` FK→`draft.id` (ondelete CASCADE); `pick_number` (int),
  `team_slot` (int), `player_id` FK→`player.espn_player_id` (ondelete CASCADE, NOT NULL);
  `is_auto` (bool, default false, server_default false); `created_at`. UNIQUE(`draft_id`,
  `pick_number`) as a named constraint; index `player_id`. Register BOTH in `models/__init__.py`.
- Migration: pure `create_table` for both (+ the unique/index), mirroring the master_tier_break
  revision's structure and docstring style. It must apply cleanly on SQLite (offline) and Postgres.

### 2. Rehydration seam (`app/draft/session.py`, or a clearly-separated helper)
- `build_state(db, draft) -> tuple[DraftState, FieldBoard]`: `ranks = field_ranks(db,
  draft.field_horizon, draft.field_source_ids)`; `positions = positions_for(db, ranks.keys())`;
  build `DraftConfig` from the snapshot; construct `DraftState`; replay the draft's `DraftPick`
  rows in `pick_number` order via `apply_pick(player_id, team_slot=...)`; return the state and
  `FieldBoard.of(ranks)`. This is the ONE place the DB becomes an engine state — keep it small and
  test it directly (the T19 seam `field_ranks`/`positions_for` is what it stands on).

### 3. Draft state router (`app/api/draft.py`, prefix `/draft`, mounted in `app/api/__init__.py`)
- `POST /draft` — create the current draft. Body (all optional): `my_slot`, `mode`,
  `field_horizon`, `field_source_ids`, `roster_slots`; unset → `Settings`/`DraftConfig` defaults.
  409 if a draft already exists unless `reset=true` (which replaces it, dropping its picks — guard
  like `POST /master/seed`). A bad `field_source_ids` → `UnknownSources` → 400. Returns the state.
- `GET /draft` — the current draft: its config/mode, the pick log (pick_number, team_slot,
  player + name, is_auto), each team's roster, `on_the_clock`, my remaining pick numbers, and
  `is_complete`. 404 with a helpful message when no draft exists.
- `POST /draft/reset` — clear the picks, keep the config (start the same draft over). (Full
  reconfigure = `POST /draft?reset=true`.)
- `POST /draft/picks` — apply a MANUAL pick. Body `{player_id, team_slot?}` (team_slot defaults to
  `on_the_clock`). Wrap the engine: a `ValueError` from `apply_pick` (already drafted / not in the
  universe / draft complete / wrong slot) → 422 with the engine's message. Persists `is_auto=false`.
  Returns the state.
- `POST /draft/simulate` — the sim-advance: auto-pick OPPONENTS forward until my next pick (or the
  draft ends) via `simulate_opponents_until`, and PERSIST each made pick as `is_auto=true`. Body:
  optional `seed` (default: a fresh random seed so a live mock isn't identical every reset — but
  accept a seed for reproducible tests), plus `top_k`/`temperature`/`need_mult` defaulting to
  `Settings`. Returns the picks it made + the new state. (It stops before my seat — it never picks
  for me; that is the T19 rule.)
- `POST /draft/undo` — remove the last pick (highest `pick_number`), auto or manual. Returns the
  state. (Corrects a mis-entry and re-rolls a sim advance; callable repeatedly.)

### 4. The plan (`GET /draft/plan`)
- Params: `iterations` (default `DEFAULT_SIM_ITERATIONS`=1000), `seed` (default `DEFAULT_SIM_SEED`),
  `picks` (how many of my upcoming picks to plan for; default all remaining), `size` (top-N per
  list; sensible default ~15). 404 when no draft; a clean empty plan when the draft is complete.
- Build the candidate set from MY board: read `load_entries(db)` (ranked, non-excluded, in rank
  order) → `best_available` = those still AVAILABLE in the current state; `targets` = those with
  `tag == 'target'` still available. Bound the availability run to `candidates =` the union of the
  top players needed across all planned picks (e.g. the available board down to a reasonable depth ∪
  the tagged set) so the Monte-Carlo stays cheap.
- Run `simulate_availability(state, field, my_remaining_pick_numbers[:picks], iterations=…, seed=…,
  candidates=…)`. For EACH planned pick return `{pick_number, my open needs at that point,
  targets:[{player, board rank, tier, tag, availability}], best_available:[{…}]}` where availability
  is the player's % at THAT pick. Reuse the engine — do NOT recompute availability by hand.
- Availability uses the DRAFT's stored `field_horizon` / `field_source_ids` (consistent with how the
  room is being modelled), not a per-request source list.

### 5. My Board draft-mode wiring (edit `app/api/master.py` — additively)
- Add `draft_mode: bool = False` and `hide_drafted: bool = False` query params to `GET /master/board`
  (hide_drafted implies annotation). When on AND a draft exists, annotate every `MasterPlayerRow`
  with NEW optional fields: `drafted: bool = False`, `drafted_by_slot: int | None = None`,
  `drafted_by_me: bool = False` (rostered by the draft's `my_slot`). With `hide_drafted`, omit
  drafted rows from `players` (and `set_aside`). New fields DEFAULT to false/None so every existing
  master-board test still passes unchanged; with both params off the endpoint is unchanged. No
  active draft → the flags are a no-op (nothing drafted). Keep the drafted-set lookup a single
  query, not per-row.

## Testing — required, offline, deterministic
Add `backend/tests/test_api_draft.py` (endpoints), `test_draft_session.py` (rehydration), and extend
the master-board tests for draft mode. Everything offline (in-memory SQLite + fixtures; no network).
Cover at least:
- **Model/migration:** the new tables round-trip; `alembic upgrade head` applies on a throwaway
  SQLite db (mirror `test_migrations.py`'s offline driver). `make migrate` on Postgres is the human
  acceptance check — note it in your report, don't run it here.
- **Lifecycle:** create (with defaults and with body overrides); 409 on second create; reset clears
  picks; GET 404 before create; bad `field_source_ids` → 400.
- **Manual picks:** apply advances the clock; a double-draft / unknown player / pick-after-complete
  → 422; rosters and `on_the_clock` update; undo removes the last pick and restores availability.
- **Sim advance:** `POST /draft/simulate` commits opponent picks up to my next pick and STOPS at my
  seat (never drafts for me); `is_auto=true` on those rows; deterministic when a `seed` is passed;
  a second advance after I make my pick moves to my following pick.
- **Rehydration:** `build_state` replays a stored log to a state whose `selections` / rosters /
  `on_the_clock` match a state built by applying the same picks directly.
- **Plan:** on `aged` + a seeded master board with a couple of `target` tags, `GET /draft/plan`
  returns both lists per upcoming pick; availabilities in [0,1] and non-increasing across later
  picks; drafted players are absent from both lists; `targets` are exactly the tagged-and-available
  set; deterministic under a fixed `iterations`+`seed`; complete draft → empty plan.
- **My Board draft mode:** with a draft holding some picks, `draft_mode=true` flags drafted rows and
  marks my own picks; `hide_drafted=true` omits them; BOTH OFF is unchanged (assert an existing
  board response is untouched — this guards the frozen endpoint).

Keep `make test` + `make lint` green (backend uv/py3.12, ruff). No frontend changes this task, so
`npm test` / `npm run build` are untouched — say so in your report. Add any new `DRAFT_*` plan
defaults to `Settings` and pin them in `conftest.py`'s `pinned_settings`, matching Task 19.

## Constraints
- REUSE the engine and the T19 adapter: `simulate_availability`, `simulate_opponents_until`,
  `field_ranks`, `positions_for`, `DraftState`, `FieldBoard`. Do NOT reimplement availability,
  autopick, or consensus anywhere in `app/api` or the models.
- One migration, portable: pure `create_table`, applies on SQLite (offline tests) and Postgres.
- No new heavy dependencies. Secrets/config from `Settings`. DB-agnostic queries (the suite is SQLite;
  positions live in a JSON column — filter in Python as `players.py`/`consensus.py` already do).
- The master-board edit is ADDITIVE and off by default; the existing endpoint's behavior with no
  draft params must not change.

## Acceptance criteria (verify before reporting done)
1. `cd backend && uv run pytest -q` passes (old + new).
2. `make lint` clean.
3. `alembic upgrade head` applies on a fresh SQLite db (single head after your revision).
4. `GET /master/board` with no draft params returns exactly what it did before (a diff of a fixture
   response shows only additive optional fields, all defaulted).
5. `POST /draft/simulate` never creates a `draft_pick` for `my_slot`; `GET /draft/plan` availabilities
   are in [0,1], non-increasing across my later picks, and exclude drafted players.
6. `build_state` round-trips a stored log to an equivalent engine state.

## Report back with
- Branch + file tree of what you added/changed (`app/db/models/draft.py`, the migration,
  `app/draft/session.py`, `app/api/draft.py`, the `app/api/master.py` edit, tests).
- Confirm STAGED, NOT committed, on `task-20-draft-plan`, and that `alembic heads` shows one head.
- A short dry-run: create a draft, apply a couple of picks, `POST /draft/simulate` once, then paste
  a trimmed `GET /draft/plan` for my next pick (both lists with availabilities) so it can be
  sanity-checked; and a one-line `GET /master/board?draft_mode=true` sample showing a drafted flag.
- Decisions/deviations and anything deferred to Task 21 (all UI).

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
