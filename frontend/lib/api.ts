/**
 * Typed client for the FastAPI backend.
 *
 * Every call goes through `request`, so auth headers, error shaping, and base-URL
 * handling stay in one place as the API grows.
 *
 * The response types below MIRROR the backend's pydantic models — `BoardResponse` /
 * `BoardRow` / `TierSummaryRow` from app/api/players.py, `CurveResponse` / `TiersResponse`
 * from app/api/valuation.py, `ImportResponse` / `RowOutcomeResponse` / `KindInfo` from
 * app/api/imports.py. Field names and nullability are copied, not invented: if the backend
 * renames a field, the compiler is supposed to notice.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type HealthResponse = {
  status: string;
};

export type DbHealthResponse = {
  status: string;
  database?: string;
  detail?: string;
};

export type ServiceInfo = {
  name: string;
  version: string;
  docs: string;
};

/** The two value horizons the board can be ranked by (players.py: HORIZONS). */
export type Horizon = "current_year" | "dynasty";
export const HORIZONS = ["current_year", "dynasty"] as const;

/** Whether the board cuts itself into tiers (players.py: TIER_MODES). */
export type TiersMode = "auto" | "off";

/** The positions the board can be filtered to. `null` here means "All". */
export const POSITIONS = ["PG", "SG", "SF", "PF", "C"] as const;
export type Position = (typeof POSITIONS)[number];

/** One player's line on the board — app/api/players.py: BoardRow. */
export type BoardRow = {
  rank: number;
  espn_player_id: number;
  name: string;
  nba_team: string | null;
  positions: string[];
  /** Whole years old at the response's `age_as_of`. Null when we hold no birthdate. */
  age: number | null;
  fantasy_points_per_game: number;
  fantasy_points_total: number;
  projected_games: number | null;
  per_game_basis: string;
  /** The market's redraft ADP from `adp_source`. Null when that source has no read. */
  adp: number | null;
  auction_value: number | null;
  percent_owned: number | null;
  current_year_value: number;
  dynasty_value: number;
  age_multiplier: number;
  /**
   * False when the multiplier is 1.0 because we had no birthdate to adjust with — NOT
   * because the curve judged him to be in his prime. Same number, opposite meanings.
   */
  age_adjusted: boolean;
  /** Tier on the OVERALL board, 1 being the top. Null = below the tiered pool, or tiers off. */
  tier: number | null;
};

/** One tier on the board — app/api/players.py: TierSummaryRow. */
export type TierSummaryRow = {
  tier: number;
  size: number;
  value_high: number;
  value_low: number;
  start_rank: number;
  gap: number | null;
  gap_ratio: number | null;
};

/** A ranked slice of the board, plus what it was built from — players.py: BoardResponse. */
export type BoardResponse = {
  source: string;
  kind: string;
  season: number;
  adp_source: string;
  adp_season: number | null;
  total_ranked: number;
  position: string | null;
  horizon: Horizon;
  /** ISO date (YYYY-MM-DD) every `age` on this board was computed at. */
  age_as_of: string;
  tiers: TiersMode;
  tier_pool: number;
  /** Describes the OVERALL board, not this filtered page. */
  tier_summary: TierSummaryRow[];
  players: BoardRow[];
};

export type BoardParams = {
  horizon?: Horizon;
  position?: Position | null;
  source?: string;
  season?: number;
  adp_source?: string;
  adp_season?: number;
  limit?: number;
  tiers?: TiersMode;
};

/** The five age-curve tunables — app/api/valuation.py: CurveParams. */
export type CurveParams = {
  prime_start: number;
  prime_end: number;
  youth_bonus_per_year: number;
  decline_per_year: number;
  min_multiplier: number;
};

/** One age and what dynasty value multiplies by at it — valuation.py: CurveSampleRow. */
export type CurveSampleRow = {
  age: number;
  multiplier: number;
  /** 'youth' | 'prime' | 'decline' | 'floor' — the shape, in words. */
  band: string;
};

export type CurveResponse = {
  params: CurveParams;
  /** Parameter name -> the env var that moves it. */
  env_vars: Record<string, string>;
  sample_min_age: number;
  sample_max_age: number;
  sample: CurveSampleRow[];
};

