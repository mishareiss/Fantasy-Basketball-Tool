# Task 26 — The league is now 9 teams, not 10

Context: The draft engine (Tasks 19–22a) was built for a 10-team snake. The league is now
**9 teams**. Everything else is unchanged: **20 rounds**, Misha at **slot 2**, the same roster
(PG/SG/SF/PF/C + 2 UT + 13 BE). The engine is already parameterized: team count is one setting,
and the snake, board grid, seat inputs and availability sim all read it. So this is mostly
changing defaults, re-deriving the pinned numbers and fixing hardcoded copy. It is NOT a
refactor.

New shape, so you can check your work: 9 teams × 20 rounds = **180 picks**. Slot 2 picks
**2, 17, 20, 35, 38, …** (a 15-pick wait, then a 3-pick wait). They used to be 2, 19, 22, 39, 42
(a 17-pick wait, then 3) with 200 picks.

Read first:
- `backend/app/draft/config.py` (`DEFAULT_TEAM_COUNT`, module docstring, `_snake`, `my_pick_numbers`)
- `backend/app/config.py` (`draft_team_count` and the comment above it)
- `backend/app/api/draft.py` (`POST /draft` snapshots `team_count` onto the `Draft` row; `POST /draft/reset` KEEPS the snapshot)
- `backend/app/db/models/draft.py`, `backend/tests/conftest.py`, `backend/tests/test_draft_engine.py`, `backend/tests/test_api_draft.py`
- `frontend/lib/draft.ts`, `frontend/lib/api.ts`, `frontend/components/draft/DraftSetup.tsx`, `frontend/__tests__/draft.test.tsx`
- `docs/HANDOFF.md` (draft-shape lines)

Branch: make sure `main` is current. If `task-25-rank-alignment` still has unmerged or staged
work, STOP and tell me rather than building on top of it. Otherwise create `nine-team-league`
off `main`.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits
himself. Do not add any Co-Authored-By / Claude attribution anywhere.

## Scope

1. **The two defaults, kept in step.** `DEFAULT_TEAM_COUNT = 9` in `app/draft/config.py` and
   `draft_team_count: int = 9` in `app/config.py`. The existing `test_draft_engine` guard
   (`DraftConfig.from_settings(get_settings()) == DraftConfig()`) must still pass. Don't
   remove it. Leave `DEFAULT_ROUNDS = 20`, `DEFAULT_MY_SLOT = 2` and `DEFAULT_ROSTER_SLOTS` as they are.
2. **Env / deploy.** Grep `.env`, `.env.example`, `render.yaml`, `docker-compose.yml`, the
   `Makefile`, `docs/DEPLOY.md` and `.github/` for `DRAFT_TEAM_COUNT`. Anywhere it's set to 10,
   set it to 9. Anywhere the draft settings are documented (e.g. `.env.example`), document
   `DRAFT_TEAM_COUNT=9`. Report every place you found, including "none".
3. **Hunt every remaining 10-team assumption.** Search backend `app/`, frontend
   `app/ components/ lib/` and `docs/` for: `10 teams`, `ten teams`, `10-team`, `of 10`,
   `slot 2 of`, `19, 22`, `39, 42`, `17-pick`, `17-then-3`, `seventeen`, `200` (as total picks),
   and "ten" used as a count of seats or names. For each hit:
   - **Logic that hardcodes 10 (or 200 / 19 / 22) instead of reading `team_count`:** make it
     read the config. I expect none, so flag any you find in the report.
   - **Copy the user sees:** `DraftSetup.tsx` says "(ten teams, twenty rounds)". Render it from
     the live `teamCount` / rounds instead of hardcoding the new number, so the copy can't drift
     again. If rounds aren't available as a prop there, `{teamCount} teams` is enough. Don't
     re-plumb props for this.
   - **Docstrings/comments that use the 10-team numbers as the worked example** (`config.py`
     module docstring, `api/draft.py` near `my_pick_numbers`, `availability.py` "at 19 / 22",
     `lib/draft.ts`, `lib/api.ts`, `models/draft.py`, `DraftBoard.tsx` "ten seats"): update
     them to the 9-team numbers (2, 17, 20, 35, 38; 15-then-3; 180 picks; "pick 17 / pick 20").
     Leave a comment alone if it's about something genuinely unrelated (e.g. "ten" names in a
     list, the TF stat id `10`, `SEASON_START = (10, 1)`).
