"use client";

import { useId, useMemo, useState } from "react";

import { Segment, Segmented } from "@/components/board/BoardControls";
import {
  POSITIONS,
  type DraftStateResponse,
  type MasterPlayerRow,
  type Position,
} from "@/lib/api";
import { availabilityOf, filterAvailable, playersById, teamRoster } from "@/lib/draft";
import { AvailableRow } from "./AvailableRow";
import { FIELD } from "./DraftStates";

/**
 * The standing panel, and the two questions it answers: who is left, and who has what.
 *
 * AVAILABLE is the one that never goes away — MY board minus whoever is gone, all the way
 * down, with a percentage beside every name and a button that enters the pick on the clock.
 * The draft room used to stack the plan's panels down the page, which meant the answer to
 * "who is still there?" was somewhere below whatever you were looking at and was recomputed
 * per pick as a shortlist. This is the standing version of the same question.
 *
 * TEAMS is the other thing you look up mid-draft, and it is a different shape rather than a
 * filtered list, which is why it is a TAB and not another chip: a lineup card. The Roster view
 * in the main panel answers "what has that seat taken, and when does it pick again" — pick
 * numbers, every seat at once. This answers "what does that seat's STARTING FIVE look like",
 * one seat at a time, and the empty slots are the point of it. No pick numbers, no
 * percentages, no buttons: it is a thing to read, and every verb lives in Available.
 *
 * THE FILTERS ARE AVAILABLE'S OWN and the dropdown is Teams' own, so the controls swap with
 * the view. Sharing them would mean a position chip that narrowed a lineup card, which is not
 * a question anybody has.
 *
 * AVAILABLE'S THREE FILTERS ARE ALL SUBTRACTIVE and none of them reorders. Search is the one
 * that reaches the 400th man (`matches`, shared with /my-board, so "jokic" finds Jokić);
 * "Targets only" is the tagged shortlist I wanted anyway; the position chips are MULTI-SELECT,
 * because the reason to tick PG and SG is that either would do. The arithmetic is all in
 * `lib/draft.ts:filterAvailable` — this component is the controls and the list.
 *
 * WINDOWED at `WINDOW` rows, with a count of what is below it. A thousand rows of buttons
 * costs a layout pass on every pick, and the answer to "who is left" is never row 900 —
 * search is how you get there, the same bargain /my-board's `PAGE` makes.
 */

/** How many rows the sidebar draws before "show more". Deep enough to cover a round's worth
    of thinking, short enough that a pick re-renders in nothing. */
export const WINDOW = 60;

