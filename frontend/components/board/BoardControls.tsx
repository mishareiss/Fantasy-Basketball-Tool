"use client";

import { HORIZONS, POSITIONS, type Horizon, type Position, type TiersMode } from "@/lib/api";
import { HORIZON_LABEL } from "@/lib/board";

/**
 * The four dials over the board: which horizon ranks it, which position it is narrowed to,
 * how deep it goes, and whether it is cut into tiers.
 *
 * Segmented buttons rather than selects for horizon and position — they are the two things
 * you change mid-draft, and a dropdown costs a click you don't have when you're on the clock.
 */

export const LIMITS = [25, 50, 100, 200, 500] as const;

export type BoardControlValues = {
  horizon: Horizon;
  position: Position | null;
  limit: number;
  tiers: TiersMode;
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
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
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
  return (
    <div
      className={`flex flex-wrap items-center gap-x-6 gap-y-3 ${disabled ? "pointer-events-none opacity-50" : ""}`}
    >
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

      <Segmented label="Tiers">
        <Segment active={values.tiers === "auto"} onClick={() => onChange({ tiers: "auto" })}>
          On
        </Segment>
        <Segment active={values.tiers === "off"} onClick={() => onChange({ tiers: "off" })}>
          Off
        </Segment>
      </Segmented>
    </div>
  );
}
