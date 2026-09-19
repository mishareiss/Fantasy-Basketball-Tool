"use client";

import { memo } from "react";

/**
 * The line between two tiers, and the gap where a new one can be started.
 *
 * A tier is not a property of a player here — it is a BAND over the ranks, stored as the rank
 * it starts at. So the thing on screen is a line between two rows rather than a column on
 * them, and everything you can do to it is a change to where that line sits: nudge it a row,
 * drag it to another gap, add one, merge one away. None of it can move a player, which is the
 * guarantee that makes the dividers safe to fiddle with mid-draft.
 *
 * Drawn as a real `<tr>` spanning the table rather than as a floating rule, because a divider
 * that isn't part of the table drifts out of alignment the moment a column changes width —
 * and because interleaving it into the same `<tbody>` is what keeps it in the right place
 * inside the window, the search results, and the row order all at once.
 *
 * Both rows below carry `data-tier-*` attributes rather than relying on their text: the page's
 * tests read the structure, and a divider is the one row here with no player to identify it
 * by.
 */

const CONTROL =
  "rounded px-1.5 py-0.5 text-xs font-medium text-zinc-500 transition-colors " +
  "hover:bg-zinc-200 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-500 " +
  "dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100";

export type DividerHandlers = {
  /** Nudge this divider one rank up or down within the active scope. */
  onNudge: (rank: number, step: -1 | 1) => void;
  /** Merge this tier into the one above it. */
  onRemove: (rank: number) => void;
  onDragStart: (rank: number) => void;
  onDragOver: (rank: number) => void;
  onDrop: (rank: number) => void;
  onDragEnd: () => void;
};

function TierDividerInner({
  tier,
  start,
  end,
  scopeNoun,
  canMoveUp,
  canMoveDown,
  removable,
  editable,
  dragging,
  dropTarget,
  handlers,
}: {
  tier: number;
  /** The rank this band starts at, within the ACTIVE scope. */
  start: number;
  end: number;
  /** What the ranks count — "on the board", "among point guards". */
  scopeNoun: string;
  canMoveUp: boolean;
  canMoveDown: boolean;
  /** False for tier 1: the top of the board is where tier 1 starts, and it cannot be merged. */
  removable: boolean;
  /** False while a write is in flight. The controls are DISABLED rather than unmounted, so a
      save doesn't make the line you are working on jump around under the cursor. */
  editable: boolean;
  dragging: boolean;
  dropTarget: boolean;
  handlers: DividerHandlers;
}) {
  const size = end - start + 1;
  const where = `${start}–${end} ${scopeNoun}`;

  return (
    <tr
      data-tier-divider={tier}
      data-tier-start={start}
      onDragOver={(event) => {
        // Without preventDefault the browser refuses the drop outright.
        event.preventDefault();
        handlers.onDragOver(start);
      }}
      onDrop={(event) => {
        event.preventDefault();
        handlers.onDrop(start);
      }}
      className={`border-y border-zinc-200 bg-zinc-100/80 dark:border-zinc-800 dark:bg-zinc-900/70 ${
        dragging ? "opacity-40" : ""
      } ${dropTarget ? "outline-2 -outline-offset-2 outline-sky-500" : ""}`}
    >
      <td colSpan={8} className="px-2 py-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {removable ? (
            <button
              type="button"
              draggable={editable}
              disabled={!editable}
              aria-label={`Drag the start of tier ${tier}`}
              onDragStart={(event) => {
                // The rank rides in component state, not in the transfer — jsdom has no
                // dataTransfer. The type is set anyway so a real browser can tell a divider
                // drag from a player drag before it is dropped.
                event.dataTransfer?.setData("application/x-tier-cut", String(start));
                event.dataTransfer?.setData("text/plain", `tier ${tier}`);
                handlers.onDragStart(start);
              }}
              onDragEnd={handlers.onDragEnd}
              title="Drag this line to another gap, or use ▲ ▼ to move it one row."
              className={`px-1 text-zinc-400 select-none dark:text-zinc-600 ${
                editable
                  ? "cursor-grab hover:text-zinc-600 active:cursor-grabbing dark:hover:text-zinc-300"
                  : "cursor-not-allowed opacity-40"
              }`}
            >
              ⠿
            </button>
          ) : (
            <span aria-hidden className="px-1 text-zinc-300 dark:text-zinc-700">
              ⎯
            </span>
          )}

          <span className="text-[11px] font-semibold tracking-wide text-zinc-700 uppercase dark:text-zinc-200">
            Tier {tier}
          </span>
          <span className="font-mono text-[11px] text-zinc-500">
            {where} · {size} player{size === 1 ? "" : "s"}
          </span>

          <span className="ml-auto flex items-center gap-1">
            <button
              type="button"
              disabled={!editable || !canMoveUp}
              onClick={() => handlers.onNudge(start, -1)}
              aria-label={`Move the start of tier ${tier} up one`}
              title="Start this tier one rank higher — the row above joins it."
              className={CONTROL}
            >
              ▲
            </button>
            <button
              type="button"
              disabled={!editable || !canMoveDown}
              onClick={() => handlers.onNudge(start, 1)}
              aria-label={`Move the start of tier ${tier} down one`}
              title="Start this tier one rank lower — its top row joins the tier above."
              className={CONTROL}
            >
              ▼
            </button>
            {removable ? (
              <button
                type="button"
                disabled={!editable}
                onClick={() => handlers.onRemove(start)}
                aria-label={`Remove the break before tier ${tier}`}
                title="Merge this tier into the one above it."
                className={CONTROL}
              >
                ✕
              </button>
            ) : null}
          </span>
        </div>
      </td>
    </tr>
  );
}

