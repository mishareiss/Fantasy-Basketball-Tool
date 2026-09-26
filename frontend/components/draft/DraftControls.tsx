"use client";

import { Segment, Segmented } from "@/components/board/BoardControls";
import { DRAFT_MODES, type DraftMode, type DraftStateResponse } from "@/lib/api";
import { MODE_LABEL } from "./DraftSetup";
import { QUIET_BUTTON } from "./DraftStates";

/**
 * The clock, and the buttons that move it.
 *
 * MODE IS A UI PREFERENCE and the backend says so: every verb works under either, so this
 * toggle decides which controls are on screen and nothing else. It is not persisted, because
 * there is no endpoint that persists it — the draft stores the mode it was STARTED in, and
 * flipping this is "I am running the rest of it by hand", not a write.
 *
 * The advance buttons are simulation-only for that reason. Undo, reset and the search box
 * are in both: correcting a mis-entry in a simulated draft, and auto-picking a stalled room
 * in a manual one, are both things that happen.
 */

export function OnTheClock({ state }: { state: DraftStateResponse }) {
  if (state.is_complete) {
    return (
      <div
        role="status"
        data-clock="complete"
        className="rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900"
      >
        <p className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">Draft complete</p>
        <p className="text-xs text-zinc-500">
          All {state.total_picks} picks are in. Undo takes the last one back; reset starts the
          same draft over.
        </p>
      </div>
    );
  }

  const mine = state.is_my_pick;
  return (
    <div
      role="status"
      data-clock={mine ? "mine" : "theirs"}
      className={`flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg border px-4 py-3 ${
        mine
          ? "border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10"
          : "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
      }`}
    >
      <p
        className={`text-sm font-semibold ${
          mine ? "text-amber-900 dark:text-amber-200" : "text-zinc-800 dark:text-zinc-200"
        }`}
      >
        {mine ? "YOUR PICK" : `Team ${state.on_the_clock} on the clock`}
      </p>
      <p className="font-mono text-xs text-zinc-500">
        pick {state.next_pick_number} · round {state.current_round} · {state.picks_made} of{" "}
        {state.total_picks} made
      </p>
      {!mine && state.my_remaining_pick_numbers.length > 0 ? (
        <p className="text-xs text-zinc-500">
          Yours next at {state.my_remaining_pick_numbers[0]} —{" "}
          {state.my_remaining_pick_numbers[0] - (state.next_pick_number ?? 0)} picks away.
        </p>
      ) : null}
    </div>
  );
}

export type DraftControlsProps = {
  state: DraftStateResponse;
  mode: DraftMode;
  onMode: (mode: DraftMode) => void;
  isBusy: boolean;
  onAdvance: () => void;
  onStep: () => void;
  onUndo: () => void;
  onReset: () => void;
  onReconfigure: () => void;
};

export function DraftControls({
  state,
  mode,
  onMode,
  isBusy,
  onAdvance,
  onStep,
  onUndo,
  onReset,
  onReconfigure,
}: DraftControlsProps) {
  // The advance has nothing to do when I am already up — the engine stops at my seat, so it
  // would answer 200 with an empty list. Saying so on the button beats spinning.
  const nothingToAdvance = state.is_my_pick || state.is_complete;

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Mode">
          {DRAFT_MODES.map((option) => (
            <Segment
              key={option}
              active={mode === option}
              onClick={() => onMode(option)}
              title={
                option === "simulation"
                  ? "Show the controls that let the other seats draft themselves."
                  : "Hide them: every pick, including the room's, gets typed in."
              }
            >
              {MODE_LABEL[option]}
            </Segment>
          ))}
        </Segmented>

        {mode === "simulation" ? (
          <>
            <button
              type="button"
              onClick={onAdvance}
              disabled={isBusy || nothingToAdvance}
              title={
                nothingToAdvance
                  ? "The room is waiting on you — it never picks for your seat."
                  : "Let the other seats draft until your next pick, and commit what they took."
              }
              className={QUIET_BUTTON}
            >
              Advance to my pick
            </button>
            <button
              type="button"
              onClick={onStep}
              disabled={isBusy || nothingToAdvance}
              title="One opponent pick, so you can watch the room name by name."
              className={QUIET_BUTTON}
            >
              Step one pick
            </button>
          </>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onUndo}
          disabled={isBusy || state.picks_made === 0}
          title="Take the last pick back, whoever made it. Called once per pick, it re-rolls an advance."
          className={QUIET_BUTTON}
        >
          Undo last pick
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={isBusy || state.picks_made === 0}
          title="Throw every pick away and start this same draft from pick 1."
          className={`${QUIET_BUTTON} hover:bg-rose-50 hover:text-rose-700 dark:hover:bg-rose-500/10 dark:hover:text-rose-300`}
        >
          Start over
        </button>
        <button
          type="button"
          onClick={onReconfigure}
          disabled={isBusy}
          title="Change the seat or the mode — it replaces the draft, so every pick in it goes."
          className={QUIET_BUTTON}
        >
          Reconfigure…
        </button>
      </div>
    </div>
  );
}
