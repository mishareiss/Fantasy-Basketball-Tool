# HANDOFF — Fantasy Basketball Dynasty Tool (as of 2026-09-19)

SOURCE OF TRUTH for resuming. The desktop project-memory store (build_status.md etc.) has been
unwritable all session, so its notes are STALE — trust THIS file over them. Fold back into project
memory once it accepts writes again.

## The workflow (delegated build loop)
This Cowork session PLANS + writes prompts (docs/prompts/NN) that Misha pastes into a SEPARATE Claude
Code session which WRITES the code. This session never edits the repo directly except docs/prompts + this
handoff. Invoke the `anthropic-skills:delegated-build-loop` skill each session.
- Every CC prompt: STAGE all changes (git add -A) but DO NOT COMMIT. Misha commits/merges/pushes himself.
- NO AI attribution / Co-Authored-By / Claude-Session trailers in commits, ever.
- Keep `make test` + `make lint` + `npm test` + `npm run build` + CI green; every task ADDS tests
  (backend: offline pytest, SQLite in-memory; frontend: Vitest/RTL, api mocked).
- Review loop when Misha pastes CC results: confirm STAGED-not-committed on the right branch; verify
  risky claims independently (git, math, real-DB spot checks); note deviations; then give merge commands.
- Merge rhythm handed to Misha per task:
  `make test && make lint && ( cd frontend && npm test ) && ( cd frontend && npm run build )`
  then `git commit -m "<clean msg, no trailers>"` → `git checkout main && git merge <branch>` →
  `git push origin main && git push origin <branch>`.
- Mount/bridge gotchas: cloud↔Mac bridge throws `Resource deadlock avoided` / `.git/index.lock`
  (false-Modified noise — verify with `git diff`; read locked files via `git show <ref>:path`; overwrite
  in place with `cat > file`, NOT `mv`, which the no-delete policy blocks). device_bash runs in a Linux VM
  with NO GitHub creds and NO access to Misha's native git token (so no API log fetch, no push from here).
  remote-devices MCP drops/reconnects mid-session — reload via ToolSearch.

## Git / merge state
- main = 93722e5. Tasks 1-21a + the CI fix are all merged. CI green.
- T20 (draft plan + live drafted-state backend) MERGED at a738569 (parent b930441). 878 tests,
  autogenerate-clean migration f3a9c41d7b62, frozen /master/board guarded. Draft backend COMPLETE.
- T19 (draft engine) MERGED at b930441 (parent bee3f2c). Pure engine in app/draft/, 49 tests
  (41 pure + 8 db), full suite 813 passed, ruff clean. Reviewed independently this session:
  snake my_pick_numbers exact, pure modules zero app.db/app.api/sqlalchemy imports, availability
  deterministic + monotone + no-ops at my seat.
