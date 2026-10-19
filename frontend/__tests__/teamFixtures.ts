import type {
  CalendarDay,
  CalendarMatchup,
  FreeAgentRow,
  FreeAgentsParams,
  FreeAgentsResponse,
  GameInfo,
  LineupSource,
  PlanAcquisitions,
  PlanDrop,
  PlanMove,
  PlanPlayerRef,
  PlanResponse,
  PlanWarning,
  StartsDay,
  StartsResponse,
  StartsTotals,
  TeamCalendarResponse,
  TeamDayResponse,
  TeamPlayer,
  TeamPlayerCardResponse,
  TeamSlotRow,
} from "@/lib/api";

/**
 * Small hand-built stand-ins for the Team API (app/team/schemas.py), typed against the client
 * so a fixture that drifts from the wire shape is a compile error.
 *
 * A 20-day season in three matchups (1–6, 7–13, 14–20) starting Tue 2026-10-20, as the real
 * one does, and a ten-row slot grid with every kind of row the page draws: filled, empty, a
 * player with no game, and an IR slot.
 */

const DAY_ONE = Date.UTC(2026, 9, 20);

export function isoFor(sp: number): string {
  return new Date(DAY_ONE + (sp - 1) * 86_400_000).toISOString().slice(0, 10);
}

export const MATCHUPS: CalendarMatchup[] = [
  { id: 1, label: "Matchup 1", start: isoFor(1), end: isoFor(6), first_sp: 1, last_sp: 6 },
  { id: 2, label: "Matchup 2", start: isoFor(7), end: isoFor(13), first_sp: 7, last_sp: 13 },
  { id: 3, label: "Matchup 3", start: isoFor(14), end: isoFor(20), first_sp: 14, last_sp: 20 },
];

function matchupIdFor(sp: number): number {
  return MATCHUPS.find((m) => sp >= m.first_sp && sp <= m.last_sp)!.id;
}

export const CALENDAR_DAYS: CalendarDay[] = Array.from({ length: 20 }, (_, index) => ({
  sp: index + 1,
  date: isoFor(index + 1),
  matchup_id: matchupIdFor(index + 1),
  my_games: (index % 4) + 2,
}));

export function calendar(overrides: Partial<TeamCalendarResponse> = {}): TeamCalendarResponse {
  return {
    season: 2027,
    today_period: null,
    effective_period: 1,
    final_period: 20,
    matchups: MATCHUPS,
    days: CALENDAR_DAYS,
    ...overrides,
  };
}

export function game(sp: number, opponent: string, isHome: boolean): GameInfo {
  return {
    game_id: 1000 + sp,
    sp,
    opponent_pro_team_id: 9,
    opponent,
    is_home: isHome,
    starts_at: `${isoFor(sp)}T02:00:00Z`,
    start_time_tbd: false,
    display: `${isHome ? "vs" : "@"} ${opponent} 7:00 PM`,
    display_matchup: `${isHome ? "vs" : "@"} ${opponent}`,
    display_time: "7:00 PM",
  };
}

function player(overrides: Partial<TeamPlayer> & Pick<TeamPlayer, "id" | "name">): TeamPlayer {
  return {
    nba_team: "LAL",
    positions: ["PG"],
    eligible_slots: [12, 13],
    injury_status: "ACTIVE",
    game: game(1, "GSW", true),
    stats: {
      season: 2027,
      games: 70,
      fantasy_ppg: 40,
      fantasy_total: 2800,
      per_game: { PTS: 25, REB: 8 },
    },
    ...overrides,
  };
}

export const LUKA = player({
  id: 1,
  name: "Luka Doncic",
  matchup_games_left: 3,
  positions: ["PG", "SG"],
  eligible_slots: [0, 1, 5, 11, 12, 13],
});
export const LEBRON = player({
  id: 2,
  name: "LeBron James",
  positions: ["SF", "PF"],
  eligible_slots: [2, 3, 6, 11, 12, 13],
  injury_status: "DAY_TO_DAY",
});
export const CENTER = player({
  id: 3,
  name: "No Game Center",
  nba_team: "BOS",
  positions: ["C"],
  eligible_slots: [4, 11, 12, 13],
  game: null,
  stats: null,
});
export const GUARD = player({
  id: 4,
  name: "Utility Guard",
  positions: ["PG"],
  eligible_slots: [0, 11, 12, 13],
});
export const WING = player({
  id: 5,
  name: "Bench Wing",
  positions: ["SG", "SF"],
  eligible_slots: [1, 2, 11, 12, 13],
});

