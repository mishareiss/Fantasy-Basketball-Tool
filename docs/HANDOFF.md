# HANDOFF — Fantasy Basketball Dynasty Tool (as of 2026-09-19)

Written from a Cowork planning session because the project-memory store was unavailable for many
turns. Mirrors what build_status.md would hold; fold back into project memory when it's writable.

## Workflow (unchanged)
Cowork session PLANS + writes prompts (docs/prompts/NN) → Misha pastes into a separate Claude Code
that STAGES (never commits) → Cowork reviews → Misha commits/merges/pushes himself. No AI/Co-Authored
trailers. Keep make test + make lint + npm test + CI green; every task adds tests. Merge rhythm:
`git commit -m "…"` (no trailers) → `git checkout main && git merge <branch>` → `git push origin main`.

## Git / merge state
- main = 600371e (tasks 1-15 merged).
- Task 16 (Master Ranking PAGE) = STAGED on branch `master-page` (off 600371e), reviewed & clean,
  zero backend changes, awaiting Misha's commit/merge.

## Done & merged (1-15)
- T1-11 foundation; T12 consensus board (multi-source, equal-weight, /sources + /board/consensus;
  shared-pool percentile is affine w/ rank — toggle restyles, doesn't reorder; accepted).
- T13 market_line source (odds → de-vig → Projection(source='market'); MARKET_SIGMA_FRAC=0.25,
  MARKET_DEFAULT_GAMES=70). T14 market entry/edit UI (/market; delete removes phantom projection;
  per-kind importer placeholders).
- T15 Master Ranking BACKEND (branch was master-ranking): MasterRankEntry (rank NULL iff excluded,
  contiguous 1..N), reconcile-on-read (GET /master/board WRITES+commits — deliberate, idempotent;
  new→is_new at consensus slot, dropped→is_stale), /master board/order/entries/seed API. One board,
  not per-horizon; horizon = reference lens only (MASTER_SEED_HORIZON=dynasty fixes membership).

## STAGED, not merged — T16 Master Ranking PAGE (branch master-page)
- Route /my-board (nav: Board · My Board · Import · Market). components/masterboard/* + lib/masterboard.ts.
  REORDER interaction: drag + ▲/▼ + "move to #"; full order held in state, PUT /master/order sends ALL
  non-excluded ids, server response beats optimistic guess (concurrent-write ticket; horizon rides each
  mutation). Depth: PAGE=175 rendered + "show more" + whole-order search. Tags (target/fade), inline
  notes (save on blur), exclude→Set-aside tray→restore, is_new/is_stale badges, horizon toggle (order
  unchanged, reference changes), reset-to-consensus behind a confirm. Reuses Segmented/Segment
  (exported from BoardControls), board table/delta styling, market page pattern. 33 new tests (127 FE).
- KNOWN BUG (backend, NOT fixed here — frontend-only task): app/api/master.py `delta` docstring is
  WRONG. Code + tests are `delta = rank - consensus_rank` (NEGATIVE when you rank a player higher than
  the field), but the docstring says positive=higher. The UI is correct: lib/masterboard.ts `edge() =
  -delta` (positive/green ▲ = you have him above the field). FUTURE 1-liner: fix the docstring; OPTIONAL
  bigger cleanup: flip the backend field to the edge convention (positive=higher) + update 3 test
  asserts so API and UI speak the same language.

## Design mockup (Misha picked REORDER)
https://claude.ai/artifact/BBwmwpkBmn7XytDkECPES5  (a Cowork Docs artifact; not shared to CC's access —
CC built T16 from the written spec, which was fine.)

## Roadmap
- The Master Ranking board is now COMPLETE end-to-end (pending T16 merge). The tool is usable for a draft.
- Remaining: draft-plan builder (next big feature); per-source weighting on the consensus; the delta
  docstring/sign cleanup (tiny); whole-app design/UX polish (deferred, function-first by Misha).
- Not built: keyboard-only drag (▲/▼ + move-to-# cover it), multi-select moves.

## Open items / debts
- Branch protection on main (require CI) — Misha TODO.
- ~124 players un-aged until a full `make sync-ages` (no-age → 1.0× dynasty curve).
- delta docstring/sign (above).
- Env: Mac (Apple Silicon), Postgres Docker host port 5433, iCloud-synced repo = slow cold builds.
