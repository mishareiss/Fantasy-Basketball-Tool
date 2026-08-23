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

### Make targets

| Target | What it does |
|---|---|
| `make setup` | Install backend + frontend dependencies |
| `make db-up` / `db-down` / `db-logs` | Manage the Postgres container |
| `make migrate` | `alembic upgrade head` |
| `make revision m="..."` | Autogenerate a migration from the models |
| `make backend` | Uvicorn with reload on :8000 |
| `make frontend` | `next dev` on :3000 |
| `make test` | pytest |
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
│   │   ├── api/             # routers (health today; features next)
│   │   ├── db/              # engine/session, declarative Base, models/
│   │   ├── espn/            # stub: unofficial ESPN v3 client + cookie auth
│   │   ├── scoring/         # stub: custom scoring formula from mSettings
│   │   ├── projections/     # stub: pluggable ProjectionSource layer
│   │   ├── valuation/       # stub: current-year + dynasty value engine
│   │   ├── ranking/         # stub: ranking sets + personal model
│   │   ├── draft/           # stub: draft board, plan, live pick following
│   │   └── ingest/          # stub: CSV/paste import + player matching
│   ├── alembic/             # migrations (URL injected from Settings)
│   └── tests/
├── frontend/                # Next.js App Router + TypeScript + Tailwind
│   ├── app/                 # layout + placeholder health page
│   └── lib/api.ts           # typed backend client
├── docs/                    # PLAN.md, FEATURE_SPEC.md, prompts/
├── docker-compose.yml       # Postgres 16
├── .env.example
└── Makefile
```

The feature packages under `backend/app/` are intentionally empty stubs — this is the
scaffold; implementations land task by task.
