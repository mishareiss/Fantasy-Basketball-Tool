import type {
  AliasResponse,
  BoardResponse,
  BoardRow,
  ConsensusMethod,
  ConsensusResponse,
  ConsensusRow,
  CurveResponse,
  Horizon,
  SourceInfo,
  SourcesResponse,
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


/* ---------------------------------------------------------------------------------------- *
 * The consensus board — app/api/consensus.py shapes.
 *
 * The pool is 101 so a percentile is a round number: `percentile_for(rank, 101)` is
 * `101 - rank`, which keeps the expected numbers in the tests readable instead of being
 * three decimals nobody can check by eye.
 * ---------------------------------------------------------------------------------------- */

export const POOL_SIZE = 101;

/** app/ranking/sources.py: percentile_for, at POOL_SIZE. */
export function percentileFor(rank: number): number {
  return Math.max(0, Math.min(100, (100 * (POOL_SIZE - rank)) / (POOL_SIZE - 1)));
}

export const PROJECTION_SOURCE: SourceInfo = {
  id: "projection:espn",
  label: "espn projection",
  kind: "projection",
  source: "espn",
  season: 2027,
  horizon: null,
  player_count: 90,
};

export const ADP_SOURCE: SourceInfo = {
  id: "adp:espn",
  label: "espn ADP",
  kind: "adp",
  source: "espn",
  season: 2027,
  horizon: null,
  player_count: 101,
};

/** Tagged dynasty at import, so it is eligible under the dynasty horizon and no other. */
export const DYNASTY_RANKING_SOURCE: SourceInfo = {
  id: "ranking:1",
  label: "Dizzle Dynasty",
  kind: "ranking",
  source: "Dizzle Dynasty",
  season: 2027,
  horizon: "dynasty",
  player_count: 40,
};

/** Its redraft counterpart — what the win-now horizon swaps in for it. */
export const REDRAFT_RANKING_SOURCE: SourceInfo = {
  id: "ranking:2",
  label: "Rest of Season",
  kind: "ranking",
  source: "Dizzle Dynasty",
  season: 2027,
  horizon: "redraft",
  player_count: 30,
};

export function sourcesResponse(horizon: Horizon = "dynasty"): SourcesResponse {
  return {
    horizon,
    ranking_horizon: horizon === "dynasty" ? "dynasty" : "redraft",
    pool_size: POOL_SIZE,
    sources: [
      PROJECTION_SOURCE,
      ADP_SOURCE,
      horizon === "dynasty" ? DYNASTY_RANKING_SOURCE : REDRAFT_RANKING_SOURCE,
    ],
  };
}

/**
 * The ranks each fixture player gets from each source. A missing entry is a source with no
 * opinion on him — which is the case the whole missing-player rule is about, so the fixture
 * has one on purpose (nobody projects a rookie).
 */
const CONSENSUS_RANKS: { name: string; age: number; ranks: Record<string, number> }[] = [
  { name: "Victor Wembanyama", age: 23, ranks: { "projection:espn": 1, "ranking:1": 1 } },
  { name: "Cade Cunningham", age: 24, ranks: { "projection:espn": 8, "ranking:1": 6 } },
  // The payoff row: a projection loves him, a dynasty board doesn't. 2 vs 17.
  { name: "Giannis Antetokounmpo", age: 32, ranks: { "projection:espn": 2, "ranking:1": 17 } },
  // On the imported board and nowhere else.
  { name: "Cameron Boozer", age: 20, ranks: { "ranking:1": 12 } },
];

function consensusRow(
  place: number,
  entry: (typeof CONSENSUS_RANKS)[number],
  selected: string[],
  method: ConsensusMethod,
): ConsensusRow {
  const present = selected.filter((id) => entry.ranks[id] !== undefined);
  const cells = Object.fromEntries(
    present.map((id) => [
      id,
      { rank: entry.ranks[id], percentile: percentileFor(entry.ranks[id]) },
    ]),
  );
  const ranks = present.map((id) => entry.ranks[id]);
  const percentiles = ranks.map(percentileFor);
  const values = method === "percentile" ? percentiles : ranks;

  return {
    rank: place,
    espn_player_id: 2000 + place,
    name: entry.name,
    nba_team: "MIL",
    positions: ["PF"],
    age: entry.age,
    consensus: values.reduce((total, value) => total + value, 0) / values.length,
    cells,
    sources_present: present.length,
    sources_missing: selected.filter((id) => entry.ranks[id] === undefined),
    spread: ranks.length > 1 ? Math.max(...percentiles) - Math.min(...percentiles) : null,
    rank_spread: ranks.length > 1 ? Math.max(...ranks) - Math.min(...ranks) : null,
  };
}

/**
 * A consensus board over the given sources, ordered the way the backend orders it.
 *
 * The consensus and the spread are DERIVED from the per-source ranks here rather than
 * hand-written, for the same reason the backend derives them: a fixture whose consensus
 * column disagreed with its source columns would let a rendering bug pass.
 */
export function consensusResponse(
  overrides: Partial<ConsensusResponse> = {},
  selected: string[] = [PROJECTION_SOURCE.id, DYNASTY_RANKING_SOURCE.id],
): ConsensusResponse {
  const method = overrides.method ?? "rank";
  const horizon = overrides.horizon ?? "dynasty";
  const catalog = [PROJECTION_SOURCE, ADP_SOURCE, DYNASTY_RANKING_SOURCE, REDRAFT_RANKING_SOURCE];
  const sources = selected
    .map((id) => catalog.find((source) => source.id === id))
    .filter((source): source is SourceInfo => source !== undefined);

  const rows = CONSENSUS_RANKS.filter((entry) =>
    selected.some((id) => entry.ranks[id] !== undefined),
  )
    .map((entry, index) => consensusRow(index + 1, entry, selected, method))
    .sort((a, b) => (method === "percentile" ? b.consensus - a.consensus : a.consensus - b.consensus))
    .map((row, index) => ({ ...row, rank: index + 1 }));

  return {
    horizon,
    ranking_horizon: horizon === "dynasty" ? "dynasty" : "redraft",
    method,
    pool_size: POOL_SIZE,
    position: null,
    total_ranked: rows.length,
    age_as_of: "2027-10-21",
    sources,
    players: rows,
    ...overrides,
  };
}
