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

import type { DraftPickRow, DraftStateResponse, MasterPlayerRow } from "@/lib/api";
import { matches } from "@/lib/masterboard";

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

/** The sentence the chip carries in its tooltip and to a screen reader. */
export function availabilityDescription(value: number, picksAway: number): string {
  const percent = availabilityPercent(value);
  if (picksAway === 0) {
    return `He is on the board right now — this is your pick (${percent}%)`;
  }
  const waiting = `${picksAway} ${picksAway === 1 ? "pick" : "picks"} from now`;
  return (
    `${percent}% of simulated rooms still had him when this pick came up, ${waiting}. ` +
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
