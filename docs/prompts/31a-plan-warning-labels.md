# Task 31a — Plan warnings name moves by what they are, not by database id

Context: The waiver plan (tasks 30–31, merged) is live. The Plan tab numbers moves by their position in
the list ("move 1", "move 2"), but the backend's warning messages in `backend/app/team/plan.py` use database
ids ("Moves 12 and 13 both pick up Jerami Grant", "Move 14 drops whoever move 12 picks up…"). The same move
gets two different numbers on one screen. Ids also never mean anything to a person, and positions change
whenever moves are reordered or deleted, so neither belongs in prose.

Fix: every warning/info message refers to a move by a **stable human label**: its day and its players, e.g.
`the Wed 10/21 move (Gobert → Grant)`. For a chained drop, use the resolved pickup name, e.g.
`the Fri 10/23 move (Grant → Whitmore)`. When the pickup is unresolved, use the primary candidate.
**Backend only**, plus any frontend test fixture strings that assert on old message text.

Read first: `backend/app/team/plan.py` (every f-string that interpolates `move.id` / `other_move_id`, around
lines 520–705), its `name()` / `day()` helpers, `backend/tests/test_team_plan.py` and
`test_api_team_plan.py` (assertions on message text), `frontend/__tests__/team-plan.test.tsx` +
`teamFixtures.ts` (fixture messages).

Branch: confirm `waiver-plan-ui` (task 31) is merged to `main` and `main` is clean. Create
`plan-warning-labels` off `main`. Check for `* 2.*` duplicates before staging, and stage none.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Scope
1. One pure helper in `plan.py`, `move_label(move, ...) -> str`, built from the same name/day helpers the
   messages already use: `"the {Ddd M/D} move ({drop} → {pickup})"`. `drop` is the resolved drop's name
   (for a chain, the resolved pickup of the referenced move; for a broken chain, "an unresolved drop").
   `pickup` is the effective pickup. If two moves share the same label (same day, same players, which is
   unlikely), append ` #2` to the later one by `(sp, sequence, id)` order, so labels stay unique within
   one response.
2. Replace every `Move {id}` / `move {other_move_id}` / `Moves {a} and {b}` in warning messages with labels.
   Keep `move_id` and the `detail` fields exactly as they are. The UI links warnings to rows by `move_id`,
   not by text.
3. Don't touch codes, severities, which warnings fire, or any API shape. Only the `message` strings change.

## Testing
- `move_label`: a named drop, a resolved chain, an unresolved chain (primary candidate), a broken chain,
  and the duplicate-label suffix.
- Every warning code that mentions a move: its message contains the label and **no bare number**. Assert,
  e.g., with a regex that `Move \d+` / `move \d+` never appears in any message from a plan exercising
  every code.
- Update existing message assertions (backend + frontend fixtures) to the new text. No behavioural test
  changes.
- `make test`, `make lint`, `npm test`, `npm run build` green.

## Report back with
- Branch, files, and confirmation it's STAGED, not committed.
- Before → after for each changed message (one example each), and test counts.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
