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
 * `teamCount` is passed in only to bound the seat input and to know HOW MANY name fields to
 * draw, and it comes from `DRAFT_TEAM_COUNT` by way of whatever draft last existed — before
 * the first one there is nothing to ask, so the seat field takes any positive number and the
 * backend 422s a nonsense one.
 *
 * THE NAMES ARE OPTIONAL AND THEY ARE ONLY DRAWN WHEN THE LEAGUE'S SIZE IS KNOWN. A name for
 * a seat that doesn't exist is a 422 that would refuse the whole draft, and before the first
 * one this page genuinely does not know whether the league has eight seats or twelve — so
 * rather than guess ten, the very first draft starts unnamed and the seats are named from the
 * room itself (`DraftSeats`, which can do it at any point because a name is cosmetic).
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
  defaultNames = {},
  isBusy,
  onStart,
}: {
  /** Bounds the seat input and decides how many name fields there are; null before any
      draft has told us the league's size. */
  teamCount: number | null;
  defaultSlot: number | null;
  /** The names the draft being reconfigured already had, so a reconfigure doesn't silently
      drop them. Keyed by seat number as a string, the way the API holds them. */
  defaultNames?: Record<string, string>;
  isBusy: boolean;
  onStart: (body: DraftCreateBody) => void;
}) {
  const [slot, setSlot] = useState<string>(defaultSlot === null ? "" : String(defaultSlot));
  const [mode, setMode] = useState<DraftMode>("simulation");
  const [names, setNames] = useState<Record<string, string>>(defaultNames);
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
    // Blank names are genuinely absent rather than sent as "": an unnamed seat is "Team N",
    // computed server-side, and storing the string the backend would have produced anyway is
    // how a default stops being a default.
    const named = nonBlank(names);
    if (Object.keys(named).length > 0) body.team_names = named;
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

      {teamCount === null ? null : (
        <TeamNameFields
          teamCount={teamCount}
          names={names}
          onChange={(seatNumber, value) =>
            setNames((current) => ({ ...current, [String(seatNumber)]: value }))
          }
          legend="Team names (optional)"
          hint="Blank is “Team N”. Cosmetic — nothing about the draft reads a name."
        />
      )}

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


/** Every name that was actually typed, trimmed — the blanks are absent, not empty. */
export function nonBlank(names: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(names)
      .map(([seat, value]) => [seat, value.trim()] as const)
      .filter(([, value]) => value !== ""),
  );
}

/**
 * One text box per seat — shared by the setup form and the in-room seats panel, so naming
 * the room before it starts and renaming it at pick 40 are the same control.
 *
 * Labelled per seat rather than placeholder-only: "Team 4" as a placeholder disappears the
 * moment you type, and then nothing on screen says which seat the box you are in belongs to.
 */
export function TeamNameFields({
  teamCount,
  names,
  onChange,
  legend,
  hint,
  isDisabled = false,
}: {
  teamCount: number;
  names: Record<string, string>;
  onChange: (seat: number, value: string) => void;
  legend: string;
  hint?: string;
  isDisabled?: boolean;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
        {legend}
      </legend>
      <div className="grid gap-x-3 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: teamCount }, (_, index) => index + 1).map((seat) => (
          <label key={seat} className="flex items-center gap-2 text-xs text-zinc-500">
            <span className="w-14 shrink-0 font-mono tabular-nums">Team {seat}</span>
            <input
              value={names[String(seat)] ?? ""}
              placeholder={`Team ${seat}`}
              disabled={isDisabled}
              autoComplete="off"
              data-team-name={seat}
              onChange={(event) => onChange(seat, event.target.value)}
              className={`${FIELD} w-full min-w-0`}
            />
          </label>
        ))}
      </div>
      {hint ? <p className="text-xs text-zinc-500">{hint}</p> : null}
    </fieldset>
  );
}
