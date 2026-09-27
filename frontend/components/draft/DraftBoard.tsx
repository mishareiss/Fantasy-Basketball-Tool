"use client";

import { useMemo } from "react";

import type { DraftPickRow, DraftStateResponse, DraftTeamRow } from "@/lib/api";
import { cellPickNumber, picksByNumber } from "@/lib/draft";

/**
 * The board: a rounds × teams grid, with every pick in the square that owns it.
 *
 * ONE MAPPING holds this page up — cell (round, team) -> pick number — and it is the snake,
 * so the squares of an even round count from the other end. It lives in `lib/draft.ts` with
 * a test rather than inline here, because a grid filled with real picks in the wrong columns
 * still looks like a working board (see that module's header).
 *
 * IT FITS. Every team column is on screen at once and the board never scrolls sideways,
 * which is the whole point of the grid template below: one fixed round-number column and
 * then `team_count` columns of `minmax(0, 1fr)`, so ten seats divide whatever width the page
 * has rather than claiming a fixed one each. `minmax(0, …)` and not `1fr` alone because a
 * grid track's default minimum is its content, and one long name would otherwise push the
 * row wider than the page — names TRUNCATE instead, which is the trade this view makes: the
 * shape of the draft at a glance, with the full name a hover away. The count comes in as a
 * CSS custom property rather than a Tailwind class per size, because `team_count` is a
 * number from the server and there is no class list that covers every league.
 *
 * Twenty rounds still scroll VERTICALLY, which is fine — a board is read down a column, and
 * the header row stays put while it does.
 *
 * Clicking a FILLED cell opens the edit affordance. Empty cells are not buttons: a draft has
 * one clock, so the only future pick anyone can make is the one on it, and that is what the
 * search box above the board is.
 */

/**
 * One row of the board: the round number, then one equal track per seat.
 *
 * The same template on every row rather than a subgrid off the container, because every
 * track here is either fixed or `1fr` — so identical templates on equal-width rows line up
 * by construction, with nothing depending on how a browser resolves subgrid.
 */
const ROW = "grid w-full grid-cols-[2.25rem_repeat(var(--teams),minmax(0,1fr))]";

