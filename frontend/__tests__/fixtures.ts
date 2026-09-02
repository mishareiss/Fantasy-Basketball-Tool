import type {
  BoardResponse,
  BoardRow,
  CurveResponse,
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
