"use client";

import { useState } from "react";

import { HORIZONS, type DraftConfigBody, type DraftStateResponse, type Horizon } from "@/lib/api";
import { TeamNameFields, nonBlank } from "./DraftSetup";
import { PRIMARY_BUTTON, QUIET_BUTTON } from "./DraftStates";
import { FieldSources } from "./FieldSources";

/**
 * The pre-draft panel: which seat is mine, whose board the room drafts off, and what the seats
 * are called.
 *
 * TWO KINDS OF FIELD WITH DIFFERENT RULES, and the split is the backend's rather than this
 * panel's (`PUT /draft/config`). A NAME is cosmetic — no pick, no need, no availability number
 * reads one — so it can be changed at pick 1 or pick 141. THE SEAT AND THE FIELD are not: a
 * pick number only means something under one shape, and every pick already made was made
 * against one field, so moving either after pick 19 would re-describe picks that have already
 * happened. Both therefore exist only while the draft is EMPTY, which is the case that
 * actually happens — "I set it to 2 and I'm really at 7", or "this room drafts off ADP, not
 * the consensus", noticed before the room starts — and costs nothing to fix, because there is
 * nothing to lose. Once a pick is in, both are only movable by Reconfigure, which says out
 * loud that it throws the picks away.
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
  const [horizon, setHorizon] = useState<Horizon>(asHorizon(state.field_horizon));
  // Null is "every source", which is exactly what the backend stores as NULL — so the draft's
  // own `field_source_ids` maps onto this state with nothing in between.
  const [sources, setSources] = useState<string[] | null>(state.field_source_ids);
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
  // Sent only when it actually moved. The backend accepts the field a draft already has at any
  // point, so this is not what avoids the 422 — it is this panel not asserting a decision it
  // was not asked to make, the same rule the seat above it follows.
  const fieldChanged =
    horizon !== state.field_horizon ||
    sortedIds(sources) !== sortedIds(state.field_source_ids);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!seatValid) return;
    const body: DraftConfigBody = { team_names: blankedOut(names, state.team_count) };
    // Only when it can move, and only when it moved: an unchanged seat on a started draft
    // is accepted by the backend, but sending it would still be this page asserting
    // something it was not asked to.
    if (empty && seat !== state.my_slot) body.my_slot = seat;
    if (empty && fieldChanged) {
      body.field_horizon = horizon;
      // An empty array is how "every source" is SENT once the draft already has a subset
      // stored: omitting the key would leave that subset in place, and the backend reads `[]`
      // as all of them, exactly as it reads an absent key on create.
      body.field_source_ids = sources ?? [];
    }
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

      {empty ? (
        <FieldSources
          horizon={horizon}
          onHorizon={setHorizon}
          selected={sources}
          onSelected={setSources}
          isDisabled={isBusy}
        />
      ) : (
        <p className="text-xs text-zinc-500" data-field-frozen>
          The room is drafting off the {state.field_horizon} consensus of{" "}
          {state.field_source_ids
            ? `${state.field_source_ids.length} chosen source${state.field_source_ids.length === 1 ? "" : "s"}`
            : "every source"}
          . That is what the picks already made were made against, so changing it is
          Reconfigure too.
        </p>
      )}

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


/**
 * The draft's stored horizon, narrowed to the two this page has controls for.
 *
 * `DraftStateResponse.field_horizon` is typed as the backend types it (a bare string) so a
 * horizon added server-side reads as itself rather than failing to compile. The toggle can only
 * express the two we know, so anything else falls back to dynasty — and the 'frozen' sentence
 * above still prints the real stored value, so nothing is hidden by the narrowing.
 */
function asHorizon(value: string): Horizon {
  return (HORIZONS as readonly string[]).includes(value) ? (value as Horizon) : "dynasty";
}

/** A comparable spelling of a source selection: sorted and joined, with null as "all". */
function sortedIds(ids: string[] | null): string {
  return ids === null ? "*" : [...ids].sort().join(",");
}
