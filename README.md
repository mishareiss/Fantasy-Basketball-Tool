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
universe from `kona_player_info`, then upserts both. It is idempotent — re-running reports
everything as unchanged rather than duplicating rows. Without cookies the app still boots and
`/health` still answers; only the sync fails, with a message naming the missing values.

The ESPN cookies expire periodically. When they do, sync returns HTTP 503 (or the CLI exits
non-zero) telling you to re-copy them from a logged-in browser session.

### Tests

`make test` is fully offline: it runs against sanitized ESPN responses recorded under
`backend/tests/fixtures/`. Refresh them with `make fixtures` after an ESPN change —
`scripts/record_fixtures.py` strips account and league identifiers and refuses to write a file
that still contains one. Tests marked `live` hit the real API and are excluded by default.

### Make targets

| Target | What it does |
|---|---|
| `make setup` | Install backend + frontend dependencies |
| `make db-up` / `db-down` / `db-logs` | Manage the Postgres container |
| `make migrate` | `alembic upgrade head` |
| `make revision m="..."` | Autogenerate a migration from the models |
| `make backend` | Uvicorn with reload on :8000 |
| `make frontend` | `next dev` on :3000 |
| `make sync` | Pull ESPN scoring settings + player pool into the DB (idempotent) |
| `make fixtures` | Re-record the sanitized ESPN test fixtures from a live pull |
| `make test` | pytest (offline — recorded fixtures, no ESPN cookies needed) |
| `make test-live` | Only the tests that hit the real ESPN API (needs cookies) |
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
│   │   ├── api/             # routers (health, sync; features next)
│   │   ├── db/              # engine/session, declarative Base, models/
│   │   ├── espn/            # ESPN v3 client, cookie auth, player parsing, league sync
│   │   ├── scoring/         # custom scoring formula parsed from mSettings
│   │   ├── projections/     # stub: pluggable ProjectionSource layer
│   │   ├── valuation/       # stub: current-year + dynasty value engine
│   │   ├── ranking/         # stub: ranking sets + personal model
│   │   ├── draft/           # stub: draft board, plan, live pick following
│   │   └── ingest/          # stub: CSV/paste import + player matching
│   ├── alembic/             # migrations (URL injected from Settings)
│   ├── scripts/             # sync_league + record_fixtures CLIs
│   └── tests/               # offline suite + tests/fixtures/ recorded ESPN JSON
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
