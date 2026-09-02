# Fantasy-Basketball-Tool

[![CI](https://github.com/mishareiss/Fantasy-Basketball-Tool/actions/workflows/ci.yml/badge.svg)](https://github.com/mishareiss/Fantasy-Basketball-Tool/actions/workflows/ci.yml)

A dynasty fantasy basketball toolkit for player valuation, trade analysis, roster and draft management, and multi-season stat projections.

Built for a private ESPN league with H2H points and a custom scoring formula. See
[docs/PLAN.md](docs/PLAN.md) for the overall plan and [docs/FEATURE_SPEC.md](docs/FEATURE_SPEC.md)
for the draft-tool spec.

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python 3.12 and backend dependencies)
- Node.js 20+ and npm
- Docker Desktop (for the local Postgres 16 container)
- `make`

### Setup

```bash
cp .env.example .env      # then fill in ESPN cookies/league id when you have them
make setup                # uv sync + npm install
make db-up                # start Postgres 16 in Docker
make migrate              # alembic upgrade head
```

Then run the two services in separate terminals:

```bash
make backend              # FastAPI on http://localhost:8000  (docs at /docs)
make frontend             # Next.js on http://localhost:3000
```

Open <http://localhost:3000>. That is the **draft board**: every player we can price,
ranked by the selected horizon and cut into tiers, with a horizon / position / depth / tiers
toggle above it and a read-only "curve & tiers" inspector showing the two dials the ranking
depends on. The reachability check that used to live there is now the footer strip, and in
full at <http://localhost:3000/status> (**API: ok**, **Database: connected**).

An unsynced backend gives the board an explicit "run `make sync`" state, and a stopped one a
"can't reach the API" state — neither is a blank table.

Quick check from the shell:

```bash
curl localhost:8000/health      # {"status":"ok"}
curl localhost:8000/health/db   # {"status":"ok","database":"connected"}
```

### Syncing the league from ESPN

With real `ESPN_S2` / `SWID` / `ESPN_LEAGUE_ID` / `ESPN_SEASON` in `.env`:

```bash
make sync                            # or: curl -X POST localhost:8000/sync/league
```

That pulls our custom scoring coefficients from ESPN's `mSettings` view and the full player
universe from `kona_player_info`, then upserts four things: league settings, scoring rules,
players, and — from the same player payload — ESPN's **season projections** (priced under our
custom scoring) and its **redraft ADP**. It is idempotent — re-running reports everything as
unchanged rather than duplicating rows. Without cookies the app still boots and `/health`
still answers; only the sync fails, with a message naming the missing values.

The ESPN cookies expire periodically. When they do, sync returns HTTP 503 (or the CLI exits
non-zero) telling you to re-copy them from a logged-in browser session.

### Player ages (nba.com)

ESPN publishes no birthdate anywhere in its fantasy API, and age is the biggest lever on
dynasty value after production itself. So ages come from nba.com instead, via `nba_api`:

```bash
make sync-ages                       # or: curl -X POST localhost:8000/sync/ages
```

Two halves. The **match** is offline and instant: `nba_api`'s bundled static roster is resolved
against our ESPN-keyed players by `app/matching`, and each confident match is recorded as a
`PlayerAlias` carrying its confidence and method. The **fetch** is one HTTP call to nba.com per
player, paced and retried, and only for players that have an alias but no birthdate — so the
first run takes ten-odd minutes and every run after it is nearly free. Interrupting it is safe;
progress is committed as it goes.

Ages are computed at a **fixed as-of date**, not `date.today()`:

```
AGE_AS_OF=2026-10-01     # defaults to Oct 1 of the season ESPN_SEASON starts
```

Birthdate is the source of truth and `Player.age` is a cached derivative of it. That makes a
stored age reproducible — the same player is the same age on two runs a month apart — and
correct for draft day rather than for whenever the sync last happened. Changing `AGE_AS_OF`
and re-running rebuilds every age with no network calls. `/players/board` returns the date its
ages were computed at, because an age without one means nothing three months later.

