/**
 * The draft room's arithmetic: which pick a grid cell is, and who is still on the board.
 *
 * The board on screen is a `rounds x team_count` grid, and everything about it hangs off one
 * mapping — cell (round, team) -> pick number. THE SNAKE is that mapping and it is the only
 * part of the page that can be quietly wrong: odd rounds run team 1..T, even rounds run
 * T..1, so a cell's pick number is not `(round - 1) * teams + team` half the time. Getting it
 * backwards would fill the board with real picks in the wrong squares, which looks like a
 * working page. Hence a pure module with a test, mirroring `app/draft/config.py:_snake`.
 *
 * Everything here is pure: the page passes in the state it was handed and gets arrays and
 * maps back, so the grid and the search can both be exercised without a DOM.
 */

import {
  POSITIONS,
  SCOPE_OVERALL,
  type DraftPickRow,
  type DraftStateResponse,
  type MasterPlayerRow,
  type Position,
  type TierScope,
  type TierScopeRow,
} from "@/lib/api";
import { matches, scopeTiers, tierAt } from "@/lib/masterboard";

/** How many names the pick-entry search offers at once. Enough to see the one you meant. */
export const SEARCH_LIMIT = 12;

/** One square of the board: which round, which seat, and the pick number it owns. */
export type Cell = { round: number; teamSlot: number };

/**
 * The pick number a cell owns. Round 1 runs left to right, round 2 runs right to left.
 *
 * Out of range throws rather than clamping, the same choice `DraftConfig.pick_slot` makes:
 * a cell outside the grid is a bug in whoever built the grid, and returning pick 1 for it
 * would hide that behind a square that looks plausible.
 */
export function cellPickNumber(round: number, teamSlot: number, teamCount: number): number {
  if (!Number.isInteger(teamCount) || teamCount < 1) {
    throw new Error(`teamCount ${teamCount} must be an integer >= 1`);
  }
  if (!Number.isInteger(round) || round < 1) {
    throw new Error(`round ${round} must be an integer >= 1`);
  }
  if (!Number.isInteger(teamSlot) || teamSlot < 1 || teamSlot > teamCount) {
    throw new Error(`teamSlot ${teamSlot} is outside 1..${teamCount}`);
  }
  // The even round's seats are numbered from the other end, which is the whole snake.
  const withinRound = round % 2 === 1 ? teamSlot : teamCount - teamSlot + 1;
  return (round - 1) * teamCount + withinRound;
}

/** The inverse: which square a pick number lands in. */
export function cellOf(pickNumber: number, teamCount: number): Cell {
  if (!Number.isInteger(teamCount) || teamCount < 1) {
    throw new Error(`teamCount ${teamCount} must be an integer >= 1`);
  }
  if (!Number.isInteger(pickNumber) || pickNumber < 1) {
    throw new Error(`pickNumber ${pickNumber} must be an integer >= 1`);
  }
  const round = Math.floor((pickNumber - 1) / teamCount) + 1;
  const withinRound = (pickNumber - 1) % teamCount; // 0-based place in the round
  return {
    round,
    teamSlot: round % 2 === 1 ? withinRound + 1 : teamCount - withinRound,
  };
}

/** The round a pick number falls in — the same arithmetic, without the seat. */
export function roundOf(pickNumber: number, teamCount: number): number {
  return cellOf(pickNumber, teamCount).round;
}

/**
 * Every pick number a seat owns, in order. My column, when it is asked about my seat.
 *
 * At slot 2 of 10 this is [2, 19, 22, 39, 42, ...] — the alternating 17-then-3 wait, which is
 * what makes the highlighted column worth drawing at all.
 */
export function pickNumbersFor(teamSlot: number, teamCount: number, rounds: number): number[] {
  return Array.from({ length: rounds }, (_, index) =>
    cellPickNumber(index + 1, teamSlot, teamCount),
  );
}

/** The log, indexed by pick number — what each square looks itself up in. */
export function picksByNumber(log: DraftPickRow[]): Map<number, DraftPickRow> {
  return new Map(log.map((pick) => [pick.pick_number, pick]));
}

