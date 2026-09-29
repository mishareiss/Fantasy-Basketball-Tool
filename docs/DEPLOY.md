# Deploying: Neon + Render + Vercel

One backend, one database, one shared password, and the co-manager can open the board on a
phone. **All state lives in Postgres** — the master board's hand-built order, the tier cuts, the
draft and its picks, every import — so the moment both of you point at one backend and one
database, you are looking at the same thing. There is nothing to sync between you.

Three services, and they are free:

| Piece | Where | What it is |
| --- | --- | --- |
| Postgres | **Neon** | The database. Everything above is in here. |
| FastAPI | **Render** | `backend/`, from `backend/Dockerfile`, declared in `render.yaml`. Plus the nightly ESPN sync. |
| Next.js | **Vercel** | `frontend/`, auto-detected. |

Neon rather than Render's own Postgres because Render's free database expires after a month and
takes the board with it. Render rather than Vercel for the backend because this is a long-lived
container that runs migrations and a cron job, not a set of serverless functions.

**Do the steps in order.** Steps 2 and 3 are mutually dependent — the backend needs the Vercel
URL and the frontend needs the Render URL — so step 3 ends by sending you back to finish step 2.

---

## 0. Before you start

Push the branch this work is on, and know which branch Render and Vercel should build (`main`
below). Have your ESPN `espn_s2` / `SWID` cookies to hand — the same two values that are in your
local `.env`.

Invent the shared password now, so you can paste it in two places without inventing it twice:

```sh
openssl rand -hex 24
```

Keep it **ASCII**. An HTTP header can only carry bytes, so a password with an accent in it is
one no browser can ever send — the backend would refuse it forever.

---

## 1. Neon — the database

1. [neon.tech](https://neon.tech) → sign in → **New Project**. Any name; pick the region
   closest to you, and use the same region for Render in step 2.
2. On the connection-string panel, make sure **Pooled connection** is selected, and copy it. It
   looks like:

   ```
   postgresql://USER:PASSWORD@ep-something-123456-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require
   ```

   **Pooled** (the hostname contains `-pooler`) is the one to use: a web service and a cron job
   opening connections against the direct endpoint will exhaust it.

3. Change the scheme to the driver SQLAlchemy expects, and keep everything else exactly as
   Neon gave it to you — `?sslmode=require` included, which is how psycopg is told to use TLS:

   ```
   postgresql+psycopg://USER:PASSWORD@ep-something-123456-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require
   ```

That string is `DATABASE_URL`. Neon is ordinary Postgres 16+ and is perfectly happy with the
`postgresql+psycopg://` prefix — it is SQLAlchemy's way of naming the driver, and never reaches
the server.

> Neon's free tier suspends an idle database and wakes it on the next connection (a second or
> two). The first page load after a quiet morning is slow; nothing is lost.

---

## 2. Render — the backend

