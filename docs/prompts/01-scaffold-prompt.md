# Task 1 — Scaffold the monorepo skeleton

## Context
This repo is a private fantasy-basketball dynasty tool for an ESPN league (H2H points, custom
scoring). Backend = FastAPI (Python), frontend = Next.js, database = Postgres. Full plans are in
`docs/PLAN.md` and `docs/FEATURE_SPEC.md` — read them first for the big picture, but for THIS task
build **only the empty skeleton**: no feature logic, no ESPN calls, no models beyond one trivial
example. The goal is a clean foundation where the backend and frontend both run and talk to each
other, with the database and migrations wired up.

Work on a branch (e.g. `scaffold`), not directly on `main`.

## Tech stack (use these exact choices)
- **Backend:** Python 3.12, dependency management with **uv**, FastAPI, Uvicorn, SQLAlchemy 2.0
  (synchronous), Alembic, pydantic-settings, psycopg (v3) as the Postgres driver, pytest + ruff.
- **Database:** Postgres 16, run locally via **Docker Compose**.
- **Frontend:** Next.js (latest, App Router) + TypeScript + Tailwind CSS, package manager **npm**.
- **Monorepo layout:** `backend/`, `frontend/`, `docs/` (already exists), plus root config.

## Deliverables

### 1. Backend (`backend/`)
- `pyproject.toml` managed by uv, with the dependencies above. Include a `uv.lock`.
- App package `app/` with this module layout — create each as a package (`__init__.py`) with a
  short docstring stating its future purpose, but **no implementation yet**:
  - `app/main.py` — FastAPI entrypoint; include the API router; a root `/` returning basic info.
  - `app/config.py` — pydantic-settings `Settings` loading from `.env` (fields listed below).
  - `app/api/` — routers. Add `app/api/health.py` with `GET /health` returning
    `{"status": "ok"}`, and a `GET /health/db` that executes `SELECT 1` and reports DB connectivity.
  - `app/db/` — SQLAlchemy engine/session setup (`session.py`), declarative `Base` (`base.py`),
    and `models/` package. Add ONE trivial example model (e.g. `HealthCheck` with id + timestamp)
    purely so Alembic has something to generate; we'll replace it next task.
  - `app/espn/` — (stub) future ESPN API client + cookie auth.
  - `app/scoring/` — (stub) future custom-scoring engine.
  - `app/projections/` — (stub) future pluggable ProjectionSource layer.
  - `app/valuation/` — (stub) future value engine (current-year + dynasty horizons).
  - `app/ranking/` — (stub) future ranking sets + personal model.
  - `app/draft/` — (stub) future draft board / plan / live pick following.
  - `app/ingest/` — (stub) future CSV/paste import pipeline + player matching.
- **Alembic** initialized under `backend/alembic/`, configured to read the DB URL from
  `Settings`/env (not hardcoded), with one generated initial migration for the example model.
- `tests/` with a couple of pytest tests: `/health` returns 200, and the app imports cleanly.
- Configure **ruff** (lint + format) via `pyproject.toml`.

### 2. Frontend (`frontend/`)
- Next.js App Router + TypeScript + Tailwind scaffold (its own `.gitignore`, `package.json`).
- A single placeholder page that, on load, calls the backend `GET /health` through a small typed
  API client (`lib/api.ts`) and displays the status, proving the two halves connect.
- Backend base URL read from `NEXT_PUBLIC_API_BASE_URL` (env), defaulting to `http://localhost:8000`.

### 3. Root
- `docker-compose.yml` with a **Postgres 16** service (named volume for persistence, port mapped,
  db/user/password from env with sensible dev defaults).
- Extend the EXISTING root `.gitignore` (currently the GitHub Python template) with: Node/Next
  (`node_modules/`, `.next/`, `frontend/build/`), env files (`.env`, `.env.*`, but NOT
  `.env.example`), local data/caches (`data/`, `*.csv`), and OS files (`.DS_Store`).
- `.env.example` at repo root documenting every variable (with placeholder values), including:
  `DATABASE_URL` (e.g. `postgresql+psycopg://fbb:fbb@localhost:5432/fbb`), `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_DB`, `ESPN_S2`, `SWID`, `ESPN_LEAGUE_ID`, `ESPN_SEASON`,
  `NEXT_PUBLIC_API_BASE_URL`, and commented placeholders for future odds API keys
  (`BALLDONTLIE_API_KEY`, `THE_ODDS_API_KEY`). **Never commit a real `.env`.**
- A `Makefile` (or `scripts/`) with convenience targets: `db-up` (docker compose up postgres),
  `backend` (run uvicorn with reload), `frontend` (run next dev), `migrate` (alembic upgrade head),
  `test`, `lint`.
- Update `README.md` with a "Getting started" section: prerequisites (uv, Node, Docker), setup
  steps (`cp .env.example .env`, `make db-up`, `make migrate`, run backend, run frontend), and the
  project layout.

## Constraints
- **Skeleton only** — no ESPN integration, no scoring/valuation/ranking logic, no real domain
  models beyond the one example. Stubs are empty packages with docstrings.
- Don't hardcode secrets or the DB URL anywhere; everything comes from env/Settings.
- Keep dependencies to what's listed; don't add extras we haven't discussed.
- Everything must run on macOS.

## Acceptance criteria (verify before reporting done)
1. `make db-up` starts Postgres; `make migrate` (alembic upgrade head) succeeds against it.
2. Backend starts (`make backend`); `GET /health` → 200 `{"status":"ok"}` and `GET /health/db`
   confirms DB connectivity.
3. Frontend starts (`make frontend`); the placeholder page shows the backend health status
   (proving cross-service calls work).
4. `make test` and `make lint` pass.

## Report back with
- The branch name and a summary of the file tree you created.
- The exact commands to run everything locally, in order.
- Anything you had to decide or deviate on, and any follow-ups for the next task (which will be:
  ESPN auth + canonical player identity + custom scoring settings).
