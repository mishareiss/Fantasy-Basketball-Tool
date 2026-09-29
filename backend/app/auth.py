"""The shared-password gate.

This tool has one user account, and it belongs to two people. Every endpoint here can write
— reorder the master board, edit a draft pick, re-import a projection set — so a public URL
without a gate is a public URL anyone can rewrite our board through. What it is NOT is an auth
system: there is no user table, no session, no migration. There is one token in the
environment, and you either present it or you don't.

TWO RULES, and both of them are the reason this is safe to add to a finished app:

* `APP_ACCESS_TOKEN` unset means the gate is OFF. A local checkout, the test suite and CI
  configure no token and every endpoint answers exactly as it did before this module existed.
  The gate is something a deployment turns on, not something development has to work around.
* `OPEN_PATHS` answer with or without a token, at any setting. A health check that 401s reads
  as a dead service to the thing that polls it, and the frontend's status strip has no password
  to send before someone has typed one. Exact paths are enough — Starlette redirects a trailing
  slash to the canonical path before any dependency runs, so `/health/` never arrives here.
"""

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

# Unauthenticated on purpose. Render polls the health check to decide whether the deploy is
# live, and `BackendStatusStrip` probes all three on every page — including the login screen,
# where there is by definition no token yet. None of them read or write league data: `/health`
# is a constant, `/health/db` is a `SELECT 1`, and `/` is the service's name and version.
OPEN_PATHS = frozenset({"/", "/health", "/health/db"})

# `auto_error=False` so a missing header arrives here as None and this module decides what it
# means — which depends on whether a token is configured at all. The scheme is also what puts
# the Authorize box on /docs, so the token can be pasted in there.
_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AppAccessToken",
    description="The shared APP_ACCESS_TOKEN, as `Bearer <token>`.",
)


def require_access_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    """Require `Authorization: Bearer <APP_ACCESS_TOKEN>`, when one is configured.

    Pass-through when it isn't, and pass-through for `OPEN_PATHS` either way. Mounted once, on
    `api_router`, so a new router is behind the gate the moment it is included rather than when
    somebody remembers to decorate it.
    """
    expected = settings.app_access_token
    if not expected:
        return
    if request.url.path in OPEN_PATHS:
        return

    supplied = ""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        supplied = credentials.credentials

    # Constant-time, and on bytes: `compare_digest` refuses non-ASCII str, and the supplied
    # half is whatever a stranger sent. A length mismatch alone is not secret — only the
    # content is — so comparing the encodings directly is the whole of it.
    if not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