nba.com does not know every player ESPN lists — draft-and-stash prospects, G-League call-ups,
and rookies newer than the installed `nba_api` roster. Those are the long tail:

```bash
curl "localhost:8000/players/unresolved?need=age"          # the worklist, best players first
curl -X POST localhost:8000/players/1234/aliases \
  -d '{"source":"nba_api","source_id":"1628389","source_name":"Their nba.com Name"}' \
  -H 'Content-Type: application/json'                      # then re-run make sync-ages
```

A hand-made alias wins over every matcher forever after, so each of those is a one-time fix.

### The board

```bash
curl "localhost:8000/players/board?limit=20"                      # top 20 by DYNASTY value
curl "localhost:8000/players/board?limit=20&horizon=current_year" # ...by win-now value
curl "localhost:8000/players/board?position=C"                    # centers only
curl "localhost:8000/players/board?tiers=off"                     # no tier column
```

Players ranked by projected fantasy points per game **under our scoring**, each shown next to
their age and ESPN's redraft ADP — the gap between our number and ESPN's is the edge, and age
is what turns a redraft edge into a dynasty one.

Every row carries **both value horizons**, always:

| Column | What it is |
| --- | --- |
| `current_year_value` | Win-now value: the projected points per game itself, age-agnostic. Identical to `fantasy_points_per_game`. |
| `dynasty_value` | The same number through the age/longevity curve — youth rewarded, age discounted. |
| `age_multiplier` | The factor the curve applied. |
| `age_adjusted` | `false` when we hold no birthdate, so the 1.0 above it means "nothing to adjust by", not "in his prime". |
| `tier` | Which draft tier of the selected horizon he lands in, 1 at the top. `null` below the tiered pool. |

`horizon` picks which of the two **orders** the board (`dynasty`, the default, or
`current_year`) — never which one is computed. Flipping it re-ranks the same numbers, which is
the Current-Year ⇄ Dynasty toggle from FEATURE_SPEC 4. A player with no age keeps a 1.0
multiplier and therefore sits exactly where his production puts him on either board.

#### Draft tiers

A ranked list says who is better; a **tiered** list says who is interchangeable — "draft
anyone still on the board in this tier" — which is the only form the information is usable in
with 90 seconds on the clock. The board tiers itself by default:

```bash
curl "localhost:8000/valuation/tiers"                        # the breaks, with their gaps
curl "localhost:8000/valuation/tiers?horizon=current_year"   # ...for the win-now ranking
```

Walking the selected horizon's values downward, a new tier opens wherever the drop to the next
player is more than `TIER_GAP_MULTIPLE` times the **median** drop. Median rather than mean is
the whole trick: the top of a dynasty board has two or three enormous cliffs in it, a mean is
dragged upward by exactly those, and the ordinary-but-real breaks further down get swallowed
into one 40-man blob.

| Setting | Default | What it does |
| --- | --- | --- |
| `TIER_GAP_MULTIPLE` | `2.0` | A break is a drop bigger than this many typical drops. Higher -> fewer, larger tiers |
| `TIER_MIN_SIZE` | `2` | No tier smaller than this — unless the gap is genuinely huge (a real outlier gets to stand alone) |
| `TIER_MAX` | `15` | Hard cap; past about fifteen a tiered board is a list again. Overflow merges the *least* significant breaks |
| `TIER_POOL` | `150` | How many top-ranked players get tiered. Below that, `tier` is `null` — untiered, not last |

Tiers are cut over the **overall** ranking, so they are identical whether you ask for 20 rows
or 200, and a `position` filter narrows who is shown without changing anyone's tier: a point
guard keeps his tier on the board, not his tier among point guards. A player with no birthdate
is tiered on his projection, exactly where the board ranks him.

`GET /valuation/tiers` shows each tier's size, value range, leader, and the gap that opened it
— including that gap as a multiple of the typical one. Reading that column down the page is
the fastest way to tell whether `TIER_GAP_MULTIPLE` is anywhere near right.