export const TierDivider = memo(TierDividerInner);

/**
 * The gap between two rows, and the offer to cut a tier there.
 *
 * Present before every row that hasn't already got a divider, and invisible until you are on
 * it — a board with 175 permanent "+" buttons down its left edge would be unreadable, and the
 * only time this control is wanted is when the eye is already on the gap it belongs to. It
 * stays reachable from the keyboard (`focus-within`), so "invisible until hovered" is a
 * presentation choice rather than a way of hiding it from half the people using the page.
 *
 * It keeps a real height (a few pixels) rather than collapsing to nothing, and that is the
 * load-bearing part: a zero-height row cannot be hovered, so an affordance that only appears
 * on hover of a zero-height row is an affordance nobody can reach with a mouse. The chip it
 * reveals is taller than the band and overflows it, which a table cell does not clip.
 */
function TierBreakSlotInner({
  rank,
  scopeNoun,
  onAdd,
  disabled,
}: {
  /** The rank a break here would start a new tier at, within the active scope. */
  rank: number;
  scopeNoun: string;
  onAdd: (rank: number) => void;
  disabled: boolean;
}) {
  return (
    <tr data-tier-slot={rank} className="group/slot h-1.5">
      <td colSpan={8} className="p-0">
        <div className="flex h-1.5 items-center gap-2 opacity-0 transition-opacity group-hover/slot:opacity-100 focus-within:opacity-100">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onAdd(rank)}
            aria-label={`Start a new tier at ${rank} ${scopeNoun}`}
            title={`Break the tier here — a new one starts at ${rank} ${scopeNoun}.`}
            className="ml-8 rounded bg-white px-1.5 text-[10px] leading-3 font-medium tracking-wide text-sky-700 uppercase hover:bg-sky-100 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-500 dark:bg-zinc-950 dark:text-sky-300 dark:hover:bg-sky-500/15"
          >
            + tier break here
          </button>
          <span
            aria-hidden
            className="h-px flex-1 border-t border-dashed border-sky-400/60 dark:border-sky-400/40"
          />
        </div>
      </td>
    </tr>
  );
}

export const TierBreakSlot = memo(TierBreakSlotInner);