/** The four tiering tunables — app/api/valuation.py: TierParamsModel. */
export type TierParams = {
  gap_multiple: number;
  min_size: number;
  max_tiers: number;
  pool: number;
};

/** One tier with the arithmetic shown — app/api/valuation.py: TierRow. */
export type TierRow = {
  tier: number;
  size: number;
  value_high: number;
  value_low: number;
  start_rank: number;
  gap: number | null;
  gap_ratio: number | null;
  /** The best player in the tier. */
  leader: string | null;
};

export type TiersResponse = {
  horizon: Horizon;
  source: string;
  season: number;
  params: TierParams;
  env_vars: Record<string, string>;
  typical_gap: number;
  break_threshold: number;
  pool_size: number;
  total_ranked: number;
  tiers: TierRow[];
};

/* -------------------------------------------------------------------------------------- *
 * Imports — app/api/imports.py, app/ingest, and the alias escape hatch in app/api/players.py
 * -------------------------------------------------------------------------------------- */

/** One import kind, built or planned — app/api/imports.py: KindInfo. */
export type ImportKindInfo = {
  kind: string;
  label: string;
  /** False for a kind that is designed but not built; `label` says what it is waiting on. */
  implemented: boolean;
  /** field -> the header aliases that find it, e.g. `{ adp: ["adp", "avg pick", ...] }`. */
  value_columns: Record<string, string[]>;
  /** Fields a row must carry a value in, or it comes back `invalid`. */
  required: string[];
};

/**
 * A player a source name could be — app/matching/matcher.py: `MatchCandidate.as_dict()`.
 *
 * The backend declares `candidates: list[dict]` rather than a model, so this is the one type
 * here that mirrors a serializer instead of a pydantic class. Every entry comes from that one
 * `as_dict`, so the shape is exact even though FastAPI doesn't publish it.
 */
export type MatchCandidate = {
  player_id: number;
  full_name: string;
  nba_team: string | null;
  /** 0..1, how well the name scored. 1.0 for an alias or an exact hit. */
  score: number;
};

/** Where a parsed row ended up — app/ingest/pipeline.py: the STATUS_* constants. */
export const IMPORT_STATUSES = [
  "matched",
  "review",
  "unmatched",
  "duplicate",
  "invalid",
] as const;
export type ImportRowStatus = (typeof IMPORT_STATUSES)[number];

/** One row of the file and what became of it — imports.py: RowOutcomeResponse. */
export type ImportRowOutcome = {
  line: number;
  source_name: string;
  /** One of IMPORT_STATUSES. Typed as the backend types it (`str`), so a status we don't
      know about yet renders as itself rather than failing to compile. */
  status: string;
  values: Record<string, number | string | null>;
  team: string | null;
  positions: string[];
  player_id: number | null;
  player_name: string | null;
  confidence: number;
  /** 'alias' | 'exact' | 'normalized' | 'fuzzy' | 'ambiguous' | 'unmatched' | ''. */
  method: string;
  candidates: MatchCandidate[];
  note: string | null;
};

/** The preview or the receipt — imports.py: ImportResponse. `dry_run` says which. */
export type ImportResponse = {
  kind: string;
  source: string;
  season: number;
  dry_run: boolean;
  options: Record<string, string>;

  /** field -> the header it was detected under. The one mis-detection a row list can't show. */
  columns: Record<string, string>;
  delimiter: string;

  rows_parsed: number;
  rows_skipped_blank: number;

  matched: number;
  review: number;
  unmatched: number;
  duplicate: number;
  invalid: number;

  aliases_created: number;
  aliases_existing: number;

  rows_created: number;
  rows_updated: number;
  rows_unchanged: number;
  /** Whatever the handler wanted to say that the counters can't. Free text. */
  notes: string[];

  rows: ImportRowOutcome[];
};

/** The body of POST /import/{kind} — imports.py: ImportRequest, minus `dry_run`.
 *  The two callers below set that, so a preview can never be sent as a commit by accident. */
export type ImportRequestBody = {
  source: string;
  text: string;
  season?: number | null;
  column_map?: Record<string, string> | null;
  delimiter?: string | null;
  options?: Record<string, string> | null;
  strict?: boolean;
};