/** Everyone taken so far, by player id. Read off the LIVE state, so it is never stale. */
export function draftedIds(state: DraftStateResponse): Set<number> {
  return new Set(state.log.map((pick) => pick.espn_player_id));
}

/**
 * Who the search offers: the catalog, minus whoever is gone, matching the term.
 *
 * The catalog is the whole master board — every name we hold — and the only thing subtracted
 * from it is the drafted set. It is deliberately NOT narrowed to the field's universe: a
 * player nobody ranks is not draftable and `POST /draft/picks` 422s for him, and that refusal
 * naming him is more useful than his quietly not being in the list. The one pre-filter is the
 * one the page can be certain about, because it holds the log.
 *
 * Empty term means no candidates rather than the whole board: this is a "type a name" box,
 * not a list to scroll.
 */
export function candidates(
  players: MasterPlayerRow[],
  drafted: Set<number>,
  term: string,
  limit: number = SEARCH_LIMIT,
): MasterPlayerRow[] {
  if (term.trim() === "") return [];
  const found: MasterPlayerRow[] = [];
  for (const player of players) {
    if (drafted.has(player.espn_player_id)) continue;
    if (!matches(player, term)) continue;
    found.push(player);
    if (found.length === limit) break;
  }
  return found;
}

/* --- the plan's one number ---------------------------------------------------------------- *
 *
 * `availability` is the only figure on this site that is a probability, and the page has to
 * make it readable at a glance without ever making the colour the thing that says it. So:
 * three bands, each with its own word, and the percentage printed beside all of them. The
 * ramp mirrors `EDGE_*` in lib/masterboard.ts — same shape, different question.
 * ------------------------------------------------------------------------------------------ */

/** How a chance of surviving to a pick reads: calm, a coin-flip, or act now. */
export type AvailabilityTone = "likely" | "even" | "unlikely";

/**
 * Which band a [0, 1] availability falls in.
 *
 * Two thirds and one third, so "even" is genuinely the middle and not a sliver. Anything
 * outside [0, 1] is clamped rather than thrown for: a percentage is a display, and a
 * backend that ever hands back 1.0000001 should not blank the panel.
 */
export function availabilityTone(value: number): AvailabilityTone {
  if (value >= 2 / 3) return "likely";
  if (value >= 1 / 3) return "even";
  return "unlikely";
}

/** The percentage, rounded the way it is printed. Clamped into 0..100. */
export function availabilityPercent(value: number): number {
  return Math.round(Math.min(1, Math.max(0, value)) * 100);
}

export const AVAILABILITY_LABEL: Record<AvailabilityTone, string> = {
  likely: "likely there",
  even: "coin-flip",
  unlikely: "likely gone",
};

/** The bar's fill. Emerald / amber / rose, the app's own ramp — never the only signal. */
export const AVAILABILITY_FILL: Record<AvailabilityTone, string> = {
  likely: "bg-emerald-500 dark:bg-emerald-400",
  even: "bg-amber-500 dark:bg-amber-400",
  unlikely: "bg-rose-500 dark:bg-rose-400",
};

/** The number's own colour, so the figure reads as urgent even with the bar ignored. */
export const AVAILABILITY_TEXT: Record<AvailabilityTone, string> = {
  likely: "text-emerald-700 dark:text-emerald-300",
  even: "text-amber-700 dark:text-amber-300",
  unlikely: "text-rose-700 dark:text-rose-300",
};

/**
 * The sentence the chip carries in its tooltip and to a screen reader.
 *
 * `pickNumber` is named whenever there is one, because the number is ALWAYS about a later pick
 * than the clock — my next one while I am waiting, the one after this one while I am on the
 * clock — and "42%" read as a statement about the pick in front of me is the one way this
 * figure can mislead. The `picksAway === 0` branch is the degenerate case the backend no
 * longer produces, kept because a tooltip is not the place to throw.
 */
