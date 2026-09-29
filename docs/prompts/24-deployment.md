# Task 24 — Make the tool deployable: shared-password auth, env-driven config, Vercel + Render + Neon

Context: The app runs locally only (FastAPI + Postgres + Next.js, no auth). Misha wants it hosted so
his co-manager can use it from any device, with all state shared — which is already true the moment
both hit ONE backend + ONE Postgres, because every bit of state lives in the DB. This task makes it
safely deployable: a shared-password gate, env-driven CORS/URLs, deploy config for **Vercel**
(frontend) + **Render** (backend) + **Neon** (Postgres), and a DEPLOY.md walk-through. Branch
`task-24-deploy` off `main` (8716f1a). STAGE, DO NOT COMMIT. No AI trailers. NO DB migration.

Read first:
- `backend/app/main.py` — the FastAPI app + `CORSMiddleware(allow_origins=settings.cors_origins)`.
- `backend/app/config.py` — `Settings` (pydantic-settings, `.env`), `cors_origins: list[str] =
  ["http://localhost:3000"]`. A list env var does NOT parse from a plain string by default — you add
  a validator so `CORS_ORIGINS` can be comma-separated.
- `backend/app/api/__init__.py` (router mounts), `backend/app/api/health.py` (the health endpoints
  that must stay OPEN), `backend/app/api/sync.py` (`POST /sync/league`, `POST /sync/ages`).
- `backend/app/db/session.py` (engine/`DATABASE_URL`), `backend/pyproject.toml` + `uv.lock` (uv,
  py3.12), `Makefile`, `docker-compose.yml`, `backend/alembic` (migrations run via `alembic upgrade
  head`), `scripts/sync_league.py` / `scripts/sync_ages.py` (the sync entrypoints for the cron).
- `frontend/lib/api.ts` — `API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"`
  and the single `request()` that every call goes through (this is where the auth header attaches).
- `frontend/app/layout.tsx` (where a gate wraps the app), `.env.example`, `README.md`.

Keep green: `make test` + `make lint` (backend), `npm test` + `npm run build` + `npm run lint`
(frontend). CI rule: small Vitest fixtures.

---

## 1. Shared-password auth — enforced ONLY when configured
The whole app currently mutates data with no auth; on a public URL that's unacceptable. Add a single
shared access token, but make it a no-op unless set, so local dev and the 939 existing tests (which
send no token) stay green.

Backend:
- New `Settings.app_access_token: str | None = None` (`APP_ACCESS_TOKEN` in env).
- A FastAPI dependency (applied to the whole `api_router`, or as middleware) that, WHEN
  `app_access_token` is set, requires `Authorization: Bearer <token>` (constant-time compare) and
  returns 401 otherwise. When it's None (local/test/CI), it's a pass-through — auth OFF. EXEMPT the
  health endpoints (`/health`, `/health/db`) and root `/` so Render health checks + the status strip
  work unauthenticated.
- Tests: with `app_access_token` unset, every existing endpoint still works (this is what keeps the
  suite green — assert one endpoint 200s with no header). With it set: no/incorrect header → 401 on a
  protected route; correct Bearer → 200; `/health` → 200 even with no header.

Frontend:
- A gate: when `request()` gets a 401, or no token is stored, show a small **login screen** (one
  password field). On submit, store the value (localStorage) and use it. `request()` attaches
  `Authorization: Bearer <stored token>` on every call (via the existing single `request()` chokepoint
  — do NOT touch each call site). A 401 clears the stored token and returns to the login screen. When
  `NEXT_PUBLIC_API_BASE_URL` points at a backend with no token configured, the gate still works (any
  password is accepted by an open backend) — keep it simple, this is a shared password for two people,
  not user accounts.
- Tests (mocked fetch): `request()` attaches the header when a token is stored; a 401 response clears
  it and surfaces the gate; storing a password lets calls proceed. Wrap reads in try/catch for
  localStorage (private-window safe).

## 2. Env-driven config
- `CORS_ORIGINS`: add a pydantic `field_validator` (or `model_validator`) on `Settings.cors_origins`
  that accepts a comma-separated string (e.g. `https://foo.vercel.app,https://bar`) OR a JSON list, so
  it can be set from a single env var on Render. Default stays `["http://localhost:3000"]`.
- Confirm `DATABASE_URL`, `ESPN_S2`, `SWID`, `ESPN_LEAGUE_ID`, `ESPN_SEASON`, odds keys, and the new
  `APP_ACCESS_TOKEN` all read from env (they do via Settings) — and that NONE are committed. Update
  `.env.example` with the new keys (`APP_ACCESS_TOKEN`, `CORS_ORIGINS`) and a comment.
- Frontend already reads `NEXT_PUBLIC_API_BASE_URL`; make sure nothing else is hardcoded to localhost.