> **Deferred to the UI:** manual/drag tier breaks and per-position tiers. Imported ranking sets
> already carry their own source's tier column, separately from this.

#### The dynasty curve

One transparent multiplier on the current projection — not a multi-year model. Five numbers,
all from the environment, all inspectable:

```bash
curl "localhost:8000/valuation/curve"   # the active params + an age -> multiplier table
```

| Setting | Default | What it does |
| --- | --- | --- |
| `DYNASTY_PRIME_START` | `24` | Start of the prime band (multiplier 1.0) |
| `DYNASTY_PRIME_END` | `27` | End of the prime band |
| `DYNASTY_YOUTH_BONUS_PER_YEAR` | `0.04` | Added per year under prime start (age 20 -> 1.16x) |
| `DYNASTY_DECLINE_PER_YEAR` | `0.05` | Subtracted per year over prime end (age 32 -> 0.75x) |
| `DYNASTY_MIN_MULTIPLIER` | `0.40` | Floor the decline can't pass (bites at 39+) |

The defaults are a **moderate** curve — a starting point to calibrate against, not a
conviction. Change one in `.env`, restart, and the board re-ranks; nothing needs a code change
and nothing is stored, because value is computed on read.

> ESPN only publishes a season's projections once its preseason is under way. Out of season the
> sync stores ESPN's newest available projection and records the season it is really for, so the
> board is populated year-round and never mislabels a stand-in. `make sync` says so explicitly
> when the two differ, and `/players/board` returns the season in its response.

### Importing a CSV or a paste

Almost nothing outside ESPN has a usable free API: consensus ADP, expert projections, ranking
lists and season-long props are all web-only. So they arrive as an upload or a spreadsheet
paste, and one pipeline handles all of them — it finds the columns by header alias, resolves
every foreign name through `app/matching`, and previews the whole thing before writing a row.

```bash
# ADP: a consensus board, one number per player per season.
make import KIND=adp SOURCE=hashtag SEASON=2027 FILE=~/Downloads/adp.csv
make import KIND=adp SOURCE=hashtag SEASON=2027 FILE=~/Downloads/adp.csv COMMIT=1

# Projections: per-game stat columns plus GP, priced under OUR scoring.
make import KIND=projection SOURCE=hashtag SEASON=2027 FILE=~/Downloads/proj.csv BASIS=per_game
make import KIND=projection SOURCE=hashtag SEASON=2027 FILE=~/Downloads/proj.csv BASIS=per_game COMMIT=1

# Rankings: an ordered board with optional tiers. NAME labels the set.
make import KIND=ranking SOURCE=hashtag SEASON=2027 NAME="Dynasty Top 200" FILE=~/Downloads/top200.csv
make import KIND=ranking SOURCE=hashtag SEASON=2027 NAME="Dynasty Top 200" FILE=~/Downloads/top200.csv COMMIT=1
```

**Dry run by default.** Without `COMMIT=1` nothing is stored: you get the columns it found,
the matched rows, and two worklists — names it couldn't confidently place (`review`) and names
we carry nobody for (`unmatched`). Fix one with a hand-made alias and re-import:

```bash
curl -X POST localhost:8000/players/1234/aliases \
  -d '{"source":"hashtag","source_name":"Their Spelling"}' -H 'Content-Type: application/json'
```

Committing twice is a no-op — the second run reports every row `unchanged` and creates no new
aliases, which is the cheap way to confirm a file has already landed. The same two phases are
available over HTTP for a paste: `POST /import/projection` with `{"source", "text", "season",
"options": {"basis": "per_game"}}`, and `GET /import/kinds` lists what can be imported.

#### A ranking is a set, and re-importing it replaces it

ADP and projections store *a number about a player*. A ranking is a **list**, so it gets its own
tables (`ranking_set` / `ranking_entry`): the interesting facts about a board are collective —
who is on it, who fell off it, where the tiers break.

