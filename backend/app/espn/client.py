"""ESPN league access: cookie auth, the `espn-api` League, and raw v3 view requests.

Two ways in, on purpose:

* `ESPNClient.league` — the `espn-api` wrapper, for everything it models well (teams,
  rosters, draft, box scores).
* `ESPNClient.fetch_view` — a thin httpx call against the v3 endpoint, for views the library
  either doesn't surface (`mSettings`' per-stat coefficients) or caps too aggressively
  (`kona_player_info`, where `League.free_agents` is free-agent-only and size-limited).
"""

import json
from dataclasses import dataclass
from typing import Any

import httpx
from espn_api.basketball import League

from app.config import Settings, get_settings

# Same host espn-api uses; kept here so the raw helper doesn't reach into the library's internals.
ESPN_V3_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba"

DEFAULT_TIMEOUT = 60.0


class ESPNCredentialsError(RuntimeError):
    """Required ESPN settings are missing. Raised at sync time, never at import or boot."""


class ESPNRequestError(RuntimeError):
    """ESPN answered with something we can't use."""


@dataclass(frozen=True)
class ESPNCredentials:
    """The four values needed to read our private league."""

    league_id: int
    season: int
    espn_s2: str
    swid: str

    @property
    def cookies(self) -> dict[str, str]:
        return {"espn_s2": self.espn_s2, "SWID": self.swid}

    @property
    def league_url(self) -> str:
        return f"{ESPN_V3_BASE}/seasons/{self.season}/segments/0/leagues/{self.league_id}"

    @property
    def season_url(self) -> str:
        return f"{ESPN_V3_BASE}/seasons/{self.season}"


def _normalise_swid(swid: str) -> str:
    """ESPN wants the SWID cookie wrapped in braces; pasted values sometimes lose them."""
    swid = swid.strip()
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    return swid


def require_credentials(settings: Settings | None = None) -> ESPNCredentials:
    """Read ESPN credentials from settings, or explain exactly which ones are missing.

    The app boots fine without cookies — only a live sync needs them — so this is called at
    sync time rather than at startup.
    """
    settings = settings or get_settings()
    league_id, season = settings.espn_league_id, settings.espn_season
    espn_s2, swid = settings.espn_s2, settings.swid

    missing = [
        env_name
        for env_name, value in (
            ("ESPN_LEAGUE_ID", league_id),
            ("ESPN_SEASON", season),
            ("ESPN_S2", espn_s2),
            ("SWID", swid),
        )
        if value in (None, "")
    ]
    if missing:
        raise ESPNCredentialsError(
            "Missing ESPN credentials: "
            + ", ".join(missing)
            + ". Copy the espn_s2/SWID cookies from a logged-in fantasy.espn.com session into "
            "the repo-root .env (see .env.example)."
        )

    return ESPNCredentials(
        league_id=int(league_id),  # type: ignore[arg-type]  # non-None per the check above
        season=int(season),  # type: ignore[arg-type]
        espn_s2=str(espn_s2).strip(),
        swid=_normalise_swid(str(swid)),
    )


def credentials_available(settings: Settings | None = None) -> bool:
    """True when a live pull is possible. Used to skip live tests, not to gate errors."""
    try:
        require_credentials(settings)
    except ESPNCredentialsError:
        return False
    return True


class ESPNClient:
    """Authenticated access to one ESPN league season."""

    def __init__(self, credentials: ESPNCredentials, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.credentials = credentials
        self.timeout = timeout
        self._league: League | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ESPNClient":
        """Build a client from Settings, raising `ESPNCredentialsError` if cookies are missing."""
        return cls(require_credentials(settings))

    @property
    def league_id(self) -> int:
        return self.credentials.league_id

    @property
    def season(self) -> int:
        return self.credentials.season

    @property
    def league(self) -> League:
        """The `espn-api` League, built once per client and reused.

        Constructing it makes several ESPN calls, so it stays lazy: the settings and player
        views below don't need it.
        """
        if self._league is None:
            self._league = League(
                league_id=self.credentials.league_id,
                year=self.credentials.season,
                espn_s2=self.credentials.espn_s2,
                swid=self.credentials.swid,
            )
        return self._league

    def fetch_view(
        self,
        view: str | list[str],
        *,
        params: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        path: str = "",
        season_scoped: bool = False,
    ) -> Any:
        """GET one v3 view with our cookies attached.

        `filters` becomes the `x-fantasy-filter` header, which is how ESPN takes paging and
        status filters. `season_scoped` targets the season endpoint (e.g. `/players`) instead
        of the league one.
        """
        base = self.credentials.season_url if season_scoped else self.credentials.league_url
        headers = {"x-fantasy-filter": json.dumps(filters)} if filters else None

        try:
            response = httpx.get(
                base + path,
                params={"view": view, **(params or {})},
                cookies=self.credentials.cookies,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ESPNRequestError(f"ESPN request failed for view {view!r}: {exc}") from exc

        if response.status_code in (401, 403):
            raise ESPNCredentialsError(
                f"ESPN rejected our cookies (HTTP {response.status_code}) for league "
                f"{self.credentials.league_id}. They expire periodically — re-copy espn_s2 "
                "and SWID from the browser into .env."
            )
        if response.status_code == 404:
            raise ESPNRequestError(
                f"ESPN has no league {self.credentials.league_id} for season "
                f"{self.credentials.season}"
            )
        if response.status_code != 200:
            raise ESPNRequestError(f"ESPN returned HTTP {response.status_code} for view {view!r}")

        payload = response.json()
        # The league-history variant of the endpoint wraps the same object in a list.
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]
        return payload

    def fetch_settings_view(self) -> dict[str, Any]:
        """Raw `?view=mSettings` body — the source of our custom scoring coefficients."""
        payload = self.fetch_view("mSettings")
        if not isinstance(payload, dict) or "settings" not in payload:
            raise ESPNRequestError("mSettings response did not contain a `settings` object")
        return payload

    def fetch_player_pool_pages(
        self,
        *,
        page_size: int = 500,
        max_players: int = 5000,
        statuses: tuple[str, ...] = ("FREEAGENT", "WAIVERS", "ONTEAM"),
    ) -> list[dict[str, Any]]:
        """Every player ESPN will show for this league: rostered, waivers, and free agents.

        `kona_player_info` pages through `x-fantasy-filter`; ESPN stops returning rows past the
        end, which is our termination signal. `max_players` is a safety stop so a filter
        mistake can't loop forever.
        """
        entries: list[dict[str, Any]] = []
        offset = 0

        while offset < max_players:
            filters = {
                "players": {
                    "filterStatus": {"value": list(statuses)},
                    "limit": page_size,
                    "offset": offset,
                    "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                }
            }
            payload = self.fetch_view(
                "kona_player_info",
                params={"scoringPeriodId": 0},
                filters=filters,
            )
            page = payload.get("players", []) if isinstance(payload, dict) else []
            if not page:
                break
            entries.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

        return entries