export function availabilityDescription(
  value: number,
  picksAway: number,
  pickNumber: number | null = null,
): string {
  const percent = availabilityPercent(value);
  const pick = pickNumber === null ? "this pick" : `pick ${pickNumber}`;
  if (picksAway <= 0) {
    return `He is on the board right now — ${pick} is on the clock (${percent}%)`;
  }
  const waiting = `${picksAway} ${picksAway === 1 ? "pick" : "picks"} from now`;
  return (
    `${percent}% of simulated rooms still had him when ${pick} came up, ${waiting}. ` +
    "Computed as though you take nobody in between, so it can only overstate who survives."
  );
}

/**
 * How many of my remaining picks the plan should ask for next.
 *
 * The panels open on the next few rather than all twenty: planning from pick 1 simulates
 * nearly the whole draft a thousand times, and the answer for round 14 is not a decision
 * anybody is making on the clock. "Show more" walks it out a step at a time, and stops once
 * every remaining pick is on screen.
 */
export const PLAN_PICKS = 4;

export function morePlanPicks(wanted: number, remaining: number): number | null {
  const next = Math.min(wanted + PLAN_PICKS, remaining);
  return next > wanted ? next : null;
}

/* --- the available board, and the columns drawn off it ------------------------------------ *
 *
 * The sidebar and the rankings view are the same two ingredients joined six different ways:
 * MY board (the catalog — ranks, tags, positions and the tier cuts, all static while a draft
 * runs) minus the LIVE drafted set, which comes off the state's log and so is never stale.
 *
 * All of it is here and pure for the reason the snake is: a column that quietly drops a
 * player, or bands him into the wrong tier, still renders as a working page. A test can
 * check these against a hand-written board in a few lines; a component test could only check
 * that something was on screen.
 * ------------------------------------------------------------------------------------------ */

/**
 * The players still on my board, in my order.
 *
 * "On my board" is three conditions and each one is a different fact: he is RANKED (a
 * set-aside player has no place in the order), he is not EXCLUDED (the tray is not a draft
 * list), and nobody has TAKEN him. Sorted by rank rather than trusting the response's order,
 * because a caller can hand this a board it filtered or windowed itself.
 */
export function availableBoard(
  catalog: MasterPlayerRow[],
  drafted: Set<number>,
): MasterPlayerRow[] {
  return catalog
    .filter(
      (row) =>
        row.rank !== null && !row.excluded && !drafted.has(row.espn_player_id),
    )
    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
}

/** One row of a rankings column: the player, where he sits in the scope, and his band. */
export type ColumnRow = {
  player: MasterPlayerRow;
  /**
   * His place in the FULL scope order — drafted players INCLUDED. For 'overall' that is his
   * board rank; for a position it is his place among every player on my board listed there.
   * It is the number the tier cuts are counted in, and deliberately not his place in the
   * available list: a tier is a band over the board, and the board does not renumber itself
   * because the room took somebody out of it.
   */
  scopeRank: number;
  /** His band in this scope, 1 being the top. Null when the scope carries no dividers. */
  tier: number | null;
  /** The tier changed at this row going down the AVAILABLE list — draw a divider above it.
      True on the first row of the column whenever it has a tier at all. */
  startsTier: boolean;
};

/** One column of the rankings view: a scope, its available players, and how deep it runs. */
export type BoardColumn = {
  scope: TierScope;
  rows: ColumnRow[];
  /** How many players the FULL scope order holds, drafted ones included. */
  size: number;
};

/**
 * The available players eligible at one scope, in my board order, each carrying its tier.
 *
 * THE TIER IS A BAND OVER THE FULL ORDER, which is the one thing in here worth being careful
 * about. The third-best available point guard is not the third point guard on my board — six
 * of them may be gone — and his tier has to be the one I drew around HIM, not the one his
 * place in what's left would fall into. So the scope order is built from the whole catalog,
 * his place in it is found there, and the cuts are read at that number. Dividers then appear
 * wherever that tier increments down the available list, which is how a column with four of
 * its tier-1 guards gone still says the men at the top of it are tier 2.
 *
 * `scope` is 'overall' (board rank, board cuts) or a position (its sub-order, its own cuts).
 * A position nobody on the board plays is an empty column rather than an error.
 */