A set is identified by **(source, `NAME`, season)**, which is what `NAME=` is for: it lets one
source publish several boards, and it decides which stored board an import lands on. `NAME`
defaults to the source, which is right for a source with exactly one list.

Re-importing that identity **replaces the set wholesale** — the old entries are deleted and the
file's are written. Version two of a list is a different list: players drop off it and the rest
shift, and upserting row by row would leave last week's fallen players sitting there at their old
ranks. The dry run tells you the damage before you take it (`replacing 13 entries with 8, 5
player(s) drop off`).

Rank comes from a rank column when the file has one — gaps and all, because a board that prints
1, 2, 3, 6, 9 means those numbers. With no rank column, the **order of the rows is the ranking**,
taken from each row's position in the *file*: a name we couldn't place leaves a gap at its number
rather than promoting everyone below it. Tiers are stored as text ("1", "Tier 2", "Elite"),
because half of sources name their tiers rather than numbering them.

Read them back with `GET /rankings` (every set we hold, with its entry count and age) and
`GET /rankings/{id}` (the entries in rank order, joined to the player; `?limit=` for the top N).

#### Projections are priced by us, not by the source

An imported projection is a **stat line**, not a fantasy-point number. Only counting stats come
in — PTS, REB/OREB/DREB, AST, STL, BLK, TO, 3PM/3PA, FGM/FGA, FTM/FTA, MIN, PF, DD/TD and GP,
under whatever the source calls them (`TREB`, `3PTM`, `TOV`, `MPG`, `Games Played`). Columns
nothing can multiply (`FG%`, `FT%`, rank, tier, ADP) are ignored outright, because scoring a
percentage is silently wrong rather than loudly wrong. Rebounds implied by an OREB/DREB split,
and misses implied by attempts minus makes, are filled in exactly — FTMI is worth -0.5 in our
league, so dropping it would be a real error.

Those stats then go through **the same `score_projection` that prices ESPN's**, with the same
coefficients. That is what makes the two comparable: switch the board's `source` and the only
thing that changed is whose stat line you're looking at.

```bash
curl "localhost:8000/players/board?limit=20&source=hashtag"   # ranked by the import
curl "localhost:8000/players/board?limit=20"                  # ranked by ESPN, untouched
```

`BASIS` says what the file's numbers are. `per_game` (the default, and what almost every export
is) multiplies through by GP to get season totals; `season` divides instead. Either way both
lines are stored, because the board reads per-game and a draft plan budgets totals. A row with
no usable games count keeps its per-game value and stores no season total — the projection
still ranks, and nobody invented an 82-game season for a player who may not play at all.

### Tests

`make test` is fully offline: it runs against recorded responses under
`backend/tests/fixtures/` — sanitized ESPN payloads plus a narrow slice of nba.com's roster and
birthdates. Refresh them with `make fixtures` and `make nba-fixtures` after an upstream change;
`scripts/record_fixtures.py` strips account and league identifiers and refuses to write a file
that still contains one.

Anything that talks to a real external API is deselected by default: `live` (ESPN, needs
cookies) and `nbaapi` (nba.com, needs nothing but a network). Run them deliberately with
`make test-live` and `make test-nba` — they are the canary for an upstream change the recorded
fixtures cannot see.

### Continuous integration

Every push and pull request runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml): the
backend's `make lint` and `make test`, the frontend's `npm run lint`, `npm run build`, and
`npm test` (Vitest component tests for the board, with the api client mocked), and
`alembic upgrade head` (then down and back up) against a real Postgres 16 service — the
migrations are SQLite-tested here, and Postgres is where they actually run.

CI uses **no secrets and reaches no third party**. The `live` and `nbaapi` markers are
deselected by default, so the suite is the same offline suite you run locally. Keep it that
way: a check that needs cookies is a check that goes red for reasons nobody can fix.

> Enable branch protection on `main` in GitHub (Settings -> Branches) requiring the CI checks
> to pass before merge. That's a repo setting, not code — CI can report, but only branch
> protection makes it a gate.