- History tail: 6bc5ed5 (T16) → e1240bc (T17) → 9623d39 (T18) → bee3f2c (CI fix shrank a deep-board
  Vitest fixture that timed out at 5s on CI's slower node-22 runner; standing rule below) → b930441 (T19 draft engine).
- STANDING CI RULE (learned the hard way): a `userEvent`-driven Vitest test over a full-depth fixture
  (deepMasterBoard(400)) can pass on Misha's node 25 but time out at 5s on CI node 22. Keep such tests on
  a small fixture (just past the 175 window, ~210) or set an explicit `it(..., 15000)` timeout.

## Done & merged (1-18)
- T1-11 foundation: ESPN sync (projections, ADP, custom scoring coefficients, player ages), import
  pipeline, dynasty age-curve valuation (DYNASTY_*), auto-tiers on the value board (TIER_*), Next.js
  value board, importer UI.
- T12 consensus board: multi-source (projection:espn, projection:market, adp:espn, ranking:<set>),
  equal-weight consensus, GET /sources + GET /board/consensus, rank/percentile toggle (shared-pool
  percentile is AFFINE with rank, so the toggle restyles, doesn't reorder — accepted), discrepancy
  coloring. app/ranking/{sources.py: load_catalog/available_specs/SourceCatalog/percentile_for,
  consensus.py: consensus_board}.
- T13 market_line source: sportsbook odds → de-vig → fair per-game value → Projection(source='market',
  kind='projected_season'); import kind 'market_line'; dials MARKET_SIGMA_FRAC=0.25, MARKET_DEFAULT_GAMES=70.
- T14 market entry/edit UI: /market page (add/edit/delete lines); market CRUD endpoints; delete last line
  removes the phantom projection; per-kind importer placeholders.
- T15 Master Ranking BACKEND: MasterRankEntry (rank NULL iff excluded, contiguous 1..N), reconcile-on-read
  (GET /master/board WRITES+commits — deliberate, idempotent; new→is_new at consensus slot, dropped→
  is_stale), API GET /master/board?horizon=&position= , PUT /master/order, PUT /master/entries/{id},
  POST /master/seed. ONE board, not per-horizon; horizon = reference lens only (MASTER_SEED_HORIZON=dynasty
  fixes membership). Backend `delta = rank - consensus_rank` (NEGATIVE = you have him above the field);
  DOCSTRING SAYS THE OPPOSITE — known wrong; UI flips it to "edge" in lib/masterboard.ts. Tiny future fix.
- T16 Master Ranking PAGE: /my-board drag/▲▼/move-to-# reorder (full-order PUT wins over optimistic;
  windowed PAGE=175 + search; horizon toggle), target/fade tags, notes, exclude→Set-aside tray,
  is_new/is_stale badges, consensus reference + edge column.
- T17 board tiers + position filter BACKEND: MasterTierBreak(scope, cut_rank); tiers are RANK BANDS
  (cut-ranks; a player dragged into the tier-1 band becomes tier 1); auto-seeded from dynasty-value gaps
  via assign_tiers (valueless carry; seed-on-read + persist); overall + per-position scopes; every row
  carries overall_tier + position_tier + position_scope; GET ?position= filters; PUT /master/tiers
  {scope, cut_ranks} (leading 1 required, sorted/unique); POST /master/tiers/reseed?scope=.
- T18 board tiers + position filter UI: interleaved TierDivider rows from cut_ranks; move (drag/▲▼)/add/
  remove/reset dividers; position chips (All·PG·SG·SF·PF·C) drive fetch + shown tier scope; reorder
  DISABLED under a position filter (tags/notes/exclude/tiers still live); player-drag vs divider-drag kept
  apart by refs. Reuses Segmented/Segment (exported from components/board/BoardControls.tsx).

## >>> NEXT: the DRAFT-PLAN BUILDER (5 tasks; 17-18 DONE). Draft settings agreed with Misha:
10-team league, SNAKE, 20 rounds. Roster 20 slots = 7 starters (PG,SG,SF,PF,C + 2 UT) + 13 bench.
STARTUP draft (whole player pool available). MISHA DRAFTS AT PICK 2. Tiers auto-seed + hand-adjust
(done). Auto-pick = POSITIONAL-NEED-AWARE weighted-random. Availability = Monte-Carlo sim → "% chance a
player is still there at your pick." Per-pick target lists (drawn from the master board, auto-drop as
players are drafted, each showing its availability %). ONE draft board that can auto-simulate OR be
filled by hand (both-in-one), and its drafted state feeds My Board's "draft mode" (hide drafted).

Task sequence:
- T19 (PROMPTED → docs/prompts/19-draft-engine.md, unstaged) — Draft engine (BACKEND, pure + tested): draft config (10-team snake, 20 rounds, roster
  slots, Misha at pick 2 → the pick numbers he owns), draft state (the picks made so far), the
  need-aware weighted-random auto-pick, and the Monte-Carlo availability simulation (run the draft
  forward from the current state N times → per-player probability still available at each of Misha's
  upcoming picks). No UI. Reuse the consensus (load_catalog/consensus_board) for the pool + baseline
  value; reuse the master board / roster slots. Keep it deterministic under a seed for tests.
- T20 — Plan + targets (backend): per-pick target lists (from the master board), live drafted-state,
  the availability-% join per target; wire drafted-state so My Board draft mode can read it.
- T21 — Draft room (frontend): the snake board you sim or fill by hand, per-pick target panels with
  availability %, and the My Board draft-mode / hide-drafted toggle.
- (later) per-source weighting on consensus; whole-app design/UX polish (deferred, function-first).

## T19 (DONE — built + reviewed 2026-09-25; staged on task-19-draft-engine, awaiting Misha's commit)
>>> NEXT: T20 PROMPTED → docs/prompts/20-draft-plan-backend.md (unstaged). Decisions settled 2026-09-25:
  - ONE active draft (singleton Draft row + DraftPick rows, config snapshot on the row); 2nd create 409s
    unless reset=true. Draft log is mode-agnostic + linear; is_auto flag distinguishes sim vs manual picks;
    `mode` ('simulation'|'manual') stored as UI preference, NOT enforced.
  - PLAN returns TWO lists per upcoming pick: `targets` (my tag='target' still-available) + `best_available`
    (top of my master board still-available), each joined w/ availability% at that pick, + my open needs.
  - My Board draft mode = ADDITIVE params on existing GET /master/board: ?draft_mode=true annotates every
    row (drafted / drafted_by_slot / drafted_by_me); ?hide_drafted=true also omits drafted rows. Both off =
    endpoint byte-for-byte unchanged (guard the frozen endpoint).
  - FULL state backend in T20: POST /draft, GET /draft, /draft/reset, /draft/picks (manual, any team),
    /draft/simulate (commit opponents to my next pick, is_auto, seeded single draw), /draft/undo, GET
    /draft/plan. New model app/db/models/draft.py + ONE migration from e5c18b7a2f90. Rehydration seam
    app/draft/session.py build_state() replays the log via field_ranks/positions_for/DraftState. T21 = pure UI.
  - Availability stays the ephemeral Monte-Carlo (1000, deterministic under seed); sim-advance is a separate
    single seeded draw. Await Misha's paste of T20 CC results to review. <<<