## 3. Deploy config (files in the repo)
- **Backend Dockerfile** (`backend/Dockerfile`): python:3.12-slim, install `uv`, `uv sync --frozen`,
  run `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Mirror the CI Python/uv versions.
- **`render.yaml`** (repo root) — a Render Blueprint declaring:
  - a **web service** built from `backend/Dockerfile`, with `preDeployCommand: alembic upgrade head`
    (so the schema migrates on every deploy), a health check path `/health`, and env keys declared
    with `sync: false` (values pasted in the dashboard, never in git): `DATABASE_URL`,
    `APP_ACCESS_TOKEN`, `ESPN_S2`, `SWID`, `ESPN_LEAGUE_ID`, `ESPN_SEASON`, `CORS_ORIGINS`, odds keys.
  - a **cron job** (daily) running `python -m scripts.sync_league && python -m scripts.sync_ages`
    against the same env (it has DB + cookies, so it needs no HTTP/token) to refresh ESPN data.
- **Frontend**: Vercel auto-detects Next.js; document (in DEPLOY.md) setting the project **Root
  Directory = `frontend`** and env `NEXT_PUBLIC_API_BASE_URL = <the Render backend URL>`. A tiny
  `frontend/vercel.json` is optional; only add it if it removes ambiguity — don't over-configure.
- Note in DEPLOY.md: use Neon's **pooled** connection string for `DATABASE_URL`, with `sslmode=require`
  (psycopg needs it); Neon is fine with the `postgresql+psycopg://` driver prefix.

## 4. `docs/DEPLOY.md` — the click-through
A concise, ordered guide a person can follow without guessing:
1. Neon: create project → copy the POOLED connection string → that's `DATABASE_URL`.
2. Render: New → Blueprint from the repo (render.yaml) → paste the env values (incl. a strong
   `APP_ACCESS_TOKEN` you invent, and `CORS_ORIGINS` = the Vercel URL you'll get in step 3 — you may
   need to come back and set it once Vercel gives you the domain). Deploy; `alembic upgrade head` runs
   automatically. Verify `GET /health/db` → connected.
3. Vercel: import repo → Root Directory `frontend` → env `NEXT_PUBLIC_API_BASE_URL` = Render URL →
   deploy. Put the resulting Vercel domain into Render's `CORS_ORIGINS` and redeploy the backend.
4. Migrate existing data (optional but recommended so the hand-built board carries over):
   `pg_dump` the local DB → `pg_restore`/`psql` into Neon. Exact commands included.
5. First data load: run the Render cron once manually (or `POST /sync/league` with the Bearer token),
   then `POST /sync/ages`.
6. Share the Vercel URL + the shared password with the co-manager.
7. Maintenance notes: ESPN cookies (`ESPN_S2`/`SWID`) expire ~yearly — re-paste in Render env and
   redeploy when sync starts 401ing; Render free web services sleep after ~15 min idle (first request
   ~50s) — mention the paid option to keep warm for draft day.

## Constraints
- NO database migration (auth is env-based; no user table). Reuse Settings/env patterns; don't hardcode
  URLs or secrets. The auth dependency must be a pass-through when `APP_ACCESS_TOKEN` is unset so the
  existing suite and local dev are unchanged. `api.ts` stays the single network chokepoint.
- Don't weaken anything else: the frozen `/master/board`, the draft endpoints, etc. are untouched
  except for sitting behind the (optional) auth dependency.

## Acceptance criteria
1. `uv run pytest -q` green (existing + new auth/CORS tests); `make lint` clean; `alembic heads` single
   (no new migration). With `APP_ACCESS_TOKEN` unset the suite behaves exactly as before.
2. `npm test` + `npm run build` + `npm run lint` green.
3. With `APP_ACCESS_TOKEN` set: protected routes 401 without a valid Bearer token and 200 with it;
   `/health` and `/health/db` stay open.
4. `CORS_ORIGINS` parses a comma-separated env string; the frontend attaches the token through
   `request()` and shows a login gate on 401.
5. `backend/Dockerfile`, `render.yaml` (web + preDeploy migrate + daily sync cron), and `docs/DEPLOY.md`
   exist and are internally consistent (env keys match Settings).

## Report back with
- File tree (backend auth dep + config validator + Dockerfile + tests; frontend gate + api.ts + tests;
  render.yaml; DEPLOY.md; .env.example). Confirm STAGED not committed on `task-24-deploy`; single
  alembic head; four checks green.
- Show the auth dependency's exempt list and the "unset = open" logic; the `CORS_ORIGINS` validator; and
  the render.yaml service + cron blocks.
- Decisions/deviations (e.g. Bearer vs a custom header, where the gate lives in the component tree).

STAGE all changes (`git add -A`) but DO NOT COMMIT — Misha commits/merges/pushes himself.
