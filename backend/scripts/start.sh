#!/bin/sh
# Container start command for Render (and any other container host).
#
# Migrate, then serve. This lives in a script rather than inline in render.yaml's dockerCommand
# because Render mis-tokenizes an inline `sh -c "... && ..."` (it drops the -c and tries to exec
# the whole line as one program). A script is a single token with no quotes or shell operators
# for the platform to mangle.
#
# `alembic upgrade head` is idempotent (a no-op when already at head). The free plan runs a single
# instance, so there is no migration race. $PORT is Render's and must be honored; default 8000
# for local `docker run`.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
