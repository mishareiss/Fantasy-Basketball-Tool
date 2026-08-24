# Fantasy-Basketball-Tool

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

Open <http://localhost:3000>. The placeholder page calls the backend and should show
**API: ok** and **Database: connected**.

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
curl "localhost:8000/players/board?limit=20"        # top 20 by projected points per game
curl "localhost:8000/players/board?position=C"      # centers only
```

Players ranked by projected fantasy points per game **under our scoring**, each shown next to
their age and ESPN's redraft ADP — the gap between our number and ESPN's is the edge, and age
is what turns a redraft edge into a dynasty one. ADP is stored raw; the age *curve* that
weights value by longevity lands later, on top of the ages this board now carries.

> ESPN only publishes a season's projections once its preseason is under way. Out of season the
> sync stores ESPN's newest available projection and records the season it is really for, so the
> board is populated year-round and never mislabels a stand-in. `make sync` says so explicitly
> when the two differ, and `/players/board` returns the season in its response.

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
│   │   ├── api/             # routers (health, sync, players/board; features next)
│   │   ├── db/              # engine/session, declarative Base, models/
│   │   ├── espn/            # ESPN v3 client, cookie auth, player/projection/ADP parsing, sync
│   │   ├── ages/            # nba.com birthdates -> Player.birthdate/age at a fixed AGE_AS_OF
│   │   ├── matching/        # name normalization + fuzzy matcher; every source resolves here
│   │   ├── scoring/         # custom scoring formula parsed from mSettings + projection pricing
│   │   ├── projections/     # stub: pluggable ProjectionSource layer
│   │   ├── valuation/       # stub: current-year + dynasty value engine
│   │   ├── ranking/         # stub: ranking sets + personal model
│   │   ├── draft/           # stub: draft board, plan, live pick following
│   │   └── ingest/          # stub: CSV/paste import (will reuse app/matching)
│   ├── alembic/             # migrations (URL injected from Settings)
│   ├── scripts/             # sync_league / sync_ages + the two fixture recorders
│   └── tests/               # offline suite + tests/fixtures/ recorded ESPN and nba.com JSON
├── frontend/                # Next.js App Router + TypeScript + Tailwind
│   ├── app/                 # layout + placeholder health page
│   └── lib/api.ts           # typed backend client
├── docs/                    # PLAN.md, FEATURE_SPEC.md, prompts/
├── docker-compose.yml       # Postgres 16
├── .env.example
└── Makefile
```

The remaining feature packages under `backend/app/` are still empty stubs; implementations
land task by task.
