"use client";

import { useState } from "react";

import type { DraftConfigBody, DraftStateResponse } from "@/lib/api";
import { TeamNameFields, nonBlank } from "./DraftSetup";
import { PRIMARY_BUTTON, QUIET_BUTTON } from "./DraftStates";

/**
 * The seats: which one is mine, and what they are all called.
 *
 * TWO FIELDS WITH DIFFERENT RULES, and the split is the backend's rather than this panel's
 * (`PUT /draft/config`). A NAME is cosmetic — no pick, no need, no availability number reads
 * one — so it can be changed at pick 1 or pick 141. THE SEAT is not: a pick number only
 * means something under one shape, and moving my seat after pick 19 would re-label picks
 * that have already happened. So the seat input exists only while the draft is EMPTY, which
 * is the case that actually happens — "I set it to 2 and I'm really at 7", noticed before
 * the room starts — and costs nothing to fix, because there is nothing to lose. Once a pick
 * is in, the seat is only movable by Reconfigure, which says out loud that it throws the
 * picks away.
 *
 * Open by default on an empty draft (it is the thing you are doing at that moment) and
 * folded away once the room is running, where it is a rename and not a setup step.
 */

export function DraftSeats({
  state,
  isBusy,
  onSave,
}: {
  state: DraftStateResponse;
  isBusy: boolean;
  onSave: (body: DraftConfigBody) => void;
}) {
  const empty = state.picks_made === 0;
  const [open, setOpen] = useState(empty);
  const [slot, setSlot] = useState(String(state.my_slot));
  const [names, setNames] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      state.teams
        // Only the names that were actually SET: a seat showing its computed default has no
        // stored name, and echoing "Team 4" back would store the string it stands in for.
        .filter((team) => team.name !== `Team ${team.team_slot}`)
        .map((team) => [String(team.team_slot), team.name]),
    ),
  );

  const seat = Number(slot);
  const seatValid =
    !empty || (Number.isInteger(seat) && seat >= 1 && seat <= state.team_count);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!seatValid) return;
    const body: DraftConfigBody = { team_names: blankedOut(names, state.team_count) };
    // Only when it can move, and only when it moved: an unchanged seat on a started draft
    // is accepted by the backend, but sending it would still be this page asserting
    // something it was not asked to.
    if (empty && seat !== state.my_slot) body.my_slot = seat;
    onSave(body);
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`${QUIET_BUTTON} self-start`}
        data-seats="closed"
      >
        Rename teams
      </button>
    );
  }

  return (
    <form
      onSubmit={submit}
      aria-label="Seats and names"
      data-seats="open"
      className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-wrap items-end gap-6">
        {empty ? (
          <div className="flex flex-col gap-1">
            <label
              htmlFor="draft-seat-change"
              className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
            >
              My seat
            </label>
            <input
              id="draft-seat-change"
              inputMode="numeric"
              value={slot}
              onChange={(event) => setSlot(event.target.value)}
              aria-invalid={!seatValid}
              className="w-24 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 font-mono text-sm text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
            />
            <p className="text-xs text-zinc-500">
              1–{state.team_count}. Free to change — no pick has been made yet.
            </p>
          </div>
        ) : (
          <p className="text-xs text-zinc-500">
            The draft is {state.picks_made} pick{state.picks_made === 1 ? "" : "s"} in, so the
            seat is part of what those picks mean — changing it is Reconfigure, which starts
            the draft over. Names can still be changed here; nothing reads them.
          </p>
        )}
      </div>

      <TeamNameFields
        teamCount={state.team_count}
        names={names}
        onChange={(seatNumber, value) =>
          setNames((current) => ({ ...current, [String(seatNumber)]: value }))
        }
        legend="Team names"
        hint="Blank is “Team N”. Clearing one puts it back to that."
        isDisabled={isBusy}
      />

      <div className="flex items-center gap-3">
        <button type="submit" disabled={isBusy || !seatValid} className={PRIMARY_BUTTON}>
          {isBusy ? "Saving…" : "Save seats"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs font-medium text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-200"
        >
          Close
        </button>
        {seatValid ? null : (
          <p className="text-xs text-rose-600 dark:text-rose-400">
            A seat has to be a whole number in 1–{state.team_count}.
          </p>
        )}
      </div>
    </form>
  );
}

/**
 * Every seat's name as the merge should see it: the typed ones, and an empty string for the
 * ones that were cleared.
 *
 * `PUT /draft/config` merges, so a name simply left out of the body stays as it was — which
 * means "I deleted this name" has to be sent as `""` rather than as nothing. Seats never
 * touched in this form are not in `names` at all and so are left alone, which is the
 * behaviour a merge is for.
 */
function blankedOut(
  names: Record<string, string>,
  teamCount: number,
): Record<string, string> {
  const typed = nonBlank(names);
  const body: Record<string, string> = { ...typed };
  for (const [seat, value] of Object.entries(names)) {
    const slot = Number(seat);
    if (!Number.isInteger(slot) || slot < 1 || slot > teamCount) continue;
    if (value.trim() === "") body[seat] = "";
  }
  return body;
}
