/**
 * Typed client for the FastAPI backend.
 *
 * Every call goes through `request`, so auth headers, error shaping, and base-URL
 * handling stay in one place as the API grows. The shared-password header is attached
 * there and nowhere else — no call site below knows the gate exists.
 *
 * The response types below MIRROR the backend's pydantic models — `BoardResponse` /
 * `BoardRow` / `TierSummaryRow` from app/api/players.py, `CurveResponse` / `TiersResponse`
 * from app/api/valuation.py, `ImportResponse` / `RowOutcomeResponse` / `KindInfo` from
 * app/api/imports.py. Field names and nullability are copied, not invented: if the backend
 * renames a field, the compiler is supposed to notice.
 */

import { getAccessToken, reportUnauthorized } from "@/lib/auth";

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
 * The multi-source consensus board — app/api/consensus.py, over app/ranking
 * -------------------------------------------------------------------------------------- */

/** The three storage shapes an opinion arrives in — app/ranking/sources.py: KINDS. */
export const SOURCE_KINDS = ["projection", "adp", "ranking"] as const;
export type SourceKind = (typeof SOURCE_KINDS)[number];

/** How the selected sources are averaged — app/ranking/consensus.py: METHODS. */
export const CONSENSUS_METHODS = ["rank", "percentile"] as const;
export type ConsensusMethod = (typeof CONSENSUS_METHODS)[number];

/** One source a board can be built from — app/api/consensus.py: SourceInfo. */
export type SourceInfo = {
  /** The stable handle to select with: 'projection:espn', 'adp:espn', 'ranking:1'. */
  id: string;
  label: string;
  /** One of SOURCE_KINDS. Typed as the backend types it (`str`) so a kind we don't know
      about yet renders as itself rather than failing to compile. */
  kind: string;
  /** The publisher: 'espn', 'Dizzle Dynasty'. */
  source: string;
  season: number | null;
  /**
   * The rank-set tag ('dynasty' | 'redraft') an imported list declared at import — NOT the
   * board's `Horizon`. Null for projection and ADP sources, which derive both board horizons
   * from production instead of declaring one.
   */
  horizon: string | null;
  /** How many of the shared pool this source has an opinion about — read the consensus
      against it: a 449-name list and a 1,095-name one are not the same evidence. */
  player_count: number;
};

/** Everything that can rank players under one horizon — consensus.py: SourcesResponse. */
export type SourcesResponse = {
  horizon: Horizon;
  /** The `RankingSet.horizon` tag this board horizon accepts imported lists from. */
  ranking_horizon: string;
  /** Every player at least one AVAILABLE source ranks — the percentile denominator. */
  pool_size: number;
  sources: SourceInfo[];
};

/** Where one source put one player — consensus.py: ConsensusCell. */
export type ConsensusCell = {
  /** Published for an imported list; a competition rank (ties share a number) otherwise. */
  rank: number;
  /** That rank as a position in the shared pool: 100 at the top, 0 at the bottom. */
  percentile: number;
};

/** One player's line on the consensus board — consensus.py: ConsensusPlayerRow. */
export type ConsensusRow = {
  rank: number;
  espn_player_id: number;
  name: string;
  nba_team: string | null;
  positions: string[];
  age: number | null;
  /** The equal-weight average over the sources that rank him, in the method's units. */
  consensus: number;
  /** source id -> that source's cell. A source with no opinion on him has NO key here. */
  cells: Record<string, ConsensusCell>;
  /** How many of the selected sources rank him. A player missing from a source is left OUT
      of that source's average, never counted last — so a consensus of 8.0 off one source is
      not the claim a consensus of 8.0 off three is. */
  sources_present: number;
  sources_missing: string[];
  /** Disagreement across the sources that do rank him: percentile points, and places. Both
      null below two sources — one source can't disagree with itself. */
  spread: number | null;
  rank_spread: number | null;
};

export type ConsensusResponse = {
  horizon: Horizon;
  ranking_horizon: string;
  method: ConsensusMethod;
  pool_size: number;
  position: string | null;
  total_ranked: number;
  /** ISO date (YYYY-MM-DD) every `age` on this board was computed at. */
  age_as_of: string;
  /** The SELECTED sources, in the order asked for — the column order. */
  sources: SourceInfo[];
  players: ConsensusRow[];
};

