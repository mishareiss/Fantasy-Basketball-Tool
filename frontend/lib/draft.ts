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
