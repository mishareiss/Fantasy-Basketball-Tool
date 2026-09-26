"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { MasterPlayerRow } from "@/lib/api";
import { candidates } from "@/lib/draft";
import { FIELD } from "./DraftStates";

/**
 * How a name gets into the draft — the one control that enters every pick in the room.
 *
 * There is one of these and it does two jobs, because in a snake there is only ever one seat
 * that can pick: the search under the board drafts for whoever is ON THE CLOCK, which is my
 * own pick when it is mine and TEAM SEVEN'S when it is team seven's. That is how manual mode
 * works — you type in each opponent's pick as it happens and the clock walks itself along.
 * The same component, opened from a filled cell, is the edit affordance for a pick already
 * made.
 *
 * WHAT IT OFFERS is the master board minus whoever has been drafted, and no narrower (see
 * `candidates` in lib/draft.ts). A player no source ranks is not in the draft's universe and
 * `POST /draft/picks` refuses him with a 422 naming him — which is a better answer than his
 * quietly not being in this list, so the list does not try to predict that refusal.
 */

export type PickSearchProps = {
  label: string;
  hint: React.ReactNode;
  players: MasterPlayerRow[];
  drafted: Set<number>;
  onChoose: (playerId: number) => void;
  isDisabled: boolean;
  /** The edit panel opens under a cell you just clicked; the standing box does not steal focus. */
  shouldAutoFocus?: boolean;
};

/**
 * NOTE ON RESETTING: the term is not cleared by an effect watching the pick number — the
 * parent gives this a `key` that changes with the pick, so a new pick is a NEW box with an
 * empty term and its own focus. That is React's own answer to "reset state when a prop
 * changes", and it keeps the only state here (what has been typed) uninterested in the
 * draft entirely.
 */

export function PickSearch({
  label,
  hint,
  players,
  drafted,
  onChoose,
  isDisabled,
  shouldAutoFocus = false,
}: PickSearchProps) {
  const [term, setTerm] = useState("");
  const input = useRef<HTMLInputElement | null>(null);
  const inputId = useId();

  useEffect(() => {
    // Mount only, and only where the caller asked: the edit panel appears under a cell you
    // just clicked, so the cursor belongs in it. The standing box under the board does not
    // steal focus — you might be reading the grid.
    if (shouldAutoFocus) input.current?.focus();
  }, [shouldAutoFocus]);

  const found = useMemo(
    () => candidates(players, drafted, term),
    [players, drafted, term],
  );
  const searching = term.trim() !== "";

  function choose(playerId: number) {
    setTerm("");
    onChoose(playerId);
  }

  return (
    <div className="flex flex-col gap-2">
      <label
        htmlFor={inputId}
        className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
      >
        {label}
      </label>
      <input
        id={inputId}
        ref={input}
        value={term}
        placeholder="type a name"
        disabled={isDisabled}
        autoComplete="off"
        onChange={(event) => setTerm(event.target.value)}
        onKeyDown={(event) => {
          // Enter takes the one obvious answer and nothing else: with two names matching,
          // guessing which one you meant is how the wrong player ends up on a roster.
          if (event.key === "Enter" && found.length === 1) {
            event.preventDefault();
            choose(found[0].espn_player_id);
          }
        }}
        className={`${FIELD} w-full max-w-sm`}
      />
      <p className="text-xs text-zinc-500">{hint}</p>

      {searching ? (
        found.length === 0 ? (
          <p className="text-xs text-zinc-500">
            Nobody on your board matches “{term.trim()}” who isn’t already drafted.
          </p>
        ) : (
          <ul className="flex max-w-sm flex-col overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-800">
            {found.map((player) => (
              <li key={player.espn_player_id}>
                <button
                  type="button"
                  disabled={isDisabled}
                  onClick={() => choose(player.espn_player_id)}
                  data-candidate={player.espn_player_id}
                  className="flex w-full items-baseline justify-between gap-3 border-b border-zinc-200 px-3 py-1.5 text-left text-sm last:border-b-0 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
                >
                  <span className="truncate text-zinc-800 dark:text-zinc-200">
                    {player.name}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-zinc-500">
                    {player.rank === null ? "—" : `#${player.rank}`}{" "}
                    {player.positions.join("/")}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}
