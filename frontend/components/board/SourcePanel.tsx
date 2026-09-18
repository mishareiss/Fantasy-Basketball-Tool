"use client";

import type { SourceInfo } from "@/lib/api";
import { sourceHint, sourceKindLabel } from "@/lib/consensus";

/**
 * Which opinions are on the board.
 *
 * Checkboxes rather than segmented buttons, because unlike every other control here this one
 * is not a choice between alternatives — it is a set, and "ESPN's projection AND the imported
 * dynasty board" is the normal case rather than an edge one.
 *
 * Each chip carries its coverage (`ranks 449 players`), which is not decoration: a consensus
 * over a 449-name list and a 1,095-name market is a different claim about a player on both
 * than it is about a player on one, and the count is where that starts being visible.
 */

const CHIP_BASE =
  "flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-left transition-colors focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-sky-500";
const CHIP_ON =
  "border-sky-500 bg-sky-50 dark:border-sky-400 dark:bg-sky-950/40";
const CHIP_OFF =
  "border-zinc-300 bg-white hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950 dark:hover:bg-zinc-900";

export function SourcePanel({
  sources,
  selected,
  onChange,
  poolSize,
  disabled = false,
}: {
  sources: SourceInfo[];
  selected: string[];
  onChange: (next: string[]) => void;
  poolSize: number;
  disabled?: boolean;
}) {
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((each) => each !== id) : [...selected, id]);

  return (
    <fieldset
      className={`flex flex-col gap-2 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800 ${
        disabled ? "pointer-events-none opacity-50" : ""
      }`}
    >
      <legend className="px-1 text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
        Sources
      </legend>

      <div className="flex flex-wrap gap-2">
        {sources.map((source) => {
          const on = selected.includes(source.id);
          return (
            <label
              key={source.id}
              title={sourceHint(source)}
              className={`${CHIP_BASE} ${on ? CHIP_ON : CHIP_OFF}`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => toggle(source.id)}
                className="mt-0.5 accent-sky-600"
              />
              <span className="flex flex-col gap-0.5">
                <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                  {source.label}
                </span>
                <span className="font-mono text-[10px] text-zinc-500">
                  {sourceKindLabel(source.kind)} · {source.player_count}
                </span>
              </span>
            </label>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3 px-1">
        <button
          type="button"
          onClick={() => onChange(sources.map((source) => source.id))}
          className="cursor-pointer text-[11px] text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
        >
          Select all
        </button>
        <button
          type="button"
          onClick={() => onChange([])}
          className="cursor-pointer text-[11px] text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
        >
          Clear
        </button>
        <span className="text-[11px] text-zinc-500">
          Percentiles are positions in a shared pool of {poolSize} players, so a short list and
          a deep one read off the same scale.
        </span>
      </div>
    </fieldset>
  );
}
