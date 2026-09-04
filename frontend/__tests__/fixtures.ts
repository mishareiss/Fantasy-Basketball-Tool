import type {
  AliasResponse,
  BoardResponse,
  BoardRow,
  CurveResponse,
  ImportKindInfo,
  ImportResponse,
  ImportRowOutcome,
  TierSummaryRow,
  TiersResponse,
} from "@/lib/api";

/**
 * Hand-built stand-ins for the backend's responses, shaped exactly like app/api/players.py
 * and app/api/valuation.py serialize them. Nothing here hits the network: the point of the
 * component tests is the rendering, and a fixture that drifts from the pydantic model is a
 * type error rather than a flaky test.
 */

function row(overrides: Partial<BoardRow> & Pick<BoardRow, "rank" | "name">): BoardRow {
  const perGame = overrides.fantasy_points_per_game ?? 50 - overrides.rank;
  return {
    espn_player_id: 1000 + overrides.rank,
    nba_team: "BOS",
    positions: ["SF"],
    age: 26,
    fantasy_points_per_game: perGame,
    fantasy_points_total: perGame * 70,
    projected_games: 70,
    per_game_basis: "projected",
    adp: overrides.rank + 0.5,
    auction_value: null,
    percent_owned: null,
    current_year_value: perGame,
    dynasty_value: perGame,
    age_multiplier: 1,
    age_adjusted: true,
    tier: null,
    ...overrides,
  };
}

export const PLAYERS: BoardRow[] = [
  row({ rank: 1, name: "Victor Wembanyama", positions: ["C"], age: 23, tier: 1, dynasty_value: 62.4 }),
  row({ rank: 2, name: "Anthony Edwards", positions: ["SG"], age: 25, tier: 1, dynasty_value: 58.1 }),
  row({ rank: 3, name: "Cade Cunningham", positions: ["PG"], age: 24, tier: 2, dynasty_value: 49.7 }),
  row({ rank: 4, name: "Tyrese Haliburton", positions: ["PG"], age: 26, tier: 2, dynasty_value: 47.2 }),
  row({
    rank: 5,
    name: "Chris Paul",
    positions: ["PG"],
    age: 41,
    tier: null,
    dynasty_value: 18.3,
    age_multiplier: 0.6,
  }),
];

const TIER_SUMMARY: TierSummaryRow[] = [
  { tier: 1, size: 2, value_high: 62.4, value_low: 58.1, start_rank: 1, gap: null, gap_ratio: null },
  { tier: 2, size: 2, value_high: 49.7, value_low: 47.2, start_rank: 3, gap: 8.4, gap_ratio: 3.2 },
];

export function boardResponse(overrides: Partial<BoardResponse> = {}): BoardResponse {
  return {
    source: "espn",
    kind: "projected_season",
    season: 2027,
    adp_source: "espn",
    adp_season: 2027,
    total_ranked: PLAYERS.length,
    position: null,
    horizon: "dynasty",
    age_as_of: "2027-10-21",
    tiers: "auto",
    tier_pool: 4,
    tier_summary: TIER_SUMMARY,
    players: PLAYERS,
    ...overrides,
  };
}

/** The board as it comes back filtered to point guards: fewer rows, same tiers. */
export function pointGuardResponse(): BoardResponse {
  const guards = PLAYERS.filter((player) => player.positions.includes("PG"));
  return boardResponse({
    position: "PG",
    total_ranked: guards.length,
    players: guards.map((player, index) => ({ ...player, rank: index + 1 })),
  });
}

export function curveResponse(): CurveResponse {
  return {
    params: {
      prime_start: 24,
      prime_end: 28,
      youth_bonus_per_year: 0.04,
      decline_per_year: 0.07,
      min_multiplier: 0.35,
    },
    env_vars: {
      prime_start: "DYNASTY_PRIME_START",
      prime_end: "DYNASTY_PRIME_END",
      youth_bonus_per_year: "DYNASTY_YOUTH_BONUS_PER_YEAR",
      decline_per_year: "DYNASTY_DECLINE_PER_YEAR",
      min_multiplier: "DYNASTY_MIN_MULTIPLIER",
    },
    sample_min_age: 22,
    sample_max_age: 25,
    sample: [
      { age: 22, multiplier: 1.08, band: "youth" },
      { age: 23, multiplier: 1.04, band: "youth" },
      { age: 24, multiplier: 1.0, band: "prime" },
      { age: 25, multiplier: 1.0, band: "prime" },
    ],
  };
}