const TABS = ["available", "teams"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABEL: Record<Tab, string> = { available: "Available", teams: "Teams" };
const TAB_HINT: Record<Tab, string> = {
  available: "Who is still on your board, in your order — and the button that takes them.",
  teams: "One seat's lineup card: which slots are filled, and which are still open.",
};

export function DraftSidebar({
  state,
  catalog,
  available,
  availability,
  targetPick,
  picksAway,
  clock,
  canDraft,
  isBusy,
  onDraft,
}: {
  /** The live draft: the seats, their rosters, and the shape of a roster. */
  state: DraftStateResponse;
  /** The whole master board, for turning a seat's `player_ids` into names and positions. */
  catalog: MasterPlayerRow[];
  /** The still-available players, in my board order (`availableBoard`). */
  available: MasterPlayerRow[];
  availability: Record<string, number>;
  /** The pick the percentages are ABOUT — always a future one. See `AvailableRow`. */
  targetPick: number | null;
  picksAway: number | null;
  /** Whose pick is on the clock, in words: "Your pick — 7" or "Pick 5 · Team 3". */
  clock: string | null;
  canDraft: boolean;
  isBusy: boolean;
  onDraft: (playerId: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("available");
  const [term, setTerm] = useState("");
  const [targetsOnly, setTargetsOnly] = useState(false);
  const [positions, setPositions] = useState<Position[]>([]);
  const [shown, setShown] = useState(WINDOW);
  const searchId = useId();

  const rows = useMemo(
    () => filterAvailable(available, { term, targetsOnly, positions }),
    [available, term, targetsOnly, positions],
  );
  const shownRows = rows.slice(0, shown);

  function togglePosition(spot: Position) {
    setShown(WINDOW);
    setPositions((current) =>
      current.includes(spot) ? current.filter((held) => held !== spot) : [...current, spot],
    );
  }

  return (
    <aside
      data-sidebar={tab}
      aria-label={tab === "available" ? "Available players" : "Team rosters"}
      className="flex w-full flex-col gap-2 rounded-lg border border-zinc-200 bg-white p-2 lg:sticky lg:top-4 lg:w-[19rem] lg:shrink-0 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <Segmented label="Show">
        {TABS.map((option) => (
          <Segment
            key={option}
            active={tab === option}
            onClick={() => setTab(option)}
            title={TAB_HINT[option]}
          >
            {TAB_LABEL[option]}
          </Segment>
        ))}
      </Segmented>

      {tab === "teams" ? (
        <TeamsPanel state={state} catalog={catalog} />
      ) : (
        <>
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold tracking-tight">Available</h2>
            <p className="font-mono text-[10px] text-zinc-500 tabular-nums">
              {rows.length} of {available.length}
            </p>
          </div>

          {/* Whose pick the Draft buttons enter, because it is no longer always mine: with the
              board's search box gone this panel is the only entry surface, and a button that
              silently drafted for somebody else would be the worst kind of working page. */}
          <p data-sidebar-clock className="text-[11px] text-zinc-500">
            {clock === null ? "The draft is over — nothing left to enter." : clock}
            {targetPick === null ? null : (
              <>
                {" · "}
                <span data-availability-target={targetPick} className="whitespace-nowrap">
                  % = lasts to {targetPick}
                </span>
              </>
            )}
          </p>

          <label htmlFor={searchId} className="sr-only">
            Search available players
          </label>
          <input
            id={searchId}
            value={term}
            placeholder="search name or team"
            autoComplete="off"
            onChange={(event) => {
              setTerm(event.target.value);
              setShown(WINDOW);
            }}
            className={`${FIELD} w-full text-[13px]`}
          />

          <div className="flex flex-wrap items-center gap-1">
            <Chip
              active={targetsOnly}
              onClick={() => {
                setTargetsOnly((on) => !on);
                setShown(WINDOW);
              }}
              title="Only the players you tagged 'target' on your board."
              data-filter="targets"
            >
              Targets only
            </Chip>
            <span aria-hidden className="mx-0.5 h-4 w-px bg-zinc-200 dark:bg-zinc-800" />
            <Chip
              active={positions.length === 0}
              onClick={() => {
                setPositions([]);
                setShown(WINDOW);
              }}
              title="Every position."
              data-filter="position-all"
            >
              All
            </Chip>
            {POSITIONS.map((spot) => (
              <Chip
                key={spot}
                active={positions.includes(spot)}
                onClick={() => togglePosition(spot)}
                title={`Players eligible at ${spot}. Tick several — a player at any of them shows.`}
                data-filter={`position-${spot}`}
              >
                {spot}
              </Chip>
            ))}
          </div>

          <div className="max-h-[70vh] min-h-0 flex-1 overflow-y-auto">
            {rows.length === 0 ? (
              <p className="px-1 py-3 text-xs text-zinc-500">
                Nobody left on your board matches that.
              </p>
            ) : (
              <ul className="flex flex-col divide-y divide-zinc-100 dark:divide-zinc-900">
                {shownRows.map((player) => (
                  <li key={player.espn_player_id}>
                    <AvailableRow
                      player={player}
                      availability={availabilityOf(availability, player.espn_player_id)}
                      targetPick={targetPick}
                      picksAway={picksAway}
                      clock={clock}
                      canDraft={canDraft}
                      isBusy={isBusy}
                      onDraft={onDraft}
                    />
                  </li>
                ))}
              </ul>
            )}
            {rows.length > shownRows.length ? (
              <button
                type="button"
                onClick={() => setShown((count) => count + WINDOW)}
                className="w-full px-1 py-2 text-[11px] font-medium text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-200"
              >
                Show {Math.min(WINDOW, rows.length - shownRows.length)} more of{" "}
                {rows.length - shownRows.length}
              </button>
            ) : null}
          </div>
        </>
      )}
    </aside>
  );
}

/**
 * One seat's lineup card, whichever seat the dropdown is on.
 *
 * MINE BY DEFAULT, because nine times in ten the question is about my own roster — and the
 * dropdown is a dropdown rather than twelve tabs for the reason the board is one grid: twelve
 * of anything does not fit in a 19rem column.
 *
 * THE SLOTS COME FROM `lib/draft.ts:teamRoster`, which mirrors the backend's own greedy
 * assignment. The empty rows are the whole point: a seat with its centre slot open and four
 * bench men is a seat about to take a centre, and a card that listed only the names taken
 * could not say that.
 */
function TeamsPanel({
  state,
  catalog,
}: {
  state: DraftStateResponse;
  catalog: MasterPlayerRow[];
}) {
  const [slot, setSlot] = useState(state.my_slot);
  const selectId = useId();
  const byId = useMemo(() => playersById(catalog), [catalog]);

  // A seat that has gone (the draft was replaced with a smaller league while this was open)
  // falls back to mine rather than drawing an empty card for a seat that isn't there.
  const seat =
    state.teams.find((team) => team.team_slot === slot) ??
    state.teams.find((team) => team.team_slot === state.my_slot) ??
    state.teams[0];
  const rows = useMemo(
    () => (seat ? teamRoster(seat.player_ids, byId, state.roster_slots) : []),
    [seat, byId, state.roster_slots],
  );

  if (!seat) return <p className="px-1 py-3 text-xs text-zinc-500">No seats in this draft.</p>;

  const filled = rows.filter((row) => row.occupant !== null).length;

  return (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-tight">Teams</h2>
        <p className="font-mono text-[10px] text-zinc-500 tabular-nums">
          {filled} of {rows.length} slots
        </p>
      </div>

      <label htmlFor={selectId} className="sr-only">
        Team
      </label>
      <select
        id={selectId}
        data-team-select
        value={seat.team_slot}
        onChange={(event) => setSlot(Number(event.target.value))}
        className={`${FIELD} w-full text-[13px]`}
      >
        {state.teams.map((team) => (
          <option key={team.team_slot} value={team.team_slot}>
            {team.name}
            {team.is_me ? " (You)" : ""}
          </option>
        ))}
      </select>

      <ol
        data-roster-slots={seat.team_slot}
        className="max-h-[70vh] min-h-0 flex-1 overflow-y-auto"
      >
        {rows.map((row, index) => (
          <li
            key={`${row.slot}-${index}`}
            data-roster-slot={row.slot}
            data-slot-open={row.occupant === null ? "" : undefined}
            className="flex items-baseline gap-2 border-b border-zinc-100 px-1 py-1 text-[13px] last:border-b-0 dark:border-zinc-900"
          >
            <span
              data-slot-label
              className="w-7 shrink-0 font-mono text-[10px] font-semibold tracking-wide text-zinc-500 uppercase"
            >
              {row.slot}
            </span>
            {row.occupant === null ? (
              // The slot he hasn't drafted yet: the label, and a rule where the name will go.
              // Not an em dash — this is an empty place, not a value we are missing.
              <span className="min-w-0 flex-1 border-b border-dashed border-zinc-300 text-transparent dark:border-zinc-700">
                ____
              </span>
            ) : (
              <span
                data-slot-player={row.occupant.playerId}
                className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-200"
              >
                {row.occupant.name}
              </span>
            )}
          </li>
        ))}
      </ol>
    </>
  );
}

/**
 * A filter chip. Deliberately the same shape as the board pages' position chips — pressed
 * state on `aria-pressed`, not on colour alone.
 */
function Chip({
  active,
  onClick,
  title,
  children,
  ...rest
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
} & React.ComponentProps<"button">) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      title={title}
      className={`rounded border px-1.5 py-0.5 text-[11px] font-medium transition-colors ${
        active
          ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
          : "border-zinc-300 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
      }`}
      {...rest}
    >
      {children}
    </button>
  );
}