### Make targets

| Target | What it does |
|---|---|
| `make setup` | Install backend + frontend dependencies |
| `make db-up` / `db-down` / `db-logs` | Manage the Postgres container |
| `make migrate` | `alembic upgrade head` |
| `make revision m="..."` | Autogenerate a migration from the models |
| `make backend` | Uvicorn with reload on :8000 |
| `make frontend` | `next dev` on :3000 |
| `make sync` | Pull ESPN scoring settings, players, projections + ADP into the DB (idempotent) |
| `make sync-ages` | Match nba.com's roster to our players; fill in birthdates + ages (idempotent) |
| `make import KIND=... SOURCE=... FILE=...` | Import a CSV/paste (`KIND=adp\|projection\|ranking`, `BASIS=`, `NAME=`, `COMMIT=1` to write) |
| `make fixtures` | Re-record the sanitized ESPN test fixtures from a live pull |
| `make nba-fixtures` | Re-record the offline nba.com fixtures (roster + birthdates) |
| `make test` | pytest (offline — recorded fixtures, no ESPN cookies needed) |
| `make test-live` | Only the tests that hit the real ESPN API (needs cookies) |
| `make test-nba` | Only the tests that hit the real nba.com stats API (no credentials) |
| `make lint` / `make fmt` | ruff check + format |

### Configuration

Everything comes from the gitignored root `.env` (template: `.env.example`) — no secrets or
connection strings live in code. The backend reads it via `app/config.py`
(pydantic-settings); Docker Compose and the Makefile read it directly. **Never commit a real
`.env`** — the ESPN `espn_s2`/`SWID` cookies are account credentials.

The local Postgres container publishes **port 5433** by default so it doesn't collide with a
Postgres install already using 5432. Change `POSTGRES_PORT` and `DATABASE_URL` together if you
want a different port.

## Project layout

```
.
├── backend/                 # FastAPI service (Python 3.12, uv)
│   ├── app/
│   │   ├── main.py          # app factory: routers + CORS
│   │   ├── config.py        # pydantic-settings, reads the root .env
│   │   ├── api/             # routers (health, sync, players/board, rankings, valuation/curve+tiers)
│   │   ├── db/              # engine/session, declarative Base, models/
│   │   ├── espn/            # ESPN v3 client, cookie auth, player/projection/ADP parsing, sync
│   │   ├── ages/            # nba.com birthdates -> Player.birthdate/age at a fixed AGE_AS_OF
│   │   ├── matching/        # name normalization + fuzzy matcher; every source resolves here
│   │   ├── scoring/         # custom scoring formula parsed from mSettings + projection pricing
│   │   ├── projections/     # stub: pluggable ProjectionSource layer
│   │   ├── valuation/       # age curve, the two value horizons, and gap-clustered tiers (on read)
│   │   ├── ranking/         # stub: consensus blending + personal model (storage: db/models/ranking)
│   │   ├── draft/           # stub: draft board, plan, live pick following
│   │   └── ingest/          # CSV/paste import: adp + projection + ranking kinds on one pipeline
│   ├── alembic/             # migrations (URL injected from Settings)
│   ├── scripts/             # sync_league / sync_ages + the two fixture recorders
│   └── tests/               # offline suite + tests/fixtures/ recorded ESPN and nba.com JSON
├── frontend/                # Next.js App Router + TypeScript + Tailwind
│   ├── app/                 # / is the draft board; /status is the reachability check
│   ├── components/board/    # controls, table + tier dividers, states, curve/tier inspector
│   ├── lib/api.ts           # typed backend client (types mirror the pydantic models)
│   └── __tests__/           # Vitest + React Testing Library, api client mocked
├── docs/                    # PLAN.md, FEATURE_SPEC.md, prompts/
├── docker-compose.yml       # Postgres 16
├── .env.example
└── Makefile
```

The remaining feature packages under `backend/app/` are still empty stubs; implementations
land task by task.