/** The `basis` option a projection import takes — app/ingest/projection.py: BASES. */
export const PROJECTION_BASES = ["per_game", "season"] as const;
export type ProjectionBasis = (typeof PROJECTION_BASES)[number];

/**
 * The `horizon` option a ranking import REQUIRES — app/db/models/ranking.py.
 *
 * Deliberately not the board's `Horizon` above: that one names a computed lens over
 * production, this one names what a rank-only list already is. A ranking has no stats to
 * age-adjust, so it declares its horizon at import; a projection derives both from the curve.
 */
export const RANKING_HORIZONS = ["dynasty", "redraft"] as const;
export type RankingHorizon = (typeof RANKING_HORIZONS)[number];

/** POST /players/{id}/aliases — app/api/players.py: AliasRequest. */
export type AliasRequestBody = {
  source: string;
  source_name: string;
  source_id?: string | null;
};

/** app/api/players.py: AliasResponse. */
export type AliasResponse = {
  espn_player_id: number;
  name: string;
  source: string;
  source_name: string;
  source_id: string | null;
  confidence: number | null;
  match_method: string | null;
  /** False when the alias already existed — resolving the same row twice is a no-op. */
  created: boolean;
  /** ISO date, when the alias immediately gave us one. */
  birthdate: string | null;
  age: number | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    /** FastAPI's `detail`, when the error body carried one. */
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Build a query string, dropping anything the caller left undefined or null. */
function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      ...init,
    });
  } catch {
    // Network-level failure: backend not running, wrong port, or CORS rejection.
    throw new ApiError(`Could not reach the API at ${API_BASE_URL}${path}`);
  }

  if (!response.ok) {
    // FastAPI's `detail` is the only human-readable half of an error ("run `make sync`
    // first"), so it is worth carrying up rather than reporting a bare status code.
    let detail: string | undefined;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const value = (body as { detail: unknown }).detail;
        if (typeof value === "string") detail = value;
      }
    } catch {
      // Non-JSON error body: the status code is all we get.
    }
    throw new ApiError(`${path} responded ${response.status}`, response.status, detail);
  }

  return (await response.json()) as T;
}

/** A JSON POST through the same error shaping. */
async function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  info: () => request<ServiceInfo>("/"),
  health: () => request<HealthResponse>("/health"),
  dbHealth: () => request<DbHealthResponse>("/health/db"),

  /** The ranked, tiered board. 404s when nothing has been synced yet. */
  board: (params: BoardParams = {}) =>
    request<BoardResponse>(
      `/players/board${query({
        horizon: params.horizon,
        position: params.position,
        source: params.source,
        season: params.season,
        adp_source: params.adp_source,
        adp_season: params.adp_season,
        limit: params.limit,
        tiers: params.tiers,
      })}`,
    ),

  /** The active age/longevity curve behind every dynasty value. */
  valuationCurve: () => request<CurveResponse>("/valuation/curve"),

  /** Where the board breaks into tiers, and the gap arithmetic that put them there. */
  valuationTiers: (horizon?: Horizon) =>
    request<TiersResponse>(`/valuation/tiers${query({ horizon })}`),

  /** What can be imported today, and what is designed but not built. */
  importKinds: () => request<ImportKindInfo[]>("/import/kinds"),

  /** Parse, match and report — writes nothing. This is what the preview table renders. */
  importPreview: (kind: string, body: ImportRequestBody) =>
    post<ImportResponse>(`/import/${encodeURIComponent(kind)}`, { ...body, dry_run: true }),

  /** The same run, persisted. Rows held for review are still never written. */
  importCommit: (kind: string, body: ImportRequestBody) =>
    post<ImportResponse>(`/import/${encodeURIComponent(kind)}`, { ...body, dry_run: false }),

  /**
   * Record by hand what the matcher couldn't place: "this source calls our player that".
   *
   * The half of the import loop that makes a review row go away — after this, re-previewing
   * the same file lands that row as `method: 'alias'` at confidence 1.0, for good.
   */
  addPlayerAlias: (espnPlayerId: number, body: AliasRequestBody) =>
    post<AliasResponse>(`/players/${espnPlayerId}/aliases`, body),
};