Engine shipped in app/draft/: config.py (DraftConfig+snake), state.py (DraftState/Pick, pass_pick for
the no-op clock advance), needs.py (RosterFill greedy fill, open dedicated slots), autopick.py
(FieldBoard, need-aware softmax auto_pick, simulate_opponents_until), availability.py (Monte-Carlo),
field.py (ONLY db module: field_ranks/positions_for, mirrors consensus _load body but raises
UnknownSources ValueError not HTTPException). DRAFT_* in Settings, pinned in conftest. The four decisions:
T19 = PURE in-memory draft engine in app/draft/ + unit tests. NO endpoints/models/migrations
(persistence + routes are T20). Answers to the four open questions:
1. NEED-AWARE AUTO-PICK: soft need tilt. Weighted random over top-K available by field rank
   (K=DRAFT_AUTOPICK_TOPK=12), base weight ∝ exp(-rank/T) (T=DRAFT_AUTOPICK_TEMPERATURE=8.0),
   ×DRAFT_AUTOPICK_NEED_MULT=1.5 for a player eligible at an OPEN DEDICATED starter slot. UT is
   POSITIONLESS → no positional tilt once the 5 dedicated starters are filled (then best-available).
2. FIELD RANKING (what the other 9 teams draft from): consensus over a CONFIGURABLE subset of
   source ids (default = all available sources), via consensus_positions() — NOT the master board.
   Pure engine takes field_ranks: Mapping[player_id,int]; a thin app/draft/field.py adapter builds
   it (load_catalog → catalog.select → consensus_positions), ValueError on unknown source id.
3. MY PICKS IN THE SIM: the sim does NOT auto-pick for Misha. Availability Monte-Carlo projects
   OPPONENT picks forward; at Misha's own pick slots it removes NOBODY (documented approximation:
   exact at his next pick, slightly optimistic at later ones). Engine exposes both a simulation-step
   primitive (auto-advance opponents to my next pick) and manual apply_pick (every team by hand).
   The simulation-mode / manual-mode TOGGLE itself is T21 UI.
4. SIM ITERATIONS: DRAFT_SIM_ITERATIONS=1000, parameterized, deterministic under a passed seed.
Roster (from settings, agreed): 10 teams, snake, 20 rounds, my_slot=2 → my picks 2,19,22,39,42,…
Roster slots PG/SG/SF/PF/C=1, UT=2, BE=13. All DRAFT_* live in Settings + pinned in conftest.

## T21 = DRAFT ROOM UI (frontend), SPLIT into 21a + 21b (decisions settled 2026-09-25)
- T21a (draft room: /draft snake board, search entry, sim controls, edit-pick + count endpoints)
  MERGED at 93722e5. Backend 891, frontend 186, verified (snake mapping, edit-pick, count).
- T21b (plan panels + My Board draft mode) BUILT + REVIEWED — staged on task-21b-draft-plan-ui, NOT
  committed, awaiting Misha's commit. Frontend-only (git-confirmed 0 backend files); npm 204 pass +
  build + lint; backend still 891 / single head. Verified: off-lens URL byte-unchanged, refetch-after-
  write when annotating, panel-click gated to is_my_pick && picks_away===0. Suggested commit (no trailers):
  "Add draft plan panels and My Board draft mode: per-pick target/best-available with availability %,
  auto-refresh, and Off/Show/Hide drafted lens".
