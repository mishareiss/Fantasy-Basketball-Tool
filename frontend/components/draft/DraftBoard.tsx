"use client";

import { useMemo } from "react";

import type { DraftPickRow, DraftStateResponse } from "@/lib/api";
import { cellPickNumber, picksByNumber } from "@/lib/draft";

/**
 * The board: a rounds × teams grid, with every pick in the square that owns it.
 *
 * ONE MAPPING holds this page up — cell (round, team) -> pick number — and it is the snake,
 * so the squares of an even round count from the other end. It lives in `lib/draft.ts` with
 * a test rather than inline here, because a grid filled with real picks in the wrong columns
 * still looks like a working board (see that module's header).
 *
 * A CSS grid rather than a table: the cells are the interaction (click a filled one to edit
 * it) and the column a seat owns has to read as a column at a glance, which is what the
 * highlight on my seat is for. Ten columns don't fit a phone, so the whole thing scrolls
 * sideways inside its own box rather than squeezing the names.
 *
 * Clicking a FILLED cell opens the edit affordance. Empty cells are not buttons: a draft has
 * one clock, so the only future pick anyone can make is the one on it, and that is what the
 * search box under the board is.
 */

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

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <div
        role="grid"
        aria-label={`Draft board, ${rounds} rounds by ${teams} teams`}
        aria-rowcount={rounds + 1}
        aria-colcount={teams + 1}
        className="min-w-max"
      >
        <div role="row" className="flex bg-zinc-50 dark:bg-zinc-900/95">
          <div
            role="columnheader"
            className="w-10 shrink-0 border-b border-zinc-200 px-2 py-1.5 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase dark:border-zinc-800"
          >
            Rd
          </div>
          {Array.from({ length: teams }, (_, index) => index + 1).map((slot) => (
            <div
              key={slot}
              role="columnheader"
              data-team={slot}
              title={slot === mySlot ? "Your seat" : `Team ${slot}`}
              className={`w-36 shrink-0 border-b border-l border-zinc-200 px-2 py-1.5 text-[10px] font-semibold tracking-wide uppercase dark:border-zinc-800 ${
                slot === mySlot
                  ? "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200"
                  : "text-zinc-500"
              }`}
            >
              {slot === mySlot ? `Team ${slot} · you` : `Team ${slot}`}
            </div>
          ))}
        </div>

        {Array.from({ length: rounds }, (_, index) => index + 1).map((round) => (
          <div role="row" key={round} className="flex">
            <div
              role="rowheader"
              className="w-10 shrink-0 border-b border-zinc-200 px-2 py-2 text-right font-mono text-[11px] text-zinc-500 dark:border-zinc-800"
            >
              {round}
            </div>
            {Array.from({ length: teams }, (_, index) => index + 1).map((slot) => {
              const number = cellPickNumber(round, slot, teams);
              return (
                <DraftCell
                  key={slot}
                  pickNumber={number}
                  pick={byNumber.get(number) ?? null}
                  isMine={slot === mySlot}
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
  const cell = `w-36 shrink-0 border-b border-l border-zinc-200 dark:border-zinc-800 ${tint} ${ring}`;

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
        <span className="block px-2 py-1.5 font-mono text-[10px] text-zinc-300 dark:text-zinc-700">
          {pickNumber}
          <span className="block h-[30px]" aria-hidden />
        </span>
      ) : (
        <button
          type="button"
          disabled={isDisabled}
          onClick={() => onEdit(pickNumber)}
          aria-expanded={isOpen}
          title={`Pick ${pickNumber} — ${pick.name}. Click to change who was taken here.`}
          className={`block w-full px-2 py-1.5 text-left transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed dark:hover:bg-zinc-900 ${
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
                className="text-[10px] text-zinc-400"
              >
                ●
              </span>
            ) : null}
          </span>
          <span className="block truncate text-[12px] font-medium text-zinc-800 dark:text-zinc-200">
            {pick.name}
          </span>
          <span className="block truncate font-mono text-[10px] text-zinc-500">
            {pick.positions.join("/") || "—"}
          </span>
        </button>
      )}
    </div>
  );
}