/** PG, SG, SF, PF, C, UT, UT, BE, BE, IR — rows 0..9. */
export function slots(): TeamSlotRow[] {
  return [
    { slot_id: 0, slot: "PG", player: LUKA },
    { slot_id: 1, slot: "SG", player: null },
    { slot_id: 2, slot: "SF", player: LEBRON },
    { slot_id: 3, slot: "PF", player: null },
    { slot_id: 4, slot: "C", player: CENTER },
    { slot_id: 11, slot: "UT", player: GUARD },
    { slot_id: 11, slot: "UT", player: null },
    { slot_id: 12, slot: "BE", player: WING },
    { slot_id: 12, slot: "BE", player: null },
    { slot_id: 13, slot: "IR", player: null },
  ];
}

export function teamDay(
  sp = 1,
  overrides: Partial<TeamDayResponse> & { source?: LineupSource } = {},
): TeamDayResponse {
  const { source, ...rest } = overrides;
  const matchup = MATCHUPS.find((m) => sp >= m.first_sp && sp <= m.last_sp)!;
  return {
    season: 2027,
    sp,
    date: isoFor(sp),
    matchup: { id: matchup.id, label: matchup.label, start: matchup.start, end: matchup.end },
    is_today: false,
    team: { id: 8, name: "Emzep", abbrev: "EMZ" },
    actuals_available: false,
    basis: "projected",
    stat_columns: ["PTS", "REB"],
    lineup_source: source ?? "espn",
    acquisitions: { used: 2, limit: 7, matchup_id: matchup.id },
    slots: slots(),
    warnings: [],
    ...rest,
  };
}

function freeAgent(
  overrides: Partial<FreeAgentRow> & Pick<FreeAgentRow, "id" | "name">,
): FreeAgentRow {
  return {
    nba_team: "MIA",
    positions: ["SG"],
    eligible_slots: [1, 11, 12, 13],
    injury_status: null,
    status: "FREEAGENT",
    waiver: null,
    games: [],
    games_in_days: 0,
    stats: { fantasy_ppg: 20, games: 60 },
    watched: false,
    ...overrides,
  };
}

/** Three players: a free agent, a waiver with a known clear time, one with an unknown one. */
export const FREE_AGENTS: FreeAgentRow[] = [
  freeAgent({ id: 101, name: "Free Agent Guy" }),
  freeAgent({
    id: 102,
    name: "Waiver Known",
    status: "WAIVERS",
    waiver: {
      clears_at: "2026-10-22T07:00:00Z",
      clears_at_display: "clears Thu 10/22",
      clears_at_known: true,
    },
  }),
  freeAgent({
    id: 103,
    name: "Waiver Unknown",
    status: "WAIVERS",
    waiver: { clears_at: null, clears_at_display: null, clears_at_known: false },
  }),
];

/**
 * A fake `GET /team/free-agents`: echoes the requested days back, gives every player a game
 * on odd days only, keeps only `watched` rows for `watchlist`, and pages with `limit` /
 * `offset`.
 */
export function freeAgentsFor(params: FreeAgentsParams, pool = FREE_AGENTS): FreeAgentsResponse {
  const days = params.days ?? [1];
  const available = params.watchlist ? pool.filter((row) => row.watched) : pool;
  const players = available.map((row) => {
    const games = days.map((sp) => ({ sp, game: sp % 2 === 1 ? game(sp, "NYK", false) : null }));
    return { ...row, games, games_in_days: games.filter((g) => g.game).length };
  });
  const offset = params.offset ?? 0;
  const limit = params.limit ?? 100;
  return {
    season: 2027,
    days: days.map((sp) => ({ sp, date: isoFor(sp), matchup_id: matchupIdFor(sp) })),
    mode: params.mode ?? "none",
    basis: params.basis ?? "projected",
    actuals_available: false,
    total: players.length,
    players: players.slice(offset, offset + limit),
  };
}