export function tiersResponse(): TiersResponse {
  return {
    horizon: "dynasty",
    source: "espn",
    season: 2027,
    params: { gap_multiple: 2.5, min_size: 2, max_tiers: 12, pool: 150 },
    env_vars: {
      gap_multiple: "TIER_GAP_MULTIPLE",
      min_size: "TIER_MIN_SIZE",
      max_tiers: "TIER_MAX",
      pool: "TIER_POOL",
    },
    typical_gap: 2.6,
    break_threshold: 6.5,
    pool_size: 4,
    total_ranked: PLAYERS.length,
    tiers: [
      { ...TIER_SUMMARY[0], leader: "Victor Wembanyama" },
      { ...TIER_SUMMARY[1], leader: "Cade Cunningham" },
    ],
  };
}


/* ---------------------------------------------------------------------------------------- *
 * Imports — app/api/imports.py shapes: the kind listing, and a preview with one row of each
 * interesting status (a clean match, a fuzzy hit with candidates, an unmatched name nothing
 * resembles). That mix is what the importer's states are made of.
 * ---------------------------------------------------------------------------------------- */

export function importKinds(): ImportKindInfo[] {
  return [
    {
      kind: "adp",
      label: "Where a source says players are being drafted",
      implemented: true,
      value_columns: { adp: ["adp", "avg pick", "average pick", "rank"] },
      required: ["adp"],
    },
    {
      kind: "projection",
      label: "A source's projected stat line per player",
      implemented: true,
      value_columns: { PTS: ["pts", "points"], REB: ["reb", "trb"], GP: ["gp", "games"] },
      required: ["PTS"],
    },
    {
      kind: "ranking",
      label: "An ordered list of players from one source — a board, with optional tiers",
      implemented: true,
      value_columns: { rank: ["rank", "rk", "#"], tier: ["tier", "grp"], value: ["value"] },
      required: [],
    },
    {
      kind: "market_line",
      label: "Season-long sportsbook props -> new `MarketLine` model, then de-vig. Needs: …",
      implemented: false,
      value_columns: {},
      required: [],
    },
  ];
}

function outcome(
  overrides: Partial<ImportRowOutcome> & Pick<ImportRowOutcome, "line" | "source_name" | "status">,
): ImportRowOutcome {
  return {
    values: { adp: overrides.line },
    team: "OKC",
    positions: ["PG"],
    player_id: null,
    player_name: null,
    confidence: 0,
    method: "",
    candidates: [],
    note: null,
    ...overrides,
  };
}

export const IMPORT_ROWS: ImportRowOutcome[] = [
  outcome({
    line: 2,
    source_name: "Gilgeous-Alexander, Shai",
    status: "matched",
    player_id: 4278073,
    player_name: "Shai Gilgeous-Alexander",
    confidence: 1,
    method: "normalized",
  }),
  outcome({
    line: 3,
    source_name: "Victor Wembanyma",
    status: "review",
    team: "SAS",
    method: "ambiguous",
    candidates: [
      { player_id: 5104157, full_name: "Victor Wembanyama", nba_team: "SAS", score: 0.94 },
      { player_id: 3032977, full_name: "Victor Oladipo", nba_team: null, score: 0.61 },
    ],
  }),
  outcome({
    line: 4,
    source_name: "Nikola Topić",
    status: "unmatched",
    method: "unmatched",
  }),
];

/** The same file after the review row has been aliased: it lands as `alias` at 1.0. */
export const RESOLVED_ROWS: ImportRowOutcome[] = IMPORT_ROWS.map((row) =>
  row.line === 3
    ? {
        ...row,
        status: "matched",
        method: "alias",
        confidence: 1,
        player_id: 5104157,
        player_name: "Victor Wembanyama",
        candidates: [],
      }
    : row,
);

export function importResponse(overrides: Partial<ImportResponse> = {}): ImportResponse {
  const rows = overrides.rows ?? IMPORT_ROWS;
  return {
    kind: "adp",
    source: "hashtag",
    season: 2027,
    dry_run: true,
    options: {},
    columns: { name: "PLAYER", team: "TEAM", adp: "Avg Pick" },
    delimiter: ",",
    rows_parsed: rows.length,
    rows_skipped_blank: 0,
    matched: rows.filter((row) => row.status === "matched").length,
    review: rows.filter((row) => row.status === "review").length,
    unmatched: rows.filter((row) => row.status === "unmatched").length,
    duplicate: 0,
    invalid: 0,
    aliases_created: 1,
    aliases_existing: 0,
    rows_created: 1,
    rows_updated: 0,
    rows_unchanged: 0,
    notes: [],
    ...overrides,
    rows,
  };
}

export function aliasResponse(overrides: Partial<AliasResponse> = {}): AliasResponse {
  return {
    espn_player_id: 5104157,
    name: "Victor Wembanyama",
    source: "hashtag",
    source_name: "Victor Wembanyma",
    source_id: null,
    confidence: 1,
    match_method: "manual",
    created: true,
    birthdate: "2004-01-04",
    age: 23,
    ...overrides,
  };
}
