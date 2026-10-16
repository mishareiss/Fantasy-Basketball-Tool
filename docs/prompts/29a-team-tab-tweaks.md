# Task 29a — Team tab tweaks: two-line game cells, multi-position filter, watchlist

Context: Task 29 (the `/team` page) is built and STAGED on branch `team-tab`, not yet committed. Misha
reviewed it and wants four tweaks **before** he commits. Continue on `team-tab`, on top of the staged work:
don't create a new branch, don't commit, and keep everything (old + new) staged together.

First, delete the six iCloud duplicate files (byte-identical copies, never staged; pytest has been
collecting them, which inflated the backend count to 1298):
`backend/app/team/{grid,schemas,views} 2.py`, `backend/tests/test_{api_team_views,team_display_grid,team_views} 2.py`,
and their `__pycache__` `* 2.*.pyc`. Then confirm `find . -name "* 2.*" -not -path "*/node_modules/*" -not -path "*/.venv/*"`
is empty. Report the corrected backend test count; it should be about 1190.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

Read first: `backend/app/team/{display,views,schemas}.py`, `backend/app/api/team.py`,
`frontend/components/team/*`, `frontend/lib/team.ts`, `frontend/lib/api.ts`, `frontend/__tests__/team*.test.tsx`.

## Scope

1. **Two-line game cell, so the roster never needs horizontal scrolling.** Everywhere a game shows (roster
   Game column, waiver day cells, player-card schedule), render:
   ```
   @ LAL
   7:00 PM
   ```
   Add `display_matchup` (`"@ LAL"` / `"vs LAL"`) and `display_time` (`"7:00 PM"` / `"TBD"`) to the backend
   `GameInfo` alongside the existing `display`, which stays for compatibility. Produce them in the one Pacific
   display helper in `app/team/display.py`. The frontend renders the two fields stacked; it doesn't split
   strings. Then re-check the roster at 1440px **and 1280px** wide: no horizontal scroll on the roster table
   with all `stat_columns` + FPTS/G visible. Tighten the Game column width accordingly. Screenshot both widths.
2. **Multi-select position filter.** The waiver panel's position control becomes toggle chips: PG SG SF PF C,
   any combination, none selected = all positions. The backend `GET /team/free-agents?position=` accepts a
   comma list with **OR** semantics (`position=SF,PF` → eligible for SF or PF), matched against
   `eligible_slots` with the existing fallback. A single value still works. Remember the selection with the
   panel's other localStorage prefs.
3. **Status filter → `All | Watchlist`.** Remove the All / Free agents / Waivers control. Everyone in this list
   is available, and waiver players already carry their `W · clears …` badge. Replace it with `All |
   Watchlist`. Keep the backend `status` param working (harmless), but the UI no longer sends it.
4. **Watchlist** (shared by both co-managers, so it lives in the DB, like the upcoming plan):
   - Model `WatchlistEntry` (`season`, `player_id` FK, `added_at`, `note` nullable; unique
     `(season, player_id)`) + migration (`batch_alter_table`-safe; autogenerate clean afterwards).
   - `GET /team/watchlist` → entries with player identity. `PUT /team/watchlist/{player_id}` (idempotent add,
     optional `{note}`). `DELETE /team/watchlist/{player_id}` (idempotent remove).
   - `GET /team/free-agents` gains `watchlist=true` (only watched players) and a `watched: bool` on every row.
     Raise the query-count guard deliberately by exactly the one batched query this needs, and say so.
     A watched player who gets rostered by anyone simply drops out of the free-agent list. The entry stays
     (he may come back), and `GET /team/watchlist` shows his current owner.
   - UI: a star toggle on each waiver row (filled = watched), and the same toggle in the player card header.
     Toggling is optimistic, then refetches the list (house rule: refetch after write). On failure, revert
     and show the error inline. The `All | Watchlist` control sends `watchlist=true`. Empty-watchlist state:
     "No one on your watchlist yet — star players to add them."

## Testing
- Backend: `display_matchup`/`display_time` (home, away, TBD, PDT/PST); multi-position OR (`SF,PF`, single
  value, unknown value → 422 or ignored, your call, documented); watchlist CRUD idempotency; `watchlist=true`
  filter; the `watched` flag; a rostered watched player is excluded from free agents but kept on the
  watchlist; migration round-trip; query-count guard updated.
- Frontend: stacked game cell in roster + waiver + card; position chips multi-select → the right param and
  none = no param; `All | Watchlist` replaces the old status control; star toggle (optimistic, revert on
  error, refetch); card star; empty-watchlist state.
- Everything green: `make test`, `make lint`, `npm test`, `npm run lint`, `npm run build`.

## Report back with
- Confirmation that it's still on `team-tab`, everything STAGED, not committed; the duplicates deleted; the
  corrected backend count.
- Screenshots at 1440px and 1280px showing no horizontal scroll, plus the waiver panel with SF+PF selected
  and the Watchlist view.
- The new query count, decisions/deviations, and test counts before → after.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