export function DraftBoard({
  state,
  editing,
  onEdit,
  isDisabled = false,
}: {
  state: DraftStateResponse;
  /** The pick number whose edit panel is open, so its cell can say so. */
  editing: number | null;
  onEdit: (pickNumber: number) => void;
  isDisabled?: boolean;
}) {
  const { team_count: teams, rounds, my_slot: mySlot, next_pick_number: onTheClock } = state;
  const byNumber = useMemo(() => picksByNumber(state.log), [state.log]);
  const seats = useMemo(() => seatRows(state), [state]);

  return (
    <div className="max-h-[70vh] overflow-y-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <div
        role="grid"
        aria-label={`Draft board, ${rounds} rounds by ${teams} teams`}
        aria-rowcount={rounds + 1}
        aria-colcount={teams + 1}
        data-board="fluid"
        // The custom property the rows' template is written in: see the header. It is set
        // once here and inherited, so every row is laid out on the same tracks.
        style={{ "--teams": teams } as React.CSSProperties}
        className="w-full"
      >
        <div
          role="row"
          className={`${ROW} sticky top-0 z-10 bg-zinc-50 dark:bg-zinc-900`}
        >
          <div
            role="columnheader"
            className="border-b border-zinc-200 px-1 py-1.5 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase dark:border-zinc-800"
          >
            Rd
          </div>
          {seats.map((seat) => (
            <div
              key={seat.team_slot}
              role="columnheader"
              data-team={seat.team_slot}
              title={
                seat.is_me ? `${seat.name} — your seat` : `${seat.name} (seat ${seat.team_slot})`
              }
              className={`min-w-0 truncate border-b border-l border-zinc-200 px-1 py-1.5 text-[10px] font-semibold tracking-wide dark:border-zinc-800 ${
                seat.is_me
                  ? "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200"
                  : "text-zinc-500"
              }`}
            >
              {seat.name}
              {seat.is_me ? " (You)" : ""}
            </div>
          ))}
        </div>

        {Array.from({ length: rounds }, (_, index) => index + 1).map((round) => (
          <div role="row" key={round} className={ROW}>
            <div
              role="rowheader"
              className="border-b border-zinc-200 px-1 py-2 text-right font-mono text-[10px] text-zinc-500 dark:border-zinc-800"
            >
              {round}
            </div>
            {seats.map((seat) => {
              const number = cellPickNumber(round, seat.team_slot, teams);
              return (
                <DraftCell
                  key={seat.team_slot}
                  pickNumber={number}
                  pick={byNumber.get(number) ?? null}
                  isMine={seat.team_slot === mySlot}
                  isOnClock={number === onTheClock}
                  isOpen={number === editing}
                  isDisabled={isDisabled}
                  onEdit={onEdit}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Every seat, in slot order, whatever the response happened to list.
 *
 * `teams` is the source of the NAMES and the server builds it 1..team_count, but the columns
 * are the snake's and the snake counts slots — so a seat the response didn't carry still gets
 * a column rather than shifting every column after it by one.
 */
export function seatRows(state: DraftStateResponse): DraftTeamRow[] {
  const bySlot = new Map(state.teams.map((team) => [team.team_slot, team]));
  return Array.from({ length: state.team_count }, (_, index) => index + 1).map(
    (slot) =>
      bySlot.get(slot) ?? {
        team_slot: slot,
        name: `Team ${slot}`,
        is_me: slot === state.my_slot,
        player_ids: [],
        open_needs: [],
      },
  );
}

type CellProps = {
  pickNumber: number;
  /** The pick made here, or null for a square the draft hasn't reached. */
  pick: DraftPickRow | null;
  isMine: boolean;
  isOnClock: boolean;
  isOpen: boolean;
  isDisabled: boolean;
  onEdit: (pickNumber: number) => void;
};

function DraftCell({ pickNumber, pick, isMine, isOnClock, isOpen, isDisabled, onEdit }: CellProps) {
  // My column stays tinted whether or not the square is filled — it is the thing you scan
  // the board for. The clock gets a ring, which reads on top of the tint.
  const tint = isMine ? "bg-sky-50 dark:bg-sky-500/10" : "bg-white dark:bg-zinc-950";
  const ring = isOnClock ? "ring-2 ring-inset ring-amber-500 dark:ring-amber-400" : "";
  const cell = `min-w-0 border-b border-l border-zinc-200 dark:border-zinc-800 ${tint} ${ring}`;

  // The gridcell is the square; the button is INSIDE it rather than being it, so a screen
  // reader hears "button" on a pick you can edit and nothing on one you can't.
  return (
    <div
      role="gridcell"
      data-cell={pickNumber}
      data-player={pick?.espn_player_id}
      className={cell}
    >
      {pick === null ? (
        <span className="block px-1 py-1 font-mono text-[10px] text-zinc-300 dark:text-zinc-700">
          {pickNumber}
          <span className="block h-[22px]" aria-hidden />
        </span>
      ) : (
        <button
          type="button"
          disabled={isDisabled}
          onClick={() => onEdit(pickNumber)}
          aria-expanded={isOpen}
          title={`Pick ${pickNumber} — ${pick.name}. Click to change who was taken here.`}
          className={`block w-full px-1 py-1 text-left transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed dark:hover:bg-zinc-900 ${
            isOpen ? "ring-2 ring-inset ring-sky-500" : ""
          }`}
        >
          <span className="flex items-baseline justify-between gap-1">
            <span className="font-mono text-[10px] text-zinc-400">{pickNumber}</span>
            {/* Where the pick came from, quietly: a dot for the room's own, nothing for a
                name somebody typed. It changes nothing about the pick — undo, edit and
                replay treat the two identically — so it is a mark and not a badge. */}
            {pick.is_auto ? (
              <span
                data-auto
                title="The simulated room took him"
                className="text-[9px] text-zinc-400"
              >
                ●
              </span>
            ) : null}
          </span>
          <span className="block truncate text-[11px] font-medium text-zinc-800 dark:text-zinc-200">
            {pick.name}
          </span>
          <span className="block truncate font-mono text-[9px] text-zinc-500">
            {pick.positions.join("/") || "—"}
          </span>
        </button>
      )}
    </div>
  );
}
