"use client";

import {
  CONSENSUS_METHODS,
  HORIZONS,
  POSITIONS,
  type ConsensusMethod,
  type Horizon,
  type Position,
  type TiersMode,
} from "@/lib/api";
import { HORIZON_LABEL } from "@/lib/board";
import { METHOD_HINT, METHOD_LABEL } from "@/lib/consensus";

/**
 * The dials over the board: which VIEW it is, which horizon ranks it, which position it is
 * narrowed to, how deep it goes, and then the one dial that belongs to each view — tiers for
 * the value board, rank/percentile for the consensus one.
 *
 * Segmented buttons rather than selects for horizon and position — they are the two things
 * you change mid-draft, and a dropdown costs a click you don't have when you're on the clock.
 *
 * The horizon is shared on purpose. It governs both views, and it means different things to
 * each: on the value board it picks which number ranks the rows, on the consensus board it
 * additionally decides which imported lists are eligible at all.
 */

export const LIMITS = [25, 50, 100, 200, 500] as const;

/**
 * Which board you are looking at.
 *
 * `value` is the original single-source board — ESPN's projection through the age curve, cut
 * into tiers. `consensus` is several sources side by side. Two views rather than one merged
 * one, because they answer different questions: "what is he worth under our scoring" and
 * "who does the room like", and the second is only interesting where it disagrees with the
 * first.
 */
export const BOARD_MODES = ["value", "consensus"] as const;
export type BoardMode = (typeof BOARD_MODES)[number];

export const MODE_LABEL: Record<BoardMode, string> = {
  value: "Value",
  consensus: "Consensus",
};

export type BoardControlValues = {
  mode: BoardMode;
  horizon: Horizon;
  position: Position | null;
  limit: number;
  tiers: TiersMode;
  /** Consensus only: whether sources are averaged as places or as pool positions. */
  method: ConsensusMethod;
};

const SEGMENT_BASE =
  "px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";
const SEGMENT_ON = "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900";
const SEGMENT_OFF =
  "bg-white text-zinc-600 hover:bg-zinc-100 dark:bg-zinc-950 dark:text-zinc-400 dark:hover:bg-zinc-900";

function Segmented({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
        {label}
      </span>
      <div
        role="group"
        aria-label={label}
        className="flex overflow-hidden rounded-md border border-zinc-300 dark:border-zinc-700"
      >
        {children}
      </div>
    </div>
  );
}

function Segment({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      title={title}
      className={`${SEGMENT_BASE} ${active ? SEGMENT_ON : SEGMENT_OFF} border-r border-zinc-300 last:border-r-0 dark:border-zinc-700`}
    >
      {children}
    </button>
  );
}

export function BoardControls({
  values,
  onChange,
  disabled = false,
}: {
  values: BoardControlValues;
  onChange: (next: Partial<BoardControlValues>) => void;
  disabled?: boolean;
}) {
  const consensus = values.mode === "consensus";

  return (
    <div
      className={`flex flex-wrap items-center gap-x-6 gap-y-3 ${disabled ? "pointer-events-none opacity-50" : ""}`}
    >
      <Segmented label="Board">
        {BOARD_MODES.map((mode) => (
          <Segment key={mode} active={values.mode === mode} onClick={() => onChange({ mode })}>
            {MODE_LABEL[mode]}
          </Segment>
        ))}
      </Segmented>

      <Segmented label="Horizon">
        {HORIZONS.map((horizon) => (
          <Segment
            key={horizon}
            active={values.horizon === horizon}
            onClick={() => onChange({ horizon })}
          >
            {HORIZON_LABEL[horizon]}
          </Segment>
        ))}
      </Segmented>

      <Segmented label="Position">
        <Segment active={values.position === null} onClick={() => onChange({ position: null })}>
          All
        </Segment>
        {POSITIONS.map((position) => (
          <Segment
            key={position}
            active={values.position === position}
            onClick={() => onChange({ position })}
          >
            {position}
          </Segment>
        ))}
      </Segmented>

      <div className="flex items-center gap-2">
        <label
          htmlFor="board-limit"
          className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
        >
          Rows
        </label>
        <select
          id="board-limit"
          value={values.limit}
          onChange={(event) => onChange({ limit: Number(event.target.value) })}
          className="rounded-md border border-zinc-300 bg-white px-2 py-1 font-mono text-xs text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
        >
          {LIMITS.map((limit) => (
            <option key={limit} value={limit}>
              {limit}
            </option>
          ))}
        </select>
      </div>

      {consensus ? (
        // Drives both the consensus column and every source cell, so the board is read in one
        // unit at a time rather than in two that have to be mentally converted.
        <Segmented label="Averaged by">
          {CONSENSUS_METHODS.map((method) => (
            <Segment
              key={method}
              active={values.method === method}
              onClick={() => onChange({ method })}
              title={METHOD_HINT[method]}
            >
              {METHOD_LABEL[method]}
            </Segment>
          ))}
        </Segmented>
      ) : (
        <Segmented label="Tiers">
          <Segment active={values.tiers === "auto"} onClick={() => onChange({ tiers: "auto" })}>
            On
          </Segment>
          <Segment active={values.tiers === "off"} onClick={() => onChange({ tiers: "off" })}>
            Off
          </Segment>
        </Segmented>
      )}
    </div>
  );
}