export function playerCard(id = 1, name = "Luka Doncic"): TeamPlayerCardResponse {
  // Seven days from Sat 10/24 (sp 5): two in Matchup 1, five in Matchup 2.
  const window = [5, 6, 7, 8, 9, 10, 11];
  return {
    season: 2027,
    player: {
      id,
      name,
      nba_team: "LAL",
      positions: ["PG", "SG"],
      eligible_slots: [0, 1, 11, 12, 13],
      injury_status: null,
      owner: { team_id: 8, name: "Emzep" },
    },
    waiver: null,
    watched: false,
    actuals_available: true,
    stat_columns: ["PTS", "REB"],
    stats: {
      projected: { season: 2027, games: 70, fantasy_ppg: 40.2, fantasy_total: 2814, per_game: { PTS: 28.1, REB: 8.4 } },
      season: null,
      l7: { season: 2027, games: 3, fantasy_ppg: 45.5, fantasy_total: 136.5, per_game: { PTS: 31 } },
      l15: null,
      l30: null,
    },
    schedule: window.map((sp) => ({
      sp,
      date: isoFor(sp),
      weekday: ["Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon"][(sp - 1) % 7],
      matchup_id: matchupIdFor(sp),
      matchup_label: `Matchup ${matchupIdFor(sp)}`,
      game: sp === 5 ? game(5, "GSW", true) : null,
    })),
    by_matchup: [
      { matchup_id: 1, label: "Matchup 1", start: isoFor(1), end: isoFor(6), days: [5, 6], games: 1 },
      { matchup_id: 2, label: "Matchup 2", start: isoFor(7), end: isoFor(13), days: [7, 8, 9, 10, 11], games: 0 },
    ],
  };
}

/* --- the waiver plan (task 31) ----------------------------------------------------- */

export function ref(id: number, name: string): PlanPlayerRef {
  return { id, name, nba_team: "MIA", positions: ["PF"], injury_status: null };
}

export const GOBERT = ref(3, "No Game Center");
export const GRANT = ref(201, "Jerami Grant");
export const GREEN = ref(202, "Draymond Green");
export const WHITMORE = ref(203, "Cam Whitmore");

/** A planned move on `sp`, dropping a named player, with candidates in rank order. */
export function planMove(
  overrides: Partial<PlanMove> & Pick<PlanMove, "id" | "sp"> & { pickups?: PlanPlayerRef[] },
): PlanMove {
  const { pickups = [GRANT], ...rest } = overrides;
  const sp = rest.sp;
  return {
    date: isoFor(sp),
    matchup_id: matchupIdFor(sp),
    sequence: 0,
    status: "planned",
    // The old meaning, so a fixture move lands on its own day unless a test says otherwise.
    timing: "before_games",
    lands_sp: sp,
    overdue: false,
    drop: { player: GOBERT, alternatives: [], from_move_id: null, problem: null, display: GOBERT.name },
    candidates: pickups.map((player, index) => ({ rank: index + 1, player })),
    pickup: pickups[0] ?? null,
    alternatives: pickups.slice(1),
    resolved_player_id: null,
    claim_pending: false,
    espn_transaction_id: null,
    note: null,
    created_at: "2026-10-16T00:00:00Z",
    updated_at: "2026-10-16T00:00:00Z",
    stream: null,
    ...rest,
  };
}

/** A chained drop: whoever move `fromId` picks up. */
export function chainedDrop(fromId: number, display: string, alternatives: PlanPlayerRef[] = []): PlanDrop {
  return { player: null, alternatives, from_move_id: fromId, problem: null, display };
}

