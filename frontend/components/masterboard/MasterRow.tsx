"use client";

import { memo, useState } from "react";

import type { MasterPlayerRow } from "@/lib/api";
import { MISSING, positions as positionList, whole } from "@/lib/format";
import {
  EDGE_CLASS,
  EDGE_GLYPH,
  edge,
  edgeDescription,
  edgeTone,
  tagStyle,
  type EdgeTone,
} from "@/lib/masterboard";

/**
 * One line of our board: where WE have him, who he is, where the FIELD has him, and the four
 * things you can do to him.
 *
 * The column order is the argument the page makes. Our rank is first and biggest because it
 * is the only number here that is a decision; the consensus is a REFERENCE, set back in the
 * middle of the row in the muted weight every other read-only column on the site uses. The
 * gap between them is the one thing worth colouring, because it is the only column that says
 * anything you didn't already know.
 *
 * Every index this component reports is an index into the FULL order, never into the window
 * or the search results — see `moveTo` in lib/masterboard.ts.
 *
 * The row also accepts a DROP while a tier divider is being dragged, which is why the `<tr>`
 * keeps its drag handlers even when the order is frozen under a position filter: the page
 * decides from its own drag state whether a drop over this row moves a player or a line.
 */

export type RowHandlers = {
  /** Both indices are into the full order. */
  onMove: (from: number, to: number) => void;
  onTag: (row: MasterPlayerRow) => void;
  onNote: (row: MasterPlayerRow, note: string) => void;
  onExclude: (row: MasterPlayerRow) => void;
  onDragStart: (index: number) => void;
  onDragOver: (index: number) => void;
  onDrop: (index: number) => void;
  onDragEnd: () => void;
};

const CONTROL =
  "rounded-md px-1.5 py-0.5 text-xs font-medium text-zinc-600 transition-colors " +
  "hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-30 " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-500 " +
  "dark:text-zinc-400 dark:hover:bg-zinc-800";

const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2 py-0.5 text-xs text-zinc-800 " +
  "placeholder:text-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

const BADGE = "rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase";

/** How far above the field we have him, or an em dash when nobody ranks him. */
function EdgeChip({ row }: { row: MasterPlayerRow }) {
  const value = edge(row);
  const description = edgeDescription(row);

  if (value === null) {
    return (
      <span className="text-zinc-400 dark:text-zinc-600" title={description}>
        {MISSING}
      </span>
    );
  }

  const tone: EdgeTone = edgeTone(value);
  return (
    <span
      data-edge={tone}
      title={description}
      className={`inline-block min-w-14 rounded px-1.5 py-0.5 font-mono text-xs tabular-nums ${EDGE_CLASS[tone]}`}
    >
      <span aria-hidden>{EDGE_GLYPH[tone]}</span>{" "}
      {value > 0 ? `+${value}` : value === 0 ? "0" : String(value)}
      <span className="sr-only"> — {description}</span>
    </span>
  );
}

/**
 * "Move to #": the control the whole feature depends on at depth.
 *
 * A drag gets you across a screen and the nudges get you across three rows; neither can take
 * a man from 240 to 30, and that is the move a draft board is actually made of. The input is
 * local (a draft, not board state) so typing into it never re-renders the order underneath.
 */