4. **Existing draft rows: the landmine.** A `Draft` row snapshots `team_count` when it's
   created, and `POST /draft/reset` keeps that snapshot. So changing the default does NOT
   change a draft already in Misha's database: it stays 10 teams. Don't write a migration that
   rewrites old drafts. The snapshot is deliberate and documented. Instead:
   - Make sure the draft room makes the mismatch visible. If the current draft's `team_count`
     differs from `get_settings().draft_team_count`, expose it on the state response (e.g. a
     `config_drift` / `settings_team_count` field, your call on the name) and show a short,
     plain notice in the draft room. Something like: "This draft was set up for 10 teams; the
     league is now 9. Start a new draft to use the new size." Use the existing new-draft path
     (`POST /draft` with `reset=true`), not a new endpoint. Keep it small.
   - In the report, tell Misha exactly what to click or call to replace his current draft.
5. **Don't touch** the ESPN sync's `LeagueSettings.team_count` (that's whatever ESPN reports),
   the scoring, the valuation/tiers code, or anything outside the draft shape. If you see the
   synced ESPN `team_count` in the dev DB, put its value in the report. Don't wire it into anything.

## Testing

- `backend/tests/conftest.py` pins `"draft_team_count": 10`. Change it to 9.
- Re-derive every pinned league-shape assertion in `test_draft_engine.py` and
  `test_api_draft.py`: `(10, 20, 2)` → `(9, 20, 2)`, `total_picks == 200` → `180`,
  `[2, 19, 22, 39, 42]` → `[2, 17, 20, 35, 38]`, `[19, 22]` → `[17, 20]`, `[2, 19, 22]` →
  `[2, 17, 20]`, `"outside 1..200"` → `"outside 1..180"`, and any others you find. Work out each
  new number from the snake. Don't just edit until the test passes: if one changes in a way you
  didn't predict, stop and explain why.
- Leave the toy-league tests (`team_count=2/3/4`) alone. They test the engine, not our league.
- Frontend: update `draft.test.tsx` to assert `pickNumbersFor(2, 9, 20).slice(0, 5)` equals
  `[2, 17, 20, 35, 38]`. Add one more assertion that `cellPickNumber` / `cellOf` round-trip for
  every cell of a 9×20 grid (180 cells, all distinct, covering 1..180).
- Add tests for the scope-4 drift signal: (a) a draft created at 10 with settings at 9 reports
  the drift; (b) a fresh draft at 9 reports none; (c) the frontend notice renders only when
  there's drift.
- Add one test pinning a 9-team, odd-count snake edge: at slot 9 (last seat) the picks are 9,
  10, 27, 28, … (back-to-back at the turn). The slot-1 mirror is 1, 18, 19, 36, 37.
- Everything stays offline and deterministic: fixtures + in-memory SQLite, no network.
- `make test`, `make lint` and `npm test` (and the frontend build/typecheck CI runs) must all be green.

## Constraints

- No new dependencies. No migration unless scope-4 truly needs a column. It shouldn't, because
  drift is computed at request time.
- Reuse `DraftConfig` / `from_settings`. Don't add a second place that knows the team count.
- Don't change rounds, seat, roster slots or the autopick/sim defaults.

## Acceptance criteria

1. `DraftConfig()` and `DraftConfig.from_settings(get_settings())` both give 9 teams, 20 rounds,
   slot 2, 180 picks, and `my_pick_numbers[:5] == [2, 17, 20, 35, 38]`.
2. `POST /draft` with no overrides creates a 9-team draft, and `GET /draft` shows 9 seats.
3. A grep for the old 10-team worked numbers (`19, 22`, `39, 42`, `ten teams`, `10 teams`,
   `slot 2 of 10`) in `backend/app`, `frontend/{app,components,lib}` and `docs/HANDOFF.md`
   returns nothing, except hits you explicitly justify in the report.
4. An old 10-team draft row is flagged on the state response and in the UI. A new one isn't.
5. `make test`, `make lint` and `npm test` are green. The new tests are in place.

## Report back with

- Branch, the list of files changed, and confirmation that everything is STAGED, NOT committed.
- Every `DRAFT_TEAM_COUNT` location found (env/deploy/docs), and what you changed.
- Every 10-team hit from scope 3, grouped as logic / copy / comment, with what you did to each.
  Call out loudly any LOGIC that hardcoded 10.
- The before → after of each re-derived test number.
- The name and shape of the drift field, plus a description of the notice.
- The exact steps for Misha to replace his existing 10-team draft.
- The ESPN-synced `team_count`, if one is in the dev DB.
- Test counts before → after for backend and frontend.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