export function acquisitionRow(matchupId: number, overrides: Partial<PlanAcquisitions> = {}): PlanAcquisitions {
  return {
    matchup_id: matchupId,
    label: `Matchup ${matchupId}`,
    used: 0,
    used_source: "espn",
    planned: 0,
    limit: 7,
    over: false,
    ...overrides,
  };
}

export function planResponse(overrides: Partial<PlanResponse> = {}): PlanResponse {
  return { season: 2027, today_sp: 1, moves: [], warnings: [], acquisitions: [acquisitionRow(1)], ...overrides };
}

export function warning(overrides: Partial<PlanWarning> & Pick<PlanWarning, "code" | "message">): PlanWarning {
  return { severity: "warning", move_id: null, matchup_id: null, sp: null, detail: {}, ...overrides };
}

/* --- starts (task 35) --------------------------------------------------------------- */

type DayNumbers = [filled: number, possible: number, proj: number, asSet: number];

/** Matchup 1's days in the Actual view: day 3 has 3 open and 1 benched, day 5 2 benched. */
const ACTUAL_STARTS: Record<number, DayNumbers> = {
  1: [5, 5, 250, 250],
  2: [7, 7, 420.4, 400],
  3: [3, 4, 180, 140],
  4: [7, 7, 430, 430],
  5: [4, 6, 330, 245],
  6: [7, 7, 530, 530],
};
/** The plan adds a pickup who fills one of day 3's open starts. */
const PLAN_STARTS: Record<number, DayNumbers> = { ...ACTUAL_STARTS, 3: [4, 5, 260, 220] };

function startsDay(sp: number, [filled, possible, proj, asSet]: DayNumbers): StartsDay {
  const date = isoFor(sp);
  return {
    sp,
    date,
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(`${date}T00:00:00Z`).getUTCDay()]!,
    past: false,
    slots: 7,
    filled,
    possible,
    empty: 7 - possible,
    left_on_bench: possible - filled,
    benched_with_games: sp === 2 ? [{ id: 5, name: "Bench Wing" }] : [],
    projected_points: proj,
    projected_points_as_set: asSet,
  };
}

function totalsOf(days: StartsDay[]): StartsTotals {
  const sum = (pick: (d: StartsDay) => number) => days.reduce((n, d) => n + pick(d), 0);
  return {
    days: days.length,
    max: sum((d) => d.slots),
    filled: sum((d) => d.filled),
    possible: sum((d) => d.possible),
    empty: sum((d) => d.empty),
    left_on_bench: sum((d) => d.left_on_bench),
    projected_points: sum((d) => d.projected_points),
    projected_points_as_set: sum((d) => d.projected_points_as_set),
  };
}

/**
 * `GET /team/starts` for a matchup and view. Matchup 1 has the numbers above (Actual: 33/42,
 * 6 open, 3 benched, Proj 2,140 (as set 1,995)); other matchups are 7/7 every day.
 */
export function startsFor(matchupId = 1, view: "actual" | "plan" = "actual"): StartsResponse {
  const matchup = MATCHUPS.find((m) => m.id === matchupId)!;
  const sps = Array.from({ length: matchup.last_sp - matchup.first_sp + 1 }, (_, i) => matchup.first_sp + i);
  const pick = (table: Record<number, DayNumbers>) =>
    sps.map((sp) => startsDay(sp, matchupId === 1 ? table[sp]! : [7, 7, 400, 400]));
  const actual = pick(ACTUAL_STARTS);
  const days = view === "plan" ? pick(PLAN_STARTS) : actual;
  const totals = totalsOf(days);
  const actualTotals = totalsOf(actual);
  return {
    season: 2027,
    matchup: { id: matchup.id, label: matchup.label, start: matchup.start, end: matchup.end },
    view,
    days,
    totals,
    actual_totals: view === "plan" ? actualTotals : null,
    vs_actual:
      view === "plan"
        ? {
            filled: totals.filled - actualTotals.filled,
            possible: totals.possible - actualTotals.possible,
            projected_points: totals.projected_points - actualTotals.projected_points,
            projected_points_as_set: totals.projected_points_as_set - actualTotals.projected_points_as_set,
          }
        : null,
  };
}