>>> AFTER T21b MERGES: THE DRAFT-PLAN BUILDER IS COMPLETE (tasks 1-21b). Tool is draft-ready end to end. <<<
- T22 PROMPTED → docs/prompts/22-draft-room-redesign.md (unstaged): draft-room UI redesign from Misha's
  notes (2026-09-26). MERGE T21b FIRST (branch T22 off that). ONE task. Backend adds: team_names on Draft
  (+migration), PUT /draft/config (change my_slot only when picks_made==0; merge team_names anytime),
  GET /draft/availability (full-board availability at my next pick, candidates=None). Frontend: /draft
  becomes persistent LEFT SIDEBAR (available by my rank + search + targets-only + position chips + tags +
  availability% + Draft buttons disabled off my pick) + MAIN PANEL with tabs [Board|List|Roster|Rankings],
  default Board. Board = fluid grid, all 10 cols visible (no h-scroll), names truncate. Rankings = 6 cols
  (overall+PG/SG/SF/PF/C) by my rank w/ per-position tier breaks + availability. Roster = per-team, unmade
  picks shown as numbered blanks. Team names default 'Team N', mine '(You)'. Seat changeable pre-draft.
  REMOVES the T21b plan panels (DraftPlan); /draft/plan endpoint stays (now UI-unused). Heavy derivations
  go in lib/draft.ts (pure, tested).
Remaining backlog (no prompts written):
  1. Per-source consensus WEIGHTING (trust Dizzle 2x ESPN) — the one deferred ranking feature; needs a
     store for weights + a calibration path (see app/ranking/consensus.py EQUAL-WEIGHT note).
  2. Whole-app DESIGN/UX POLISH (function-first so far). CC's T21b notes to fold in: /draft plan+grid
     stack gets long (side-by-side or tabs); Targets vs Best-available overlap (dedupe the 2nd list?);
     three status lines in the room (one status region); plan rows show field rank but not WHICH
     consensus except in the config line.
  3. In-season features (games-maximizer / streaming, trade analyzer, league mirror) — see PLAN.md phases.
- T21a scope delivered = /draft page scaffold:
  * FULL SNAKE GRID (rounds x teams), my column highlighted, on-the-clock cell marked.
  * PICK ENTRY = search box (draft any available player for the on-the-clock seat) — this is how BOTH
    my picks and (manual mode) every opponent pick get entered, since the snake only lets the clock pick.
  * SIM CONTROLS: Advance to my pick, Step one pick, Undo, Reset, + EDIT ANY PICK.
  * Two SMALL BACKEND ADDITIONS in 21a (with tests, no migration): `count` param on POST /draft/simulate
    (step-one = count:1, still stops at my seat); PUT /draft/picks/{pick_number} to edit/override any
    made pick (swap player, free the old, is_auto=false, 422 on taken/unmade).
  * api.ts draft client (mirror app/api/draft.py models) + lib/draft.ts snake helpers + Vitest tests.
  * Mode toggle (simulation|manual) is CLIENT-SIDE (backend doesn't enforce mode; no persist endpoint).
- T21b (AFTER 21a merges) = the PLAN PANELS (targets + best_available per upcoming pick with
  availability %, from GET /draft/plan) wired into /draft (panel click-to-draft), PLUS the My Board
  draft-mode toggle: draft_mode/hide_drafted params on api.masterBoard + the 3 new MasterPlayerRow
  fields; a 3-state toggle on /my-board (off / annotate-drafted / hide-drafted). When NOT hiding, make
  it visually clear which rows are drafted vs available (Misha's ask).
- T21 CARRY-FORWARD (bake into 21a/21b): draft-mode annotation lives ONLY on GET /master/board, so any
  master-board WRITE (PUT order/entries) returns UN-annotated rows — the draft room + My Board must
  REFETCH the board after a write rather than render the write's response.
- Design: FUNCTION-FIRST, consistent with existing pages (Tailwind zinc, Segmented/Segment). Whole-app
  design polish still deferred. Off-board-player limitation: NON-ISSUE per Misha (only ~200 of a larger
  pool ever drafted) — dropped.

## Reuse surfaces
Consensus/pool: app/ranking/sources.py (load_catalog, available_specs, SourceCatalog, percentile_for),
consensus.py (consensus_board), app/api/consensus.py (_load, consensus_view). Value: app/api/players.py
ranked_board / value_player, dynasty_value. Tiers: app/valuation/tiers.py assign_tiers + Settings.tier_params().
Master board: app/api/master.py, app/ranking/master.py, app/ranking/tiers.py. Scoring/roster: league is
"I LOVE REMYS BUM CHEESE", season 2027; roster PG1 SG1 SF1 PF1 C1 UT2 BE13 (=20). Frontend: lib/api.ts
(request/put/post/ApiError), lib/board.ts (Horizon/POSITIONS), components/board + components/masterboard +
__tests__ Vitest patterns. Env: Mac Apple Silicon, Postgres Docker host port 5433, iCloud repo = slow cold
builds; CI = ubuntu, backend uv/py3.12 --frozen, frontend node 22 `npm install` (not npm ci), migrations
on real Postgres 16.
