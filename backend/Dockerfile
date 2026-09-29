# The backend image, for Render (see render.yaml) and for anything else that runs a container.
#
# Build context is `backend/`, not the repo root: the frontend is Vercel's problem and the
# image has no use for it. That is why every COPY below is a bare path.
#
# Python 3.12 and uv, matching CI (.github/workflows/ci.yml pins UV_PYTHON=3.12 and pyproject
# requires >=3.12,<3.13) — an image that resolves a different interpreter than the tests ran on
# is an image nothing has tested.
FROM python:3.12-slim

# uv from its own published image rather than pip: it is the tool the lockfile belongs to, and
# copying the binary keeps the layer to two files. Pinned, because "whatever uv shipped today"
# is not a reproducible build.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

# No .pyc writing (the image is read-only in practice), unbuffered stdout so Render's log tail
# shows a traceback as it happens, and uv's bytecode compile at build time instead — which is
# paid once here rather than on every cold start.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /srv

# Dependencies before source, so editing a router doesn't re-resolve the whole environment.
# `--frozen` installs exactly what uv.lock says and never re-resolves: a pyproject change that
# wasn't relocked fails the build here rather than quietly deploying different versions than CI
# tested. `--no-install-project` because the app itself arrives in the next layer.
# README.md is not documentation here, it is a build input: pyproject declares
# `readme = "README.md"`, and hatchling refuses to build the wheel without it.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN chmod +x scripts/start.sh

RUN uv sync --frozen --no-dev

# scripts/start.sh runs `alembic upgrade head` then execs uvicorn. The migration lives in the
# start command (not a Render preDeployCommand) because the free tier has no preDeployCommand;
# it is idempotent and the free plan is single-instance, so there is no migration race. Invoked
# as `sh <script>` so no execute bit is required and $PORT is expanded at start time.
CMD ["sh", "/srv/scripts/start.sh"]