export type ConsensusParams = {
  horizon?: Horizon;
  /** Source ids from GET /sources. Omitted entirely means "all of them". */
  sources?: string[];
  method?: ConsensusMethod;
  position?: Position | null;
  limit?: number;
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

/* -------------------------------------------------------------------------------------- *
 * Market lines — app/api/market.py, over app/ingest/market_line
 *
 * The one source that is kept by hand rather than imported and replaced: a player's props
 * are a standing SET, edited one number at a time, so they get CRUD instead of a paste.
 * -------------------------------------------------------------------------------------- */

/** A stat a line can be entered on — market.py: MarketStat. The LEAGUE's scored counting
 *  stats: a line on something we don't score derives nothing, and a rate can't be priced. */
export type MarketStat = {
  stat_id: number;
  /** 'PTS', 'AST' — the name the line is stored under. */
  name: string;
  label: string;
  /** What our scoring pays per unit of it. */
  points: number;
};

/** One stored line — market.py: MarketLineRow. */
export type MarketLineRow = {
  id: number;
  player_id: number;
  stat_id: number;
  stat: string;
  /** Per game, always — season-long props are quoted that way. */
  line: number;
  /** American odds; null for a side nobody priced. A line with no price derives itself. */
  over_odds: number | null;
  under_odds: number | null;
  /** ISO timestamp: when these values last changed, not when we last looked. */
  as_of: string;
};

/** One player's whole set of lines and what they derive to — market.py: MarketPlayer. */
export type MarketPlayer = {
  espn_player_id: number;
  name: string;
  nba_team: string | null;
  positions: string[];
  age: number | null;
  lines: MarketLineRow[];
  /**
   * The derived market projection — the row the consensus board actually reads. Null when he
   * has no lines left, which is NOT the same claim as zero: the market saying nothing about
   * a player and the market rating him at nothing are different facts.
   */
  fantasy_points_per_game: number | null;
  fantasy_points_total: number | null;
  projected_games: number | null;
  /** How many stats the value is built from. Partial by construction. */
  stats_priced: number;
};

/** Every line for one (source, season), grouped by player — market.py: MarketLinesResponse. */
export type MarketLinesResponse = {
  source: string;
  season: number;
  /** Empty before a league sync has stored our coefficients — nothing can be priced yet. */
  stats: MarketStat[];
  total_players: number;
  total_lines: number;
  players: MarketPlayer[];
};

/** The body of PUT /market/lines — market.py: MarketLineWrite. */
export type MarketLineWriteBody = {
  source: string;
  season?: number | null;
  player_id: number;
  /** Any spelling the importer accepts: 'AST', 'assists', 'apg', or the bare stat id. */
  stat: string;
  line: number;
  over_odds?: number | null;
  under_odds?: number | null;
};

/** market.py: MarketLineWriteResponse. */
export type MarketLineWriteResponse = {
  source: string;
  season: number;
  /** False when the line already existed and was moved in place. */
  created: boolean;
  line: MarketLineRow;
  /** The player as he is NOW: every line of his, and the re-derived value. */
  player: MarketPlayer;
};

/** market.py: MarketDeleteResponse. */
export type MarketDeleteResponse = {
  source: string;
  season: number;
  deleted: number;
  player: MarketPlayer;
  /**
   * True when that was his last line: the derived projection was REMOVED, so he is gone from
   * this source and off the consensus board rather than ranked last on it.
   */
  player_removed: boolean;
};

/* -------------------------------------------------------------------------------------- *
 * The master ranking — app/api/master.py, over app/ranking/master
 *
 * Our own board, and the one thing that makes it different from every other list here: the
 * order is STORED. Nothing that lands in the sources moves a player on it. What moves is the
 * reference beside him — `consensus_rank` and `delta` — which is the number the board is for.
 * -------------------------------------------------------------------------------------- */

/** What a player can be to us beyond his place — app/db/models/master_rank.py: MASTER_TAGS. */
export const MASTER_TAGS = ["target", "fade"] as const;
export type MasterTag = (typeof MASTER_TAGS)[number];

/** One player on our board — master.py: MasterPlayerRow. */
export type MasterPlayerRow = {
  /** OUR place, 1-based and contiguous. Null only for a player in `set_aside`. */
  rank: number | null;
  espn_player_id: number;
  name: string;
  nba_team: string | null;
  positions: string[];
  age: number | null;
  /** One of MASTER_TAGS, or null. Typed as the backend types it (`str`) so a tag added
      server-side renders as itself rather than failing to compile. */
  tag: string | null;
  note: string | null;
  excluded: boolean;
  /** Placed into the order by THIS response — a new arrival, or one just restored from the
      tray. A fact about the response, not a stored bit: it is gone on the next GET. */
  is_new: boolean;
  /** He has an entry but no source ranks him any more. His rank stands; nothing backs it. */
  is_stale: boolean;
  /** His place on the consensus of every available source, under the requested horizon. */
  consensus_rank: number | null;
  /**
   * `rank - consensus_rank`, so a player we have ABOVE the field carries a NEGATIVE number
   * (our 1 against their 4 is -3 — see the assertion in backend/tests/test_api_master.py).
   * The board prints the gap the other way up, as spots-above-the-field; `edge()` in
   * lib/masterboard.ts is the one place that flip happens.
   */
  delta: number | null;
  /**
   * Which band of the board he is in, 1 being the top. Null only for a set-aside player: a
   * tier here is a band over the RANKS, and he hasn't got one. Never null for a ranked
   * player — the bands cover the whole order, so the man below the last divider is in the
   * bottom tier rather than untiered.
   */
  overall_tier: number | null;
  /** His tier among the players at `position_scope`. Null when we hold no position for him,
      or when that position has too few players on the board to tier at all. */
  position_tier: number | null;
  /** Which position `position_tier` counts in — the requested `?position=` when there is one,
      otherwise the first position he is listed at. Typed as the backend types it (`str`). */
  position_scope: string | null;

  /* --- two production numbers beside the reference rank ---------------------------------- */
  /**
   * What LAST SEASON was worth per game under THIS league's scoring — production that
   * happened, not a forecast. Null for a player who has never completed a season we hold, and
   * that is a different claim from zero: the board prints an em dash for him rather than
   * putting him last. `api.playerDetail` expands it into the box score.
   */
  last_year_fantasy_ppg: number | null;
  /**
   * The sportsbook-derived projection's fantasy points per game (`projection:market`). Null
   * when nobody has posted a prop on him, and PARTIAL when they have posted only some — it is
   * built from the stats that happen to be priced, so it reads low for a player with one
   * line. A reference column, never a ranking.
   */
  market_fantasy_ppg: number | null;

  /* --- the live draft, and ONLY when the request asked about it (`draft_mode`) ----------
     All three are annotation, not stored state: they are false/null on a plain board read,
     false/null when no draft exists, and false/null on every WRITE response — the flags ride
     on `GET /master/board` alone, which is why the page re-reads after a save while the lens
     is on rather than rendering what the write handed back. */
  /** Somebody in the draft has taken him. */
  drafted: boolean;
  /** Which seat took him, 1-based. Null while he is still on the board. */
  drafted_by_slot: number | null;
  /** That seat is mine. Hoisted out of `drafted_by_slot` because "I have him" and "he is
      gone" are opposite facts about a row and the page styles them oppositely. */
  drafted_by_me: boolean;

  updated_at: string;
};

/**
 * The draft lens on `GET /master/board` — master.py's `draft_mode` / `hide_drafted`.
 *
 * Both default off, and off means the request is byte-identical to what it was before the
 * draft existed. `hide_drafted` IMPLIES `draft_mode` server-side; the page sends both anyway,
 * because the URL it built should say what it asked for.
 */
export type MasterBoardLens = {
  /** Annotate every row with what the live draft has taken. Nothing is hidden. */
  draft_mode?: boolean;
  /** Leave the drafted players out of `players` and `set_aside` entirely. Ranks and tiers
      are untouched, so a gap in the rank column is a player the room took. */
  hide_drafted?: boolean;
};

/**
 * Which order a set of cut ranks is over — master_tier.py: TIER_SCOPES.
 *
 * 'overall' is the whole board; a position is that position's sub-order, so its cut ranks
 * count point guards rather than board ranks. The position filter on the page is also the
 * scope selector, which is why `Position` and this share their five members.
 */
export const SCOPE_OVERALL = "overall";
export const TIER_SCOPES = [SCOPE_OVERALL, ...POSITIONS] as const;
export type TierScope = (typeof TIER_SCOPES)[number];

/** One scope's tier structure — master.py: TierScopeRow. */
export type TierScopeRow = {
  /** 'overall' | 'PG' | 'SG' | 'SF' | 'PF' | 'C'. Typed as the backend types it (`str`). */
  scope: string;
  /** How many players are in THIS scope's order — the board, or that position's slice. */
  size: number;
  /**
   * The rank each tier starts at, ascending, always beginning with 1. `[1, 4, 12]` is three
   * tiers: 1-3, 4-11, 12-size. This is exactly what `PUT /master/tiers` takes back — send it
   * with a divider added, moved or removed, never a delta.
   */
  cut_ranks: number[];
  tier_count: number;
};

/** Our board, its set-aside pile, and the consensus it is read against — MasterBoardResponse. */
export type MasterBoardResponse = {
  /** The horizon the REFERENCE column was computed under — the lens, not the board. Typed
      as the backend types it (`str`), like `ranking_horizon` above. */
  horizon: string;
  ranking_horizon: string;
  /** The horizon that decides who belongs on the board at all (MASTER_SEED_HORIZON). */
  seed_horizon: string;
  pool_size: number;
  total_ranked: number;
  /** This request found an empty board and seeded it from the consensus. Once, ever. */
  seeded: boolean;
  /** How many players this request inserted, and how many nobody currently ranks. */
  added: number;
  stale: number;
  /** ISO date (YYYY-MM-DD) every `age` here was computed at. */
  age_as_of: string;
  sources: SourceInfo[];
  /** The `?position=` this response was narrowed to, or null for the whole board. */
  position: string | null;
  /**
   * EVERY scope's tier structure, including the ones this response isn't showing. The page
   * draws its dividers from these rather than inferring them from the rows, so it cannot end
   * up disagreeing with the board about where a tier starts.
   */
  tiers: TierScopeRow[];
  players: MasterPlayerRow[];
  /** Off the order, not off the board: tags and notes intact, one write to bring back. */
  set_aside: MasterPlayerRow[];
};

/** The body of PUT /master/entries/{id} — master.py: MasterEntryWrite.
 *  Every key is optional and "left out" means "leave it alone", so this is built one key at
 *  a time: `{ note: null }` clears the note and touches nothing else. */
export type MasterEntryWriteBody = {
  tag?: string | null;
  note?: string | null;
  excluded?: boolean;
};

/* -------------------------------------------------------------------------------------- *
 * One player, in full — app/api/players.py: GET /players/{id}/detail
 *
 * What the board's two production columns expand into. Everything below is PRESENT-OR-ABSENT:
 * a rookie has no `last_season`, an unpriced player has no `market` and no `market_lines`, and
 * a stat nobody published a number for is simply not a key in the per-game map. There is no
 * place here where 0 stands in for "we don't know" — which is the whole reason a stat line is
 * worth showing somebody.
 * -------------------------------------------------------------------------------------- */

/** A season a player ACTUALLY played, priced under our scoring — players.py: SeasonLine. */
export type SeasonLine = {
  /** The season this is for — the one before the season being drafted, normally. */
  season: number;
  /** Games he PLAYED. Null when ESPN published no games count, or zero: neither divides. */
  games: number | null;
  fantasy_ppg: number;
  fantasy_total: number;
  /** Per game, keyed by stat NAME ('PTS', 'REB', 'MIN'). Counting stats only, and a stat with
      no number is ABSENT rather than 0. */
  per_game: Record<string, number>;
};

/** What the props imply — players.py: MarketLineProjection. Partial by construction. */
export type MarketProjectionLine = {
  fantasy_ppg: number;
  fantasy_total: number;
  games: number | null;
  per_game: Record<string, number>;
};

/** One raw prop behind that projection — players.py: DetailMarketLine. */
export type DetailMarketLine = {
  stat: string;
  /** Per game, always. */
  line: number;
  /** American odds; null for a side nobody priced. */
  over_odds: number | null;
  under_odds: number | null;
};

/** One player's whole evidence — players.py: PlayerDetailResponse. */
export type PlayerDetailResponse = {
  espn_player_id: number;
  name: string;
  nba_team: string | null;
  positions: string[];
  age: number | null;
  /** Null for a player who has never completed a season we hold. */
  last_season: SeasonLine | null;
  /** Null when nobody has posted a prop on him. */
  market: MarketProjectionLine | null;
  /** The props the market projection came from, by stat id. Empty when there are none. */
  market_lines: DetailMarketLine[];
};

/* -------------------------------------------------------------------------------------- *
 * The draft room — app/api/draft.py, over app/draft
 *
 * ONE live draft, and every verb below answers with the WHOLE state of it. That is the
 * contract worth knowing before using any of this: a pick, an edit, an advance, an undo and
 * a reset all hand back `DraftStateResponse`, so the page never patches its own board — it
 * replaces it with what the server says the draft is. The log is the record; everything else
 * on the response (the rosters, the clock, the open needs) is replayed from it.
 * -------------------------------------------------------------------------------------- */

/** The two ways the room is run — app/db/models/draft.py: DRAFT_MODES.
 *  A stored PREFERENCE and nothing more: the backend enforces neither, and every verb works
 *  under both. It decides which controls the page shows. */
export const DRAFT_MODES = ["simulation", "manual"] as const;
export type DraftMode = (typeof DRAFT_MODES)[number];

/** One pick that happened — draft.py: DraftPickRow. */
export type DraftPickRow = {
  /** 1-based, in snake order. The log is contiguous 1..N — a draft cannot skip a pick. */
  pick_number: number;
  round: number;
  team_slot: number;
  is_mine: boolean;
  espn_player_id: number;
  name: string;
  positions: string[];
  /** The simulated field took him, rather than this being typed in. Display only. */
  is_auto: boolean;
};

/** One seat: what it has taken, and what it still starts nobody at — draft.py: DraftTeamRow. */
export type DraftTeamRow = {
  team_slot: number;
  /** What this seat is CALLED: its stored name, or "Team {slot}". Resolved server-side, so
      the page never has to build the default itself. Cosmetic — nothing in the engine reads
      it, which is why it can be edited mid-draft while the seat cannot. */
  name: string;
  is_me: boolean;
  /** Player ids, in the order this seat drafted them. Their names are in `log`. */
  player_ids: number[];
  /** Its unfilled DEDICATED starter positions (PG/SG/SF/PF/C). Empty once five are set. */
  open_needs: string[];
};

/** The whole draft: its shape, the log, the rosters and the clock — draft.py:
 *  DraftStateResponse. */
export type DraftStateResponse = {
  team_count: number;
  rounds: number;
  my_slot: number;
  roster_slots: Record<string, number>;
  /** Which consensus the simulated room drafts off, and whose. Null sources = all of them.
      Both typed as the backend types them (`str`). */
  field_horizon: string;
  field_source_ids: string[] | null;
  /** One of DRAFT_MODES. Typed as the backend types it, so a mode added server-side reads
      as itself rather than failing to compile. */
  mode: string;
  /** How many players the field ranks at all. A pick outside it is a 422. */
  universe_size: number;
  total_picks: number;
  picks_made: number;
  /** Null once the draft is complete, all three of them. */
  on_the_clock: number | null;
  next_pick_number: number | null;
  current_round: number | null;
  is_my_pick: boolean;
  is_complete: boolean;
  /** Every pick number my seat owns, and the ones still to come. At slot 2 of 10 the first
      is [2, 19, 22, 39, 42, ...] — the alternating wait the plan is about. */
  my_pick_numbers: number[];
  my_remaining_pick_numbers: number[];
  created_at: string;
  updated_at: string;
  log: DraftPickRow[];
  /** Every seat, 1..team_count, mine included. */
  teams: DraftTeamRow[];
};

/** What the room did, and where that leaves the draft — draft.py: DraftAdvanceResponse. */
export type DraftAdvanceResponse = {
  /** The seed this advance was rolled with, echoed so a mock worth keeping can be re-run. */
  seed: number;
  /** The opponents' picks, in order. EMPTY when my seat is already on the clock or the draft
      is over: the advance never picks for me. */
  picks: DraftPickRow[];
  state: DraftStateResponse;
};

/** The body of POST /draft — draft.py: DraftCreate. Every field is optional, and an empty
 *  body is our league: the shape comes from DRAFT_*, the field from the consensus. */
export type DraftCreateBody = {
  my_slot?: number;
  mode?: DraftMode;
  field_horizon?: string;
  field_source_ids?: string[];
  roster_slots?: Record<string, number>;
  /** What the seats are called, keyed by seat number as a STRING (`{ "1": "Sam" }`) — JSON
      has no integer keys. Seats left out render as "Team {slot}". */
  team_names?: Record<string, string>;
};

/** The body of PUT /draft/config — draft.py: DraftConfigWrite.
 *
 *  The two fields have different rules and the difference is the endpoint: a NAME is
 *  cosmetic and merges at any point in the draft; the SEAT is part of what the picks already
 *  made mean, so it moves only while the draft is EMPTY and is a 422 after that (the
 *  reconfigure path, which throws the picks away, is the only way to change it then). */
export type DraftConfigBody = {
  my_slot?: number;
  /** MERGED into the stored names, not a replacement: naming one seat leaves the rest alone.
      An empty string clears one back to "Team {slot}". */
  team_names?: Record<string, string>;
  /** Which consensus the room is assumed to draft off. THE SEAT'S RULE: every pick already
      made was made against this field, so it moves only while the draft is empty. */
  field_horizon?: string;
  /** The source ids the room drafts off, from `api.sources`. An empty array means the same as
      omitting it on create — every source the horizon offers. The seat's rule again. */
  field_source_ids?: string[];
};

/** The body of POST /draft/picks — draft.py: DraftPickWrite. */
export type DraftPickWriteBody = {
  player_id: number;
  /** Whose pick it is. Omit and the backend uses whoever is on the clock; pass it and the
      pick is REFUSED unless it matches, which is what a typed-in room wants. */
  team_slot?: number;
};

/** The body of PUT /draft/picks/{n} — draft.py: DraftPickEdit. No seat: the snake owns which
 *  team picks at a pick number, and an edit changes who was taken, never whose turn it was. */
export type DraftPickEditBody = {
  player_id: number;
};

/** The body of POST /draft/simulate — draft.py: DraftSimulateWrite. All optional. */
export type DraftSimulateBody = {
  /** At most this many opponent picks. A CAP, not a target: the advance still stops at my
      seat and at the end of the draft. `1` is the step button. */
  count?: number;
  /** Omitted, a fresh one is drawn and echoed back on the response. */
  seed?: number;
  top_k?: number;
  temperature?: number;
  need_mult?: number;
};

/** One name on the plan: where I have him, and how likely he is to last — draft.py:
 *  PlanPlayerRow. */
export type PlanPlayerRow = {
  espn_player_id: number;
  name: string;
  positions: string[];
  /** MY board rank and MY tier band. The tier is null when the board's 'overall' dividers
      have never been read into existence — a plan request does not seed them. */
  rank: number | null;
  tier: number | null;
  /** One of MASTER_TAGS, or null. Every row in `targets` carries 'target' by construction.
      Typed as the backend types it (`str`), like `MasterPlayerRow.tag`. */
  tag: string | null;
  note: string | null;
  /** His place on the FIELD's board — the room's opinion, which is what the availability
      number is actually computed from. Read the two together. */
  field_rank: number | null;
  /**
   * The chance he is still on the board when this pick comes up, in [0, 1]. Non-increasing
   * across my later picks, by construction — and 1 at the pick I am on the clock for, since
   * nothing happens between now and it.
   */
  availability: number;
  /** Would he cover a dedicated starter slot I still have open? */
  fills_need: boolean;
};

/** One of my upcoming picks, and who to be thinking about at it — draft.py: PlanPickRow. */
export type PlanPickRow = {
  pick_number: number;
  round: number;
  /** How many picks away it is. 0 means I am on the clock now. */
  picks_away: number;
  /** My unfilled dedicated starter positions. THE SAME at every planned pick, because the
      projection takes nobody for me in between. */
  open_needs: string[];
  /** The players I tagged 'target' who are still available, in my board order. */
  targets: PlanPlayerRow[];
  /** The top of my board that is still available, in my board order. */
  best_available: PlanPlayerRow[];
};

/** The round-by-round plan: my board, at each of my upcoming picks, with the odds —
 *  draft.py: DraftPlanResponse. */
export type DraftPlanResponse = {
  /** The Monte Carlo behind every `availability` on this response. */
  iterations: number;
  seed: number;
  /** How long each list is, at most. */
  size: number;
  /** The field the availability was computed against — the DRAFT's, not the request's.
      Typed as the backend types them (`str`). */
  field_horizon: string;
  field_source_ids: string[] | null;
  /** How many ranked, non-excluded players on my board are still available. */
  available_on_board: number;
  /** Nothing to plan for: the draft is over, and `picks` is empty. */
  is_complete: boolean;
  picks: PlanPickRow[];
};

/** The query of GET /draft/plan. Every one of them is optional — the backend's defaults are
 *  DRAFT_SIM_ITERATIONS, a FIXED seed, all of my remaining picks, and DRAFT_PLAN_SIZE. */
/**
 * Every available player's chance of lasting until my next pick — draft.py:
 * DraftAvailabilityResponse.
 *
 * The same probability the plan carries, asked about EVERYBODY rather than about a
 * shortlist, at ONE pick rather than four. That is what lets a sidebar and a six-column
 * rankings view put a percentage on any name they draw: the plan's `size` would have to be
 * the whole board to cover them, and this is the endpoint that question belongs to. The cost
 * of the Monte Carlo is the opponent picks it simulates, not the players it tracks, so
 * asking about a thousand names is not a thousand times the work.
 */
export type DraftAvailabilityResponse = {
  /** The pick the numbers are about — my next one. Null when there isn't one. */
  pick_number: number | null;
  /** Nothing left to be available FOR: the draft is over, or I have no pick remaining in
      it. `availability` is empty either way. */
  is_complete: boolean;
  /** Player id (as a JSON object key, so a string) -> chance in [0, 1] he is still there. A
      player the FIELD doesn't rank is absent rather than carrying a meaningless 100%. */
  availability: Record<string, number>;
};

export type DraftPlanParams = {
  /** How many of my upcoming picks to plan for. Pass 3 or 4 while a clock is running:
      planning all of them from pick 1 simulates nearly the whole draft a thousand times. */
  picks?: number;
  /** How many names in each list. */
  size?: number;
  iterations?: number;
  /** Fixed by default, deliberately: a percentage someone compares between two refreshes
      must not move on its own. */
  seed?: number;
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

/**
 * The headers every request goes out with: JSON, whatever the caller added, and the shared
 * password when one is stored.
 *
 * Built through `Headers` rather than an object spread because a spread of a `Headers`
 * instance yields `{}` — a caller passing one would silently lose its `Content-Type`.
 * `Authorization` is set last, so it is the client's to attach and not a caller's to forge.
 */
function headersFor(init?: RequestInit): Headers {
  const headers = new Headers({ Accept: "application/json" });
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
      // After `...init`, deliberately: the merge above already folded the caller's headers in,
      // and letting `init.headers` win here would drop the Accept and the token with it.
      headers: headersFor(init),
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
    if (response.status === 401) {
      // The one status this client ACTS on rather than only reporting: the stored password is
      // wrong or gone, so drop it and let the gate come back (components/AuthGate.tsx). The
      // error is still thrown — a page mid-fetch has to stop, not hang.
      reportUnauthorized();
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

/** A JSON PUT through the same error shaping. */
async function put<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** A DELETE through the same error shaping. Every one of ours answers with a body. */
async function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

export const api = {
  info: () => request<ServiceInfo>("/"),
  health: () => request<HealthResponse>("/health"),
  dbHealth: () => request<DbHealthResponse>("/health/db"),

  /**
   * Does the stored password get past the gate? Resolves if it does, throws a 401 if not.
   *
   * Any gated endpoint would answer this; `/import/kinds` is the cheapest one in the API — a
   * static list, no database, no parameters — so this is a 200-or-401 probe and nothing else.
   * The three calls above are exempt from the gate on the backend (app/auth.py: OPEN_PATHS)
   * and so can never answer it.
   */
  checkAccess: () => request<unknown>("/import/kinds").then(() => undefined),

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

  /**
   * Everything that can rank players under this horizon.
   *
   * The horizon decides ELIGIBILITY, not just presentation: value sources appear under both
   * (a per-player number can be aged), while an imported rank-only list appears only under
   * the horizon its declared tag maps to — dynasty -> 'dynasty', current_year -> 'redraft'.
   */
  sources: (horizon?: Horizon) =>
    request<SourcesResponse>(`/sources${query({ horizon })}`),

  /** Several sources side by side, averaged equally, with the disagreement called out. */
  boardConsensus: (params: ConsensusParams = {}) =>
    request<ConsensusResponse>(
      `/board/consensus${query({
        horizon: params.horizon,
        // Omitted (not empty) when the caller passes no ids: the backend reads an empty
        // `sources=` as a mistake and a missing one as "all of them".
        sources: params.sources?.length ? params.sources.join(",") : undefined,
        method: params.method,
        position: params.position,
        limit: params.limit,
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

  /** Every stored line for one book and season, grouped by player. Empty is a clean 200. */
  marketLines: (params: { source?: string; season?: number } = {}) =>
    request<MarketLinesResponse>(
      `/market/lines${query({ source: params.source, season: params.season })}`,
    ),

  /**
   * Store ONE line and re-price the player it belongs to.
   *
   * PUT, not POST, because (source, season, player, stat) is the key: sending the same stat
   * twice moves the number rather than adding a second line for it. The response carries the
   * player's re-derived value, so nothing here has to work out what a new odds pair is worth.
   */
  putMarketLine: (body: MarketLineWriteBody) =>
    put<MarketLineWriteResponse>("/market/lines", body),

  /**
   * Delete one line and re-price what is left.
   *
   * `player_removed` on the response is the case to watch: with no lines left, his derived
   * projection is removed rather than zeroed, so he leaves the market source entirely.
   */
  deleteMarketLine: (lineId: number) => del<MarketDeleteResponse>(`/market/lines/${lineId}`),

  /** Clear one player's whole set for a book and season — "he is off the board" in one call. */
  clearMarketPlayer: (params: { source: string; season?: number; player_id: number }) =>
    del<MarketDeleteResponse>(
      `/market/lines${query({
        source: params.source,
        season: params.season,
        player_id: params.player_id,
      })}`,
    ),

  /**
   * Everything we hold about one player: last season, the market, and the raw props.
   *
   * What a board row's two production columns expand into — the answer to "twenty-two a game
   * off what?". A 404 means we hold no identity for that id, which no board row can produce;
   * a player we know nothing else about is a clean 200 with both halves null.
   */
  playerDetail: (espnPlayerId: number) =>
    request<PlayerDetailResponse>(`/players/${espnPlayerId}/detail`),

  /* --- the master ranking ------------------------------------------------------------- */

  /**
   * Our board, complete and reconciled.
   *
   * A GET that WRITES, deliberately (see app/api/master.py): an empty board seeds itself from
   * the consensus, a player the sources now rank but we have no entry for is inserted at the
   * slot the consensus implies and flagged `is_new`, and an entry nobody ranks any more is
   * flagged `is_stale`. So the response is authoritative about the order in a way a cached
   * one never is — every mutation below answers with the same shape for exactly that reason.
   *
   * `horizon` picks which consensus the REFERENCE column is computed against. It does not
   * change the order and it cannot change who is on the board.
   *
   * `position` narrows WHO comes back to the players listed there, in board order, with their
   * overall ranks and overall tiers intact — a point guard's place on our board does not
   * change because we are looking at the guards. It is a view of the order, never a re-sort.
   * A position the backend doesn't know is a 422 here (unlike `GET /players/board`, where it
   * is an empty page), because the filter also picks a tier scope.
   *
   * `lens` is the DRAFT annotation, and it is the only parameter here that is off by
   * default in a way worth saying out loud: with neither flag set the URL is exactly what it
   * was before the draft room existed, so every caller that doesn't care — the draft room's
   * own catalog read included — keeps the response it always had.
   */
  masterBoard: (horizon?: Horizon, position?: Position | null, lens: MasterBoardLens = {}) =>
    request<MasterBoardResponse>(
      `/master/board${query({
        horizon,
        position,
        // Sent only when asked for: the backend reads both as false when absent, and an
        // explicit `draft_mode=false` would put a parameter in the URL that says nothing.
        draft_mode: lens.draft_mode ? "true" : undefined,
        hide_drafted: lens.hide_drafted ? "true" : undefined,
      })}`,
    ),

  /**
   * Save a reorder: rank = place in the list, for the WHOLE non-excluded board.
   *
   * Not a partial order and not a diff — the backend validates it as a permutation and 422s
   * naming what is missing, which is what catches a page whose order predates a rookie that
   * got reconciled in underneath it. The horizon rides along so the board that comes back is
   * still read against the lens the page is showing.
   */
  putMasterOrder: (orderedPlayerIds: number[], horizon?: Horizon) =>
    put<MasterBoardResponse>(`/master/order${query({ horizon })}`, {
      ordered_player_ids: orderedPlayerIds,
    }),

  /**
   * Tag him, write a note, set him aside or bring him back — any subset, in one call.
   *
   * Answers with the whole board rather than the row, because setting a player aside reflows
   * every rank below him and a single row could not honestly report that.
   */
  putMasterEntry: (playerId: number, body: MasterEntryWriteBody, horizon?: Horizon) =>
    put<MasterBoardResponse>(`/master/entries/${playerId}${query({ horizon })}`, body),

  /**
   * Save where ONE scope's tiers start — what a dragged divider writes.
   *
   * All-or-nothing per scope, like `putMasterOrder` and for the same reason: the client holds
   * the whole list of dividers, not a delta. `cutRanks` must be sorted, unique, inside
   * 1..size and begin with 1 — the backend 422s otherwise, so lib/masterboard.ts keeps every
   * list this is handed valid by construction rather than relying on that check.
   *
   * It writes cut RANKS and therefore cannot move a player: the order, the tags and the notes
   * come back exactly as they were, with different lines drawn between the bands.
   */
  putMasterTiers: (scope: TierScope, cutRanks: number[], horizon?: Horizon) =>
    put<MasterBoardResponse>(`/master/tiers${query({ horizon })}`, {
      scope,
      cut_ranks: cutRanks,
    }),

  /**
   * Throw one scope's dividers away and let the value gaps cut it again.
   *
   * The undo for a set of hand-moved boundaries, and scoped on purpose: reseeding the centres
   * must not touch the overall dividers. Nothing about the order, the ranks, the tags or the
   * notes changes.
   */
  reseedMasterTiers: (scope: TierScope, horizon?: Horizon) =>
    post<MasterBoardResponse>(`/master/tiers/reseed${query({ scope, horizon })}`, {}),

  /**
   * Throw the board away and rebuild it from the consensus — every rank, tag and note with
   * it. `reset=true` is what the backend requires to touch a board that isn't empty.
   */
  resetMasterBoard: (horizon?: Horizon) =>
    post<MasterBoardResponse>(`/master/seed${query({ reset: "true", horizon })}`, {}),

  /* --- the draft room ----------------------------------------------------------------- */

  /**
   * Where the draft stands, or a 404 when there isn't one.
   *
   * The 404 is a STATE, not a failure: it is what the page's setup form is for. Callers
   * check `status === 404` and offer to start one rather than rendering an error.
   */
  getDraft: () => request<DraftStateResponse>("/draft"),

  /**
   * Start the draft, with its config snapshotted onto it.
   *
   * An empty body is a complete request — the shape comes from `DRAFT_*` and the field from
   * the consensus. A second draft is a 409 unless `reset` is true, because the picks on the
   * first are a record of something that happened.
   */
  createDraft: (body: DraftCreateBody = {}, reset = false) =>
    post<DraftStateResponse>(`/draft${query({ reset: reset ? "true" : undefined })}`, body),

  /** Throw the picks away and keep the config: the same draft, from pick 1. */
  resetDraft: () => post<DraftStateResponse>("/draft/reset", {}),

  /**
   * Enter a pick: mine, or a rival's read off the screen.
   *
   * Strictly in pick order — the draft has one clock — so `team_slot` is left off unless the
   * caller wants it checked. Everything the engine refuses comes back as a 422 carrying its
   * own message: a player already drafted, a player the field doesn't rank, the wrong seat.
   */
  applyPick: (body: DraftPickWriteBody) => post<DraftStateResponse>("/draft/picks", body),

  /**
   * Change who an already-made pick took — the mis-entry noticed too late for undo.
   *
   * The seat is untouched and the player who was there goes back on the board. A 422 when
   * the pick hasn't happened, when the field doesn't rank the new man, or when he was taken
   * at another pick; editing him to who he already is is a no-op 200.
   */
  editPick: (pickNumber: number, body: DraftPickEditBody) =>
    put<DraftStateResponse>(`/draft/picks/${pickNumber}`, body),

  /**
   * Let the room draft, and commit what it took.
   *
   * One seeded draw — a mock happening, not a distribution. It STOPS AT MY SEAT and never
   * picks for me, so an advance while I am on the clock is an empty `picks` and a 200.
   * `{ count: 1 }` steps it one opponent pick at a time.
   */
  simulate: (body: DraftSimulateBody = {}) =>
    post<DraftAdvanceResponse>("/draft/simulate", body),

  /** Take the last pick back, auto or manual. A draft with no picks in it is a 409. */
  undoPick: () => post<DraftStateResponse>("/draft/undo", {}),

  /**
   * My board at each of my upcoming picks, with the chance each name is still there.
   *
   * The point of the whole draft engine, and the one read on this page that is a
   * PROBABILITY rather than a fact: `simulate` is one draw that commits picks, this is a
   * thousand draws that commit nothing. Two lists per pick — the targets I tagged and have
   * not lost, and the top of my board that is still there.
   *
   * A 404 means there is no draft, which callers handle the way `getDraft` does. Ask for
   * `picks` while a clock is running: the cost is the picks it has to simulate.
   */
  draftPlan: (params: DraftPlanParams = {}) =>
    request<DraftPlanResponse>(
      `/draft/plan${query({
        picks: params.picks,
        size: params.size,
        iterations: params.iterations,
        seed: params.seed,
      })}`,
    ),

  /**
   * How likely every player still on the board is to last until my next pick.
   *
   * One read for the whole page's percentages — the sidebar and every rankings column join
   * their rows to this map by player id. Re-read whenever the draft moves, because an
   * availability is a statement about the picks already made.
   *
   * A 404 means there is no draft, which callers handle the way `getDraft` does.
   */
  draftAvailability: () => request<DraftAvailabilityResponse>("/draft/availability"),

  /**
   * Change my seat (only while the draft is EMPTY) or what the seats are called (whenever).
   *
   * The asymmetry is the backend's: a name is cosmetic, a seat is part of what the picks
   * already made mean. A seat change on a started draft is a 422 pointing at the
   * reconfigure — `createDraft(body, true)` — which can do it by throwing the picks away.
   */
  updateDraftConfig: (body: DraftConfigBody) =>
    put<DraftStateResponse>("/draft/config", body),
};
