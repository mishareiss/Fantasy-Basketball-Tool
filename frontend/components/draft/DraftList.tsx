"use client";

import { useMemo } from "react";

import type { DraftPickRow, DraftStateResponse } from "@/lib/api";
import { seatRows } from "./DraftBoard";

/**
 * The draft as it happened: every pick made, grouped by round, in order.
 *
 * The board answers "what does the room look like"; this answers "what has actually
 * happened", which is a different read and the one you scroll back through when somebody
 * asks who went in the third. Snake order within the round is simply pick order — the log
 * is contiguous and already sorted, so round 2 running right to left shows up here as team
 * 10 picking first, which is what it was.
 *
 * MADE PICKS ONLY. An unmade pick has no row here, because the thing this view is is a
 * record; the empty slots are on the board, where they are squares, and in the roster view,
 * where they are the numbers a team still owns.
 */

export function DraftList({ state }: { state: DraftStateResponse }) {
  const names = useMemo(
    () => new Map(seatRows(state).map((team) => [team.team_slot, team.name])),
    [state],
  );

  const log = state.log;
  const rounds = useMemo(() => {
    const grouped = new Map<number, DraftPickRow[]>();
    for (const pick of log) {
      const held = grouped.get(pick.round);
      if (held) held.push(pick);
      else grouped.set(pick.round, [pick]);
    }
    return [...grouped.entries()].sort((a, b) => a[0] - b[0]);
  }, [log]);

  if (rounds.length === 0) {
    return (
      <p
        data-list="empty"
        className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900"
      >
        No picks yet. The first one shows up here the moment it is entered.
      </p>
    );
  }

  return (
    <div data-view="list" className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto">
      {rounds.map(([round, picks]) => (
        <section key={round} data-round={round} className="flex flex-col gap-1">
          <h3 className="sticky top-0 bg-white py-1 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase dark:bg-zinc-950">
            Round {round}
          </h3>
          <ul className="flex flex-col divide-y divide-zinc-100 rounded-lg border border-zinc-200 dark:divide-zinc-900 dark:border-zinc-800">
            {picks.map((pick) => (
              <li
                key={pick.pick_number}
                data-list-pick={pick.pick_number}
                className={`flex items-baseline gap-2 px-2 py-1.5 text-sm ${
                  pick.is_mine ? "bg-sky-50 dark:bg-sky-500/10" : ""
                }`}
              >
                <span className="w-8 shrink-0 text-right font-mono text-xs text-zinc-500 tabular-nums">
                  {pick.pick_number}
                </span>
                <span className="w-28 shrink-0 truncate text-xs text-zinc-500">
                  {names.get(pick.team_slot) ?? `Team ${pick.team_slot}`}
                  {pick.is_mine ? " (You)" : ""}
                </span>
                <span className="min-w-0 flex-1 truncate font-medium text-zinc-900 dark:text-zinc-100">
                  {pick.name}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-zinc-500">
                  {pick.positions.join("/") || "—"}
                </span>
                {/* Where the pick came from, quietly — the same mark the board's cells use. */}
                {pick.is_auto ? (
                  <span
                    data-auto
                    title="The simulated room took him"
                    className="shrink-0 text-[10px] text-zinc-400"
                  >
                    ●
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
