"use client";

import { useMemo } from "react";

import type { DraftStateResponse } from "@/lib/api";
import { picksByNumber, pickNumbersFor } from "@/lib/draft";
import { seatRows } from "./DraftBoard";

/**
 * Every team's draft, down its own column: what it has taken, and what it still owns.
 *
 * The unmade picks are the point. A roster that listed only the names taken would answer
 * "who has he got" and stop; this answers "and when does he pick again", which is the
 * question you are actually asking when you look at somebody else's roster mid-draft — the
 * seat with three centres and picks 58 and 63 coming is a seat that is about to take a
 * guard. So every pick number the seat owns is a row, and the ones that haven't happened are
 * the number with a blank where the name goes.
 *
 * A seat's pick numbers come from the snake (`pickNumbersFor`), not from the log, which is
 * what lets the unmade ones exist at all — the log has nothing to say about a pick that
 * hasn't happened.
 *
 * NO DRAFT BUTTONS. This is a reference view: the only pick anybody can make is the one on
 * the clock, and the sidebar and the rankings are where that happens.
 */

export function DraftRosters({ state }: { state: DraftStateResponse }) {
  const byNumber = useMemo(() => picksByNumber(state.log), [state.log]);
  const seats = useMemo(() => seatRows(state), [state]);

  return (
    <div
      data-view="roster"
      className="grid max-h-[70vh] gap-3 overflow-y-auto sm:grid-cols-2 xl:grid-cols-3"
    >
      {seats.map((seat) => {
        const numbers = pickNumbersFor(seat.team_slot, state.team_count, state.rounds);
        const taken = numbers.filter((number) => byNumber.has(number)).length;
        return (
          <section
            key={seat.team_slot}
            data-roster-team={seat.team_slot}
            className={`flex flex-col gap-1 rounded-lg border p-2 ${
              seat.is_me
                ? "border-sky-300 bg-sky-50/60 dark:border-sky-500/40 dark:bg-sky-500/5"
                : "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
            }`}
          >
            <header className="flex items-baseline justify-between gap-2">
              <h3 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {seat.name}
                {seat.is_me ? " (You)" : ""}
              </h3>
              <span className="shrink-0 font-mono text-[10px] text-zinc-500 tabular-nums">
                {taken}/{numbers.length}
              </span>
            </header>

            {seat.open_needs.length > 0 ? (
              <p className="flex flex-wrap items-center gap-1 text-[10px] text-zinc-500">
                <span className="tracking-wide uppercase">Needs</span>
                {seat.open_needs.map((need) => (
                  <span
                    key={need}
                    data-need={need}
                    className="rounded bg-zinc-100 px-1 py-0.5 font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                  >
                    {need}
                  </span>
                ))}
              </p>
            ) : null}

            <ul className="flex flex-col">
              {numbers.map((number) => {
                const pick = byNumber.get(number) ?? null;
                return (
                  <li
                    key={number}
                    data-roster-pick={number}
                    data-unmade={pick === null ? "" : undefined}
                    className="flex items-baseline gap-2 border-b border-zinc-100 py-0.5 text-[13px] last:border-b-0 dark:border-zinc-900"
                  >
                    <span className="w-7 shrink-0 text-right font-mono text-[11px] text-zinc-400 tabular-nums">
                      {number}
                    </span>
                    {pick === null ? (
                      // The slot he still owns: the number, and a rule where the name will
                      // go. Not an em dash — this is a pick that hasn't happened, not a
                      // value we are missing.
                      <span className="min-w-0 flex-1 border-b border-dashed border-zinc-300 text-transparent dark:border-zinc-700">
                        ____
                      </span>
                    ) : (
                      <>
                        <span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-200">
                          {pick.name}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-zinc-500">
                          {pick.positions.join("/") || "—"}
                        </span>
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
