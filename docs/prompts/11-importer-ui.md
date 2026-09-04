# Task 11 — Importer UI + dynasty/redraft tag on ranking imports

Context: Builds on task 10 (branch `frontend-board`, merged to `main`). Branch off `main`. This is
step 1 of the multi-source ranking board (docs/FEATURE_SPEC sections 3 and 5): a browser UI for the
import pipeline so sources get in without the CLI, PLUS the small backend change that tags each
imported RANKING as dynasty or redraft. Why the tag: rank-only lists (Yahoo, ESPN standard, an
expert board) have no stats to age-adjust, so they must declare which horizon they belong to at
import; value sources (projections, betting lines) have production numbers and derive BOTH horizons
from the age curve, so they need no tag. Read FIRST: backend app/api/imports.py (POST /import/{kind},
GET /import/kinds, and the ImportResponse / RowOutcomeResponse shape — it already returns each row's
`candidates`), app/ingest/ranking.py + registry.py (the ranking handler + how it reads `options`),
app/db/models/ranking.py (RankingSet / RankingEntry), app/api/rankings.py, app/api/players.py
(POST /players/{espn_player_id}/aliases); and the frontend (frontend/CLAUDE.md, lib/api.ts —
`request`/`ApiError`/API_BASE_URL, the app/components/board/* patterns and the __tests__ / vitest
setup from task 10). Private ESPN fantasy basketball dynasty tool, custom scoring. Create a new
branch `importer-ui`.

IMPORTANT: When done, STAGE all changes (git add -A) but DO NOT COMMIT. The user commits himself.

PART A — backend: dynasty/redraft tag on ranking imports (small):
- Add `horizon` (str, 'dynasty' | 'redraft') to `RankingSet`, and make it part of the set's identity
  so a source can publish BOTH a dynasty and a redraft list with the same name/season without
  colliding: change the unique key to (source, name, season, horizon). One Alembic migration
  (batch_alter_table so it runs on the SQLite the tests use; backfill any existing rows to
  'redraft'). Verify from-scratch apply + downgrade.
- The `ranking` handler reads `options['horizon']` the way it already reads `options['name']`:
  REQUIRED for ranking imports, validated to {'dynasty','redraft'}, missing/invalid raises
  ImportParseError (-> 422), exactly like an unknown option today. The wholesale-replace lookup keys
  on (source, name, season, horizon).
- Surface `horizon` on GET /rankings and GET /rankings/{id}.
- Update the task-7 ranking tests/fixtures to pass a horizon; add tests: a dynasty and a redraft set
  of the same name/season coexist; missing/invalid horizon -> 422; the migration applies + downgrades.
- `adp` and `projection` imports are UNCHANGED (value sources, no horizon).

PART B — frontend: the importer page (no backend change beyond Part A):
- New route `/import` (add a nav link alongside the board and /status). Build from the task-10
  component patterns and lib/api.ts; TypeScript strict, no `any` on response types; Tailwind v4; add
  it to the Vitest suite.
- Extend lib/api.ts with typed `importKinds()` (GET /import/kinds), `importPreview(kind, body)` and
  `importCommit(kind, body)` (POST /import/{kind} with dry_run true/false), and
  `addPlayerAlias(espnPlayerId, body)` (POST /players/{id}/aliases). Types MIRROR the backend models
  (ImportResponse, RowOutcomeResponse incl. `candidates`, KindInfo) — read them, don't invent.
- Flow:
  1. Pick a KIND from GET /import/kinds — show implemented kinds (adp, projection, ranking) and show
     planned ones (market_line) disabled / "coming soon". Show the chosen kind's expected columns +
     header aliases as a hint.
  2. Provide the table two ways: a textarea to paste, and a file picker / drag-drop that reads the
     file IN THE BROWSER (FileReader -> text) and fills it — there is deliberately no upload endpoint.
  3. Config: source (required), season (optional; backend defaults to ESPN_SEASON), delimiter
     (optional/auto). Kind-specific, mapped to the API's `options`: projection -> basis
     (per_game | season); ranking -> name (the set label) + horizon (dynasty | redraft).
  4. PREVIEW (dry_run=true) -> render: the summary counts (matched / review / unmatched / duplicate /
     invalid, aliases created/existing, rows created/updated/unchanged), the DETECTED column mapping
     (`columns`) so a mis-detection is visible, the handler `notes`, and a per-row table color-coded
     by status.
  5. RESOLVE review rows inline: a review/unmatched row shows its `candidates`; clicking the right one
     calls addPlayerAlias(candidate.player_id, {source, source_name: row.source_name, source_id?})
     and then re-previews — the row now lands as method='alias' at confidence 1.0. Unmatched rows with
     NO candidate render as a "needs a manual match" list (a player-search box is a LATER task — do
     not build it now).
  6. COMMIT (dry_run=false) -> show the receipt (what was written) and a link back to the board.
- States: loading; validation (no text, or no source); API errors via ApiError, showing the
  backend's own message (e.g. a projection import with no synced scoring rules is a 409; a bad option
  is a 422).
- Optional only if cheap: let the user correct a mis-detected column via the API's `column_map` and
  re-preview; otherwise just DISPLAY the detected mapping.

Testing (offline; keep make test + npm test + make lint + the frontend lint/build + CI all green; ADD
tests):
- Backend: the Part A tests above.
- Frontend (Vitest + RTL, api client mocked): the kind list renders from a mocked /import/kinds;
  paste + Preview renders the counts, the detected columns, and the row table; a review row's
  candidate click posts an alias and triggers a re-preview; Commit posts dry_run=false; the ranking
  form surfaces name + horizon and the projection form surfaces basis; loading + error states.

Constraints:
- Reuse app/ingest + the existing import API — NO new import endpoint, NO multipart (the file is read
  client-side). Reuse frontend lib/api.ts + the task-10 component/test patterns. SQLite-testable
  migration (batch_alter_table). No new deps if avoidable. Secrets/config from Settings. macOS;
  Postgres host port 5433 — don't change it.

Acceptance criteria (verify before reporting done):
1. `make migrate` applies (from-scratch + downgrade); a ranking import requires a valid horizon; a
   dynasty and a redraft set of the same name/season coexist; GET /rankings shows horizon.
2. `/import`: pick a kind, paste or drop a file, Preview shows counts + rows + candidates; resolving a
   review row via a candidate makes it land as 'alias' on re-preview; Commit writes and links to the
   board.
3. `make test`, `npm test`, `make lint`, and the frontend lint + build all pass; CI stays green.

Report back with:
- Branch + file tree; confirm STAGED, NOT committed.
- The ranking horizon migration, and how the importer page looks/flows (kind picker, paste/upload,
  the preview, the inline resolve step, the ranking horizon selector).
- The tests added, front and back.
- Anything deferred (the unmatched-name search box, column-remap if you skipped it, the market_line
  kind) and confirm adp/projection imports are unchanged.

STAGE all changes (git add -A), DO NOT COMMIT — Misha commits himself. Report what was staged.
