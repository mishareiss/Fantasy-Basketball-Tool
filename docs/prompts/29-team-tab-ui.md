# Task 29 — The Team tab: day picker, ESPN-style roster with slot moves, waiver list with day filters, player card

Context: Tasks 27–28 (merged) built the data and the API: `GET /team/calendar`, `GET /team/day`,
`PUT /team/lineup/{sp}`, `POST /sync/lineup`, `GET /team/free-agents`, `GET /team/player/{id}`, `GET
/team/status` (see `backend/app/api/team.py` + `backend/app/team/schemas.py` for exact shapes). This task
is the **page**: a new `/team` route. **Frontend only.** If the API is genuinely missing something,
stop and tell me rather than changing the backend.

Misha's agreed spec:
- **Layout**: roster on the LEFT, waiver list on the RIGHT, side by side on a laptop (≥ `lg`). It stacks
  (roster first) below that. A day picker spans the top.
- **Day picker** chooses which day the ROSTER shows. It has prev/next arrows, a "Today" button, a label
  like `Wed 10/21 · Matchup 1`, and a strip of the selected day's matchup days, each showing the weekday,
  date, and how many of my players play (`my_games`). It also offers a way to jump to any day of the
  season (a native date input is fine) and to the previous/next matchup. Before tip-off (`today_period`
  null) it opens on `effective_period` with a small note: "Season starts Tue 10/20".
- **Roster** in ESPN-style slots, every slot a row including empty ones (`slots` from `GET /team/day`, in
  the order given). Columns: Slot | Player (name, NBA team, positions, injury badge if not ACTIVE/NORMAL)
  | Game (the server's `display` string, e.g. `@ LAL 7:00 PM`; blank if no game) | the `stat_columns` the
  response lists | FPTS/G. A player without a game that day is visibly dimmed (as ESPN does). Missing stats
  render as an em dash (reuse `MISSING` from `lib/format`).
- **Stats toggle** for the roster: `Projected | Actual`, and when Actual, a split select `Season / L7 / L15
  / L30` → the `basis` param. When `actuals_available` is false, Actual is disabled, with a hint
  "Available once games have been played".
- **Header strip** above the roster: the team name; a lineup-source badge (`ESPN lineup` / `Edited here` /
  `Carried from <date>` / `From roster`); acquisitions `used / limit this matchup`; and a **"Sync lineup
  from ESPN"** button (`POST /sync/lineup`). If the current day is `manual`, the button first asks for
  confirmation **inline** (not `window.confirm`; no browser dialogs) because it overwrites edits.
- **Slot moves (ESPN-style)**: every player row has a `Move` control. Clicking it highlights the legal
  destinations: an empty slot the player is eligible for, or an occupied slot whose player could legally
  take the mover's slot (a swap). BE is always legal; legality comes from `eligible_slots`, mirroring the
  backend rule in `app/team/grid.py`. Clicking a destination sends the full assignment list via `PUT
  /team/lineup/{sp}`. Escape or clicking Move again cancels. A checkbox "Apply through end of matchup"
  sends `through_sp` = the matchup's last sp. Show the server's 422 errors inline, and warnings (e.g. IR
  for a healthy player) as a non-blocking note. After any write, **refetch the day** rather than
  rendering the write's response (the house rule in `docs/HANDOFF.md`). Put the legality and
  swap-building logic in a pure `lib/team.ts` and unit-test it.
- **Waiver list (right panel)**, independent of the roster's day:
  - Its own day selector: chips for the days of a matchup (with a matchup switcher, defaulting to the
    roster's current matchup when the page opens, but not linked after that).
  - Mode `Off | Any | All`. One chip selected = "one specific day".
  - Its own `Projected | Actual(+split)` toggle.
  - Position filter (PG/SG/SF/PF/C), status filter (All / Free agents / Waivers), name search (debounced),
    sort (FPTS/G, Games, Name).
  - Columns: Player (name, team, positions, injury badge, and a **waiver badge** `W · clears Thu 10/22` from
    `clears_at_display`, or `W · clear time unknown` when `clears_at_known` is false) | one compact game
    cell per selected day, under the day's header (blank when he doesn't play), plus `games_in_days` |
    FPTS/G.
  - "Show more" paginates (`offset`), and the header shows the total. With mode Off, the game cells still
    show for the selected chips, so you can scan who plays when without filtering anyone out.
- **Player card**: clicking any player name (roster or waivers) opens a dialog: name, team, positions,
  injury, owner (Free agent / Waivers + clear time / team name). A stats table has rows Projected,
  2026-27 Season, L7, L15, L30 (rows with no data say "No games yet") and columns from `stat_columns` +
  GP + FPTS/G. Below it, the **next 7 days**, grouped under matchup headers (`Matchup 1 · Oct 20–25`)
  using `by_matchup`, each day listing weekday, date, and the game `display` or "—". The card's `from_sp`
  is the roster's selected day when opened from the roster, or the first selected chip when opened from
  the waiver list. Mirror `components/masterboard/PlayerDetail.tsx`'s dialog behaviour (fetches itself,
  local loading/error, Escape closes and returns focus to the row). Reuse its pieces where they fit;
  don't fork its styles.
- Leave room for task 31's `Actual | Plan` toggle in the header, but don't build it, and don't render a
  dead control for it.

Read first:
- `backend/app/team/schemas.py`, `backend/app/api/team.py`, `backend/app/team/grid.py` (wire shapes + the
  lineup legality rule you mirror)