export function boardColumn(
  available: MasterPlayerRow[],
  catalog: MasterPlayerRow[],
  tiers: TierScopeRow[],
  scope: TierScope,
): BoardColumn {
  // The full order this scope's cut ranks count in: every ranked, non-excluded player on my
  // board, in board order, narrowed to the ones listed at the position.
  const ordered = catalog
    .filter((row) => row.rank !== null && !row.excluded)
    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
    .filter((row) => scope === SCOPE_OVERALL || row.positions.includes(scope));
  const scopeRanks = new Map(
    ordered.map((row, index) => [row.espn_player_id, index + 1]),
  );

  const cuts = scopeTiers(tiers, scope)?.cut_ranks ?? [];
  const rows: ColumnRow[] = [];
  let previous: number | null = null;
  for (const player of available) {
    const scopeRank = scopeRanks.get(player.espn_player_id);
    if (scopeRank === undefined) continue; // not eligible here, or not on the board at all
    const tier = tierAt(cuts, scopeRank) || null;
    rows.push({ player, scopeRank, tier, startsTier: tier !== null && tier !== previous });
    previous = tier;
  }
  return { scope, rows, size: ordered.length };
}

/** The position column, by name — `boardColumn` with the scope spelled out. */
export function positionColumn(
  available: MasterPlayerRow[],
  catalog: MasterPlayerRow[],
  tiers: TierScopeRow[],
  position: Position,
): BoardColumn {
  return boardColumn(available, catalog, tiers, position);
}

/** The rankings view's six: best available, then one column per position, in lineup order. */
export function rankingsColumns(
  available: MasterPlayerRow[],
  catalog: MasterPlayerRow[],
  tiers: TierScopeRow[],
): BoardColumn[] {
  return [SCOPE_OVERALL as TierScope, ...POSITIONS].map((scope) =>
    boardColumn(available, catalog, tiers, scope),
  );
}

/** A run of consecutive rows in one tier — what a divider heads. */
export type TierRun = { tier: number | null; rows: ColumnRow[] };

/**
 * A column, bucketed into its tiers for rendering.
 *
 * The band arithmetic is not repeated here: every row already carries the tier `tierAt` gave
 * it over the scope's stored cuts (`lib/masterboard.ts` owns that, the same way the dividers
 * on /my-board do). This only groups the runs, so a divider is drawn once per band rather
 * than tested for per row.
 */
export function tierRuns(rows: ColumnRow[]): TierRun[] {
  const runs: TierRun[] = [];
  for (const row of rows) {
    const last = runs[runs.length - 1];
    if (last === undefined || row.startsTier) runs.push({ tier: row.tier, rows: [row] });
    else last.rows.push(row);
  }
  return runs;
}

/**
 * The sidebar's three filters, applied in one pass.
 *
 * All three narrow and none of them reorders: the list is my board's order whatever is
 * showing, because "who is the best man left" is the question it exists to answer and a
 * filter is only ever about which of them are on screen. Positions are multi-select and read
 * as ANY of them — the reason to tick PG and SG is that either would do.
 */
export type SidebarFilters = {
  term: string;
  targetsOnly: boolean;
  positions: Position[];
};

export function filterAvailable(
  available: MasterPlayerRow[],
  { term, targetsOnly, positions }: SidebarFilters,
): MasterPlayerRow[] {
  return available.filter((row) => {
    if (targetsOnly && row.tag !== "target") return false;
    if (positions.length > 0 && !positions.some((spot) => row.positions.includes(spot))) {
      return false;
    }
    return matches(row, term);
  });
}

/**
 * His chance of lasting until my next pick, or null.
 *
 * Null rather than 0 for a name the map has nothing for: the field doesn't rank him, so he
 * is not in the draft's universe and there is no simulation he could have survived. "No
 * answer" and "gone in every room" are opposite things to print.
 */
export function availabilityOf(
  map: Record<string, number>,
  playerId: number,
): number | null {
  const value = map[String(playerId)];
  return value === undefined ? null : value;
}