function MoveTo({
  row,
  index,
  total,
  onMove,
  disabled,
}: {
  row: MasterPlayerRow;
  index: number;
  total: number;
  onMove: (from: number, to: number) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState("");

  return (
    <form
      className="flex items-center gap-1"
      onSubmit={(event) => {
        event.preventDefault();
        const target = Number(draft);
        if (!draft.trim() || !Number.isFinite(target)) return;
        // 1-based on screen, 0-based in the array. `moveTo` clamps, so 9999 means "last".
        onMove(index, Math.round(target) - 1);
        setDraft("");
      }}
    >
      <input
        aria-label={`Move ${row.name} to rank`}
        value={draft}
        inputMode="numeric"
        placeholder="#"
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        className={`${FIELD} w-12 text-right font-mono tabular-nums`}
      />
      <button
        type="submit"
        disabled={disabled || draft.trim() === ""}
        title={`Move ${row.name} to a rank between 1 and ${total}`}
        className={CONTROL}
      >
        Go
      </button>
    </form>
  );
}

/**
 * The note, saved on blur.
 *
 * Uncontrolled and remounted by the parent's key when the stored note changes — the same
 * trick the market page's line rows use. A draft is a draft: the server's copy replaces it
 * outright rather than racing an effect for it.
 */
function Note({
  row,
  onNote,
  disabled,
}: {
  row: MasterPlayerRow;
  onNote: (row: MasterPlayerRow, note: string) => void;
  disabled: boolean;
}) {
  const stored = row.note ?? "";
  return (
    <input
      aria-label={`Note on ${row.name}`}
      defaultValue={stored}
      placeholder="note…"
      disabled={disabled}
      onBlur={(event) => {
        const next = event.target.value;
        if (next !== stored) onNote(row, next);
      }}
      className={`${FIELD} w-full min-w-28`}
    />
  );
}

function MasterRowInner({
  row,
  index,
  total,
  tier,
  positionTier,
  dropTarget,
  dragging,
  busy,
  reorderable,
  handlers,
}: {
  row: MasterPlayerRow;
  /** His place in the FULL order, 0-based. */
  index: number;
  total: number;
  /** His band in the scope the page is showing — overall, or his position's. Null when the
      board has no tier for him there. */
  tier: number | null;
  /**
   * His tier among his own position, on the WHOLE-board view only. The fact a draft is
   * actually decided by: the centre you are about to reach for is the last of his tier. Null
   * under a position filter, where `tier` is already that number and printing it twice would
   * be noise.
   */
  positionTier: { scope: string; tier: number } | null;
  dropTarget: boolean;
  dragging: boolean;
  busy: boolean;
  /**
   * False under a position filter. A filtered sub-order cannot express a full-board move —
   * "one above the next point guard" is not a board rank — so the ORDER controls go away
   * while the tag, the note, the exclude and the tier dividers stay. See the hint the page
   * prints beside the filter.
   */
  reorderable: boolean;
  handlers: RowHandlers;
}) {
  const tag = tagStyle(row.tag);
  const frozen = busy || !reorderable;

  return (
    <tr
      data-player={row.espn_player_id}
      data-rank={row.rank ?? ""}
      onDragOver={(event) => {
        // Without preventDefault the browser refuses the drop outright.
        event.preventDefault();
        handlers.onDragOver(index);
      }}
      onDrop={(event) => {
        event.preventDefault();
        handlers.onDrop(index);
      }}
      className={`border-b border-zinc-100 last:border-b-0 dark:border-zinc-900 ${
        dragging ? "opacity-40" : "hover:bg-zinc-50 dark:hover:bg-zinc-900/60"
      } ${dropTarget ? "outline-2 -outline-offset-2 outline-sky-500" : ""}`}
    >
      <td className="pl-2">
        <button
          type="button"
          draggable={reorderable}
          disabled={!reorderable}
          aria-label={`Drag ${row.name}`}
          onDragStart={(event) => {
            // The index rides in component state, not in the transfer: jsdom has no
            // dataTransfer, and a reorder that only works in a real browser is one nobody
            // can test. The payload is set anyway so a real drag gets a sane image.
            event.dataTransfer?.setData("text/plain", String(index));
            handlers.onDragStart(index);
          }}
          onDragEnd={handlers.onDragEnd}
          title={
            reorderable
              ? "Drag to move him. For a long move, type a rank into the # box."
              : "Reordering is off under a position filter — switch to All to move him."
          }
          className={`inline-block px-1 text-zinc-300 select-none dark:text-zinc-700 ${
            reorderable
              ? "cursor-grab hover:text-zinc-500 active:cursor-grabbing dark:hover:text-zinc-400"
              : "cursor-not-allowed opacity-40"
          }`}
        >
          ⠿
        </button>
      </td>

      <td className="py-1.5 pr-2 text-right">
        <span className="font-mono text-base font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
          {row.rank ?? MISSING}
        </span>
        {/* Which band he is in, repeated on the row because the divider that says so scrolls
            off: a rank without its tier is the number you have to count back up to read. */}
        {tier === null ? null : (
          <span
            data-tier={tier}
            title={`He is in tier ${tier} of the list on screen. A tier is a band over the ranks — move him across the line and he is in the next one.`}
            className="ml-1.5 inline-block rounded bg-zinc-100 px-1 font-mono text-[10px] text-zinc-500 tabular-nums dark:bg-zinc-800 dark:text-zinc-400"
          >
            T{tier}
          </span>
        )}
        {positionTier === null ? null : (
          <span
            data-position-tier={positionTier.tier}
            title={`Tier ${positionTier.tier} among ${positionTier.scope}s — his band in his own position's order, which is the one a roster slot is filled out of.`}
            className="ml-1 inline-block rounded px-1 font-mono text-[10px] text-zinc-400 tabular-nums dark:text-zinc-600"
          >
            {positionTier.scope}
            {positionTier.tier}
          </span>
        )}
      </td>

      <td className="py-1.5 pr-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-medium text-zinc-900 dark:text-zinc-100">{row.name}</span>
          <span className="text-xs text-zinc-500">
            {positionList(row.positions)} · {whole(row.age)} · {row.nba_team ?? MISSING}
          </span>
          {row.is_new ? (
            <span
              className={`${BADGE} bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300`}
              title="Placed on your board by this reconcile — a rookie or an arrival nobody had an entry for. He is at the slot the consensus implies; move him."
            >
              New
            </span>
          ) : null}
          {row.is_stale ? (
            <span
              className={`${BADGE} bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300`}
              title="No source ranks him any more. His place is still yours — it was a decision — but nothing backs the reference column."
            >
              Stale
            </span>
          ) : null}
        </div>
      </td>

      <td className="hidden py-1.5 pr-3 text-right font-mono text-xs tabular-nums text-zinc-500 sm:table-cell">
        {row.consensus_rank ?? MISSING}
      </td>

      <td className="py-1.5 pr-3 text-right">
        <EdgeChip row={row} />
      </td>

      <td className="py-1.5 pr-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => handlers.onTag(row)}
          aria-label={`Tag ${row.name}`}
          title="none → target → fade → none"
          className={`${BADGE} w-14 transition-colors disabled:opacity-40 ${
            tag?.className ??
            "text-zinc-400 hover:bg-zinc-200 dark:text-zinc-600 dark:hover:bg-zinc-800"
          }`}
        >
          {tag?.label ?? "tag"}
        </button>
      </td>

      <td className="hidden py-1.5 pr-3 md:table-cell">
        <Note key={`${row.espn_player_id}:${row.note ?? ""}`} row={row} onNote={handlers.onNote} disabled={busy} />
      </td>

      <td className="py-1.5 pr-2">
        <div className="flex items-center justify-end gap-1">
          <button
            type="button"
            disabled={frozen || index === 0}
            onClick={() => handlers.onMove(index, index - 1)}
            aria-label={`Move ${row.name} up`}
            className={CONTROL}
          >
            ▲
          </button>
          <button
            type="button"
            disabled={frozen || index === total - 1}
            onClick={() => handlers.onMove(index, index + 1)}
            aria-label={`Move ${row.name} down`}
            className={CONTROL}
          >
            ▼
          </button>
          <MoveTo
            row={row}
            index={index}
            total={total}
            onMove={handlers.onMove}
            disabled={frozen}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => handlers.onExclude(row)}
            aria-label={`Set ${row.name} aside`}
            title="Take him out of the order. His tag and note survive; everyone below moves up one."
            className={CONTROL}
          >
            ✕
          </button>
        </div>
      </td>
    </tr>
  );
}

/**
 * Memoised, because a drag over a 175-row table re-renders the page on every frame and only
 * two of those rows actually change. The handlers are stable (`useCallback` in the page), so
 * the comparison is over the row object and the four flags.
 */
export const MasterRow = memo(MasterRowInner);