- `frontend/lib/api.ts` (`request`, `api` object, `ApiError`, typed responses), `frontend/lib/format.ts`
- `frontend/components/masterboard/PlayerDetail.tsx` + `frontend/lib/playerdetail.ts` (dialog pattern)
- `frontend/components/draft/DraftRoomPage.tsx` + `DraftSidebar.tsx` (closest two-panel page + states)
- `frontend/app/layout.tsx` (nav), `frontend/__tests__/masterboard.test.tsx` + `fixtures.ts` (test patterns)
- `docs/HANDOFF.md` (UI rules: refetch after write; the CI timeout rule)

Branch: confirm `main` is clean and contains task 28 (`70cab89`). Create `team-tab` off `main`.

IMPORTANT: STAGE all changes (`git add -A`) but DO NOT COMMIT and do not push. Misha commits himself.
No Co-Authored-By / Claude attribution anywhere.

## Scope
1. **Types + client**: add `TeamCalendarResponse`, `TeamDayResponse`, `TeamSlotRow`, `TeamPlayer`,
   `GameInfo`, `StatLine`, `LineupEditBody`, `FreeAgentsResponse`/row, `TeamPlayerCardResponse`, and
   `TeamStatusResponse` to `lib/api.ts`, matching the backend schemas exactly (field names and nullability).
   Add `api.teamCalendar`, `api.teamDay({sp, basis})`, `api.saveLineup(sp, body)`, `api.syncLineup({first_sp?,
   last_sp?})`, `api.freeAgents(params)`, `api.teamPlayer(id, fromSp)`, `api.teamStatus()` through the
   existing `request`/query helpers.
2. **Route + nav**: `app/team/page.tsx`. Add `Team` as the FIRST nav link (it's the in-season home now).
   Keep the others, and update the nav comment.
3. **Components** in `components/team/`: `TeamPage`, `DayPicker`, `RosterTable` (+ move mode), `WaiverPanel`,
   `TeamPlayerCard`, and state panels (loading / error / "no team synced yet: run Sync"; the API returns
   409 when no team is synced). The page owns the roster's day/basis state. The waiver panel owns its own.
4. **URL state**: the roster day lives in `?day=<sp>` so a refresh or a shared link keeps it. The roster
   basis can live in `?basis=`. Waiver filters stay in component state, and
   `localStorage` (in try/catch) may remember the waiver panel's mode/basis per browser. Nothing else
   persists.
5. **Pure logic in `lib/team.ts`**: slot legality (`canMoveTo`), building the assignment list for a move or
   swap, grouping waiver day chips by matchup, the lineup-source badge text, the injury-badge rule. All
   unit-tested.
6. **Styling**: follow the existing pages (Tailwind, zinc palette, dark mode). Tables are compact and
   numeric columns right-aligned with tabular numerals, matching the board. No new UI dependencies.

## Testing (Vitest + RTL, api mocked, small fixtures; no 400-row fixtures needed)
- `lib/team.ts`: legality (eligible empty slot ✓, ineligible ✗, BE always ✓, a swap is legal only when
  BOTH players fit, IR), assignment building keeps every player exactly once, chip grouping across a
  matchup boundary, badge text for all four lineup sources.
- Page: renders the slot grid incl. empty slots and dimmed no-game rows. Day picker prev/next/today
  updates `?day` and refetches. The pre-season note. The 409 "no team synced" state.
- Basis toggle: Actual disabled when `actuals_available` false; the split select drives `basis`.
- Move flow: Move → legal targets highlighted → click → `saveLineup` called with the right full assignment
  list (and `through_sp` when the box is checked) → day refetched. A 422 shows the errors and
  leaves the grid unchanged. Escape cancels.
- Sync button: calls `syncLineup`, with the inline confirm only when the day is `manual`.
- Waiver panel: the mode/day chips/filters produce the right `freeAgents` params; a game cell per
  selected day; the waiver badge (known + unknown); Show more appends via `offset`; the roster day
  changing does NOT refetch the waiver list.
- Player card: opens from both panels with the right `fromSp`; stats rows incl. "No games yet"; days grouped
  under matchup headers; Escape closes and returns focus.

## Constraints
- Reuse `request`, `ApiError`, `lib/format`, and the PlayerDetail dialog pattern. No new dependencies. No
  backend changes.
- Don't touch other pages' behaviour, apart from the nav link.
- Remember the CI note: CI's runner is slow. Keep fixtures small; the global 20s `testTimeout` exists, so
  don't lean on it.

## Acceptance criteria
1. `npm test`, `npm run lint`, `npm run build`, plus backend `make test` + `make lint`, all green.
2. With `make backend` + `make frontend` against Misha's synced DB: `/team` shows Emzep's slot grid for
   Tue 10/20 (pre-season default). Stepping to Wed 10/21 shows Luka `vs GSW 7:00 PM` with LAL players
   undimmed. The waiver panel with days 10/21 + 10/23 in mode All lists only players who play both, and
   clicking one opens the card with his next 7 days grouped by matchup.
3. A Move between two slots saves, survives a reload (`Edited here`), and "Sync lineup from ESPN" (after the
   inline confirm) puts ESPN's lineup back.

## Report back with
- Branch, file tree, and confirmation it's STAGED, not committed.
- Screenshots (or a precise description if you can't take them) of: the full page at laptop width, move mode
  with highlighted targets, the waiver panel in All mode with two days, and the player card.
- Any API gaps you hit, and how you worked around them without backend changes.
- Decisions/deviations, and frontend test counts before → after.

STAGE everything (`git add -A`). DO NOT COMMIT. DO NOT PUSH.