/* --- a team's roster, laid out in slots ---------------------------------------------------- *
 *
 * The sidebar's Teams tab asks a question the board and the rosters view can't answer: not
 * "what has that seat taken" but "what does that seat's LINEUP CARD look like" — which of the
 * five dedicated spots are filled, whether the utility slots have gone, and what is on the
 * bench. That is a slot ASSIGNMENT, and the assignment is a rule rather than a fact on the
 * response: nothing stored says which slot a drafted player occupies.
 *
 * So it is computed here, and it mirrors `app/draft/needs.py:RosterFill.add` exactly, because
 * the same rule already decides `open_needs` on every seat of `GET /draft`. Two greedy
 * assignments that disagreed would put a man in the PG slot on screen while the backend
 * counted him at SG and went on calling PG a need — a page contradicting its own data.
 * ------------------------------------------------------------------------------------------ */

/** The positionless slots, named as `app/draft/config.py` names them. */
export const UTILITY_SLOT = "UT";
export const BENCH_SLOT = "BE";

/** Who is in a slot. `positions` and `name` come off the catalog; the id is the log's. */
export type SlotOccupant = { playerId: number; name: string; positions: string[] };

/** One row of a lineup card: the slot's label, and who is in it or null. */
export type RosterSlotRow = { slot: string; occupant: SlotOccupant | null };

/** The catalog, indexed — what turns a seat's `player_ids` back into names and positions. */
export function playersById(catalog: MasterPlayerRow[]): Map<number, MasterPlayerRow> {
  return new Map(catalog.map((row) => [row.espn_player_id, row]));
}

/**
 * One seat's lineup card: every slot the roster has, in lineup order, each filled or open.
 *
 * The slots run PG, SG, SF, PF, C (each as many times as `roster_slots` says), then UT, then
 * BE — the order a lineup card prints them, not the order the dict happens to be in.
 *
 * ASSIGNMENT IS GREEDY IN DRAFT ORDER and matches `RosterFill.add` step for step: each player
 * takes an open dedicated slot he is eligible for, else a utility slot, else the bench. His
 * OWN position order decides which dedicated slot, which is what the backend iterates — ESPN
 * lists positions in lineup order, so a PG/SG taken while both are open lands at PG either
 * way, but iterating anything else here would eventually disagree with `open_needs`.
 *
 * Greedy, so not optimal, and deliberately: this is the card the backend's needs are counted
 * on, not a lineup optimizer. A player past every slot is dropped rather than drawn in a slot
 * that doesn't exist — a roster deeper than its own `roster_slots` is a draft with more rounds
 * than the league has places, and inventing a bench row for him would be a fiction.
 *
 * A player the catalog has never heard of still gets his slot, listed by id: he is in the log,
 * so the seat really does hold him, and dropping him would silently shorten the card.
 */
export function teamRoster(
  playerIds: number[],
  byId: Map<number, MasterPlayerRow>,
  rosterSlots: Record<string, number>,
): RosterSlotRow[] {
  const count = (slot: string) => Math.max(0, Math.trunc(rosterSlots[slot] ?? 0));
  const rows: RosterSlotRow[] = [
    ...POSITIONS.flatMap((spot) =>
      Array.from({ length: count(spot) }, () => ({ slot: spot as string, occupant: null })),
    ),
    ...Array.from({ length: count(UTILITY_SLOT) }, () => ({
      slot: UTILITY_SLOT,
      occupant: null as SlotOccupant | null,
    })),
    ...Array.from({ length: count(BENCH_SLOT) }, () => ({
      slot: BENCH_SLOT,
      occupant: null as SlotOccupant | null,
    })),
  ];

  /** The first open row at this slot, or -1. The greedy step, over the card itself. */
  const openAt = (slot: string) =>
    rows.findIndex((row) => row.slot === slot && row.occupant === null);

  for (const playerId of playerIds) {
    const known = byId.get(playerId);
    const occupant: SlotOccupant = {
      playerId,
      name: known?.name ?? `Player ${playerId}`,
      positions: known?.positions ?? [],
    };
    let index = -1;
    for (const spot of occupant.positions) {
      index = openAt(spot.trim().toUpperCase());
      if (index !== -1) break;
    }
    if (index === -1) index = openAt(UTILITY_SLOT);
    if (index === -1) index = openAt(BENCH_SLOT);
    if (index === -1) continue; // no place left on the card; see the header
    rows[index].occupant = occupant;
  }
  return rows;
}