1. [render.com](https://render.com) → sign in with GitHub → **New** → **Blueprint**.
2. Pick this repository and the branch to deploy. Render reads `render.yaml` and offers two
   services: the web service **fbb-backend** and the cron job **fbb-sync**.
3. It will then ask for every value the blueprint declares (they are all `sync: false` — keys in
   git, values only ever in the dashboard). Paste:

   | Key | Value |
   | --- | --- |
   | `DATABASE_URL` | The pooled Neon string from step 1 — for **both** services. |
   | `APP_ACCESS_TOKEN` | The password you generated in step 0. Web service only. |
   | `CORS_ORIGINS` | You don't know it yet. Put `https://localhost` for now and fix it in step 3. |
   | `ESPN_S2`, `SWID` | The two cookies, for **both** services. |
   | `ESPN_LEAGUE_ID`, `ESPN_SEASON` | e.g. `123456789` and `2027`, for **both** services. |
   | `BALLDONTLIE_API_KEY`, `THE_ODDS_API_KEY` | Leave blank. Nothing reads them yet. |

4. **Apply**. The first build takes a few minutes. When it finishes, Render runs
   `alembic upgrade head` as its pre-deploy step — that is what creates every table in the
   empty Neon database — and only then starts serving. A failed migration fails the deploy
   rather than starting a backend against a half-built schema.
5. Copy the service URL: `https://fbb-backend-XXXX.onrender.com`. Check it:

   ```sh
   curl https://fbb-backend-XXXX.onrender.com/health          # {"status":"ok"}
   curl https://fbb-backend-XXXX.onrender.com/health/db       # {"status":"ok","database":"connected"}
   ```

   Both answer **without** the password, by design: a health check that 401s reads as a dead
   service to the thing polling it. Everything else is gated — confirm that too:

   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' https://fbb-backend-XXXX.onrender.com/master/board
   #   -> 401

   curl -s -o /dev/null -w '%{http_code}\n' \
     -H "Authorization: Bearer YOUR_APP_ACCESS_TOKEN" \
     https://fbb-backend-XXXX.onrender.com/master/board
   #   -> 200
   ```

   If `/health` is fine but `/health/db` says `unreachable`, it is the connection string: check
   the `postgresql+psycopg://` prefix and that `?sslmode=require` survived the paste.

---

## 3. Vercel — the frontend

1. [vercel.com](https://vercel.com) → **Add New** → **Project** → import this repository.
2. Set **Root Directory** to `frontend`. This is the only setting that matters and the only one
   easy to miss: from the repo root Vercel sees no Next.js app. Everything else it detects.
3. Add one environment variable, for all environments:

   | Key | Value |
   | --- | --- |
   | `NEXT_PUBLIC_API_BASE_URL` | `https://fbb-backend-XXXX.onrender.com` — no trailing slash |

   `NEXT_PUBLIC_` is not decoration: it is what makes the value reach the browser, which is
   where the calls are made from. It is baked in at **build** time, so changing it later needs
   a redeploy, not a restart.

4. **Deploy**, and copy the resulting domain: `https://your-project.vercel.app`.
5. **Go back to Render** → fbb-backend → Environment → set

   ```
   CORS_ORIGINS=https://your-project.vercel.app
   ```

   and save, which redeploys. Scheme and host only — no trailing slash, no path. If you also
   want preview deployments to work, add them comma-separated:
   `https://your-project.vercel.app,https://your-project-git-main-you.vercel.app`.

6. Open the Vercel URL. The first board read comes back 401, the login screen appears, you type
   the password once, and it is remembered in that browser.

If the page instead shows *"Could not reach the API at …"*, it is CORS or the URL: the footer
strip prints the base URL the frontend is actually using, and the browser console will name a
blocked origin explicitly.

---

## 4. Carry the existing board over (recommended)

Skip this and you start from an empty database: the sync in step 5 rebuilds the player pool,
projections and ADP from ESPN, but **the master board's hand-arranged order, your tier cuts,
tags and notes are yours and exist nowhere else**. Move them.

With the local stack up (`make db-up`), dump the local database and restore it into Neon:

```sh
# 1. Dump local (the Docker Postgres on port 5433; adjust if you changed POSTGRES_PORT).
pg_dump --no-owner --no-privileges --clean --if-exists \
  -d 'postgresql://fbb:fbb@localhost:5433/fbb' -Fc -f fbb.dump

# 2. Restore into Neon. Use the URL from step 1 WITHOUT the `+psycopg` — that prefix is
#    SQLAlchemy's, and pg_restore doesn't understand it.
pg_restore --no-owner --no-privileges --clean --if-exists -d \
  'postgresql://USER:PASSWORD@ep-something-123456-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require' \
  fbb.dump
```

The dump includes Alembic's `alembic_version` table, so the schema arrives already stamped at
the same revision the code expects and the next deploy's `alembic upgrade head` is a no-op.

Notes:

* `pg_dump` must be **at least** the server's version. `pg_dump --version`; on macOS,
  `brew install postgresql@16` if it is older than the Neon server.
* `--clean --if-exists` drops the tables it is about to restore, so re-running this is safe.
  It also means it **replaces** whatever is in Neon — do it before anyone starts using the
  deployed board, not after.
* `fbb.dump` is a full copy of the database, our board included. `*.dump` is gitignored, so it
  cannot be committed by accident — delete it when you are done anyway.

Then confirm the board survived:

```sh
curl -s -H "Authorization: Bearer YOUR_APP_ACCESS_TOKEN" \
  'https://fbb-backend-XXXX.onrender.com/master/board?horizon=dynasty' | head -c 300
```

---

## 5. First data load

Skip this if you did step 4 — the data came with the dump. From an empty database, either:

* **Render dashboard** → fbb-sync → **Trigger Run**. It runs
  `python -m scripts.sync_league && python -m scripts.sync_ages`, which is exactly `make sync`
  followed by `make sync-ages`. This is the easier option: it talks to the database directly, so
  there is no token and no timeout to worry about. Watch the log — the league sync prints the
  scoring rules it found and the counts it wrote.

* **Or over HTTP**, with the password:

  ```sh
  curl -X POST -H "Authorization: Bearer YOUR_APP_ACCESS_TOKEN" \
    https://fbb-backend-XXXX.onrender.com/sync/league
  curl -X POST -H "Authorization: Bearer YOUR_APP_ACCESS_TOKEN" \
    https://fbb-backend-XXXX.onrender.com/sync/ages
  ```

  The age sync walks nba.com player by player and takes a few minutes; on the free plan it may
  outlive the request. The cron job has no such limit, which is why it is the recommendation.

Both are idempotent — run them again whenever. From here the cron job repeats them nightly at
09:20 UTC.

CSV imports (`make import`) stay local: they read a file off your disk. Point your local
`.env`'s `DATABASE_URL` at Neon to import straight into the deployed database, or use the
**Import** page in the deployed frontend, which pastes the same table over HTTP.

---

## 6. Hand it over

Send the co-manager two things: the Vercel URL, and the password. Nothing else — no account,
no invite. They open the link, type the password once, and they are on the same board you are.

---

## 7. Maintenance

**The ESPN cookies expire, roughly yearly.** The symptom is the nightly `fbb-sync` job failing
with an ESPN credentials error while everything else keeps working — the board is in the
database and doesn't care. Fix: log in to `fantasy.espn.com` in a browser, copy the fresh
`espn_s2` and `SWID`, paste them into **both** Render services' environments, save. Turn on the
failure email for the cron job so you find out then rather than in October.

**A new season.** Bump `ESPN_SEASON` (ESPN labels a season by the year it ends: `2027` is
2026-27) on both services, and trigger `fbb-sync` once by hand rather than waiting for the
night.

**The free web service sleeps after ~15 minutes idle**, and the next request pays ~50 seconds
waking it. Fine for browsing on a Tuesday; not fine with a draft clock running. **Before draft
day, put fbb-backend on Render's cheapest paid instance** — it never sleeps — and move it back
afterwards if you like. Neon's free database also suspends when idle, but wakes in a second or
two, which nobody notices.

**Deploying a change.** Push to the branch both services are watching; Render rebuilds and runs
`alembic upgrade head` before serving, and Vercel rebuilds the frontend. A migration that fails
fails the deploy and leaves the previous version running.

**Changing the shared password.** Set a new `APP_ACCESS_TOKEN` in Render and save. Everyone's
stored password stops working at the next call: they get a 401, the login screen comes back, and
they type the new one. Nothing else to do.

---

## What "auth" here is, and is not

One token in `APP_ACCESS_TOKEN`, compared in constant time against
`Authorization: Bearer <token>` on every request (`backend/app/auth.py`). The frontend attaches
it in the one `request()` in `frontend/lib/api.ts` and keeps it in `localStorage`.

* **Unset means off.** No `APP_ACCESS_TOKEN`, no gate — which is why local development and the
  test suite need no password. It also means *a deployment with the variable missing is an open,
  writable API on a public URL*. Setting it is step 2 for a reason.
* `/`, `/health` and `/health/db` answer without it, always (`app/auth.py`: `OPEN_PATHS`), so
  Render's health check and the frontend's status strip work before anyone has signed in.
* `/docs` and `/openapi.json` are also open — they describe the API's shape and carry no league
  data, but the endpoints they document are all gated.
* It is a **shared password, not a login**. It says someone knows the password; it says nothing
  about which of you it is. There is no user table, no session, no audit trail, and every page
  is the same page for both of you. That is the intended design for a two-person tool, and it
  is the wrong design for anything else.
