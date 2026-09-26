"use client";

import { useState } from "react";

import { Segment, Segmented } from "@/components/board/BoardControls";
import { DRAFT_MODES, type DraftCreateBody, type DraftMode } from "@/lib/api";
import { FIELD, PRIMARY_BUTTON } from "./DraftStates";

/**
 * Starting the draft — the page's opening state, reached whenever `GET /draft` is a 404.
 *
 * Two decisions and no more: where I am sitting, and whether the room drafts itself. Both
 * have defaults, so the honest shape of this form is "press the button": `POST /draft` takes
 * an empty body and fills the rest in from `DRAFT_*` and the consensus — the league IS ten
 * teams and twenty rounds, and a mock of a differently-sized league is a setting rather than
 * a form field. Everything else the endpoint accepts (the field's horizon, which sources the
 * room is assumed to draft off, the roster shape) lives behind "advanced", where it says so
 * and changes nothing.
 *
 * `teamCount` is passed in only to bound the seat input, and comes from `DRAFT_TEAM_COUNT`
 * by way of whatever draft last existed — before the first one there is nothing to ask, so
 * the field takes any positive seat and the backend 422s a nonsense one.
 */

export const MODE_LABEL: Record<DraftMode, string> = {
  simulation: "Simulation",
  manual: "Manual",
};

const MODE_HINT: Record<DraftMode, string> = {
  simulation: "The other seats draft themselves — advance the room to your pick.",
  manual: "Every pick is typed in, yours and everybody else’s.",
};

export function DraftSetup({
  teamCount,
  defaultSlot,
  isBusy,
  onStart,
}: {
  /** Only to bound the seat input; null before any draft has told us the league's size. */
  teamCount: number | null;
  defaultSlot: number | null;
  isBusy: boolean;
  onStart: (body: DraftCreateBody) => void;
}) {
  const [slot, setSlot] = useState<string>(defaultSlot === null ? "" : String(defaultSlot));
  const [mode, setMode] = useState<DraftMode>("simulation");
  const [advanced, setAdvanced] = useState(false);

  const seat = slot.trim() === "" ? null : Number(slot);
  const seatValid =
    seat === null ||
    (Number.isInteger(seat) && seat >= 1 && (teamCount === null || seat <= teamCount));

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!seatValid) return;
    // Left-out keys mean "use the default", so an untouched seat is genuinely absent rather
    // than a guess this form made on the backend's behalf.
    const body: DraftCreateBody = { mode };
    if (seat !== null) body.my_slot = seat;
    onStart(body);
  }

  return (
    <form
      onSubmit={submit}
      aria-label="Start a draft"
      className="flex flex-col gap-5 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          No draft is running
        </h2>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Start one and this page becomes the room: the snake board, a box to enter picks
          into, and — in simulation — the controls that let the other seats draft themselves.
          There is one draft at a time, and you can reset it as often as you like.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-6">
        <div className="flex flex-col gap-1">
          <label
            htmlFor="draft-slot"
            className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
          >
            My seat
          </label>
          <input
            id="draft-slot"
            inputMode="numeric"
            value={slot}
            placeholder={defaultSlot === null ? "default" : String(defaultSlot)}
            onChange={(event) => setSlot(event.target.value)}
            aria-invalid={!seatValid}
            className={`${FIELD} w-28 font-mono`}
          />
          <p className="text-xs text-zinc-500">
            {teamCount === null
              ? "1-based, counted the way round 1 runs."
              : `1–${teamCount}, counted the way round 1 runs.`}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <Segmented label="Mode">
            {DRAFT_MODES.map((option) => (
              <Segment
                key={option}
                active={mode === option}
                onClick={() => setMode(option)}
                title={MODE_HINT[option]}
              >
                {MODE_LABEL[option]}
              </Segment>
            ))}
          </Segmented>
          <p className="text-xs text-zinc-500">{MODE_HINT[mode]}</p>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => setAdvanced((open) => !open)}
          aria-expanded={advanced}
          className="self-start text-xs font-medium text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-200"
        >
          {advanced ? "Hide" : "Show"} advanced
        </button>
        {advanced ? (
          <div className="rounded-md bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
            <p>
              Nothing to set. The league&rsquo;s shape (ten teams, twenty rounds) comes from
              the environment, the roster is our startup one, and the room is assumed to draft
              off the same dynasty consensus your own board is read against — every available
              source, equally weighted. Those are snapshotted onto the draft when it starts,
              so it replays identically however the sources move afterwards.
            </p>
          </div>
        ) : null}
      </div>

      <div className="flex items-center gap-3">
        <button type="submit" disabled={isBusy || !seatValid} className={PRIMARY_BUTTON}>
          {isBusy ? "Starting…" : "Start draft"}
        </button>
        {!seatValid ? (
          <p className="text-xs text-rose-600 dark:text-rose-400">
            A seat has to be a whole number in 1–{teamCount}.
          </p>
        ) : null}
      </div>
    </form>
  );
}

