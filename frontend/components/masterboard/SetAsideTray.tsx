"use client";

import type { MasterPlayerRow } from "@/lib/api";
import { MISSING, positions as positionList, whole } from "@/lib/format";
import { tagStyle } from "@/lib/masterboard";

/**
 * The players we've parked.
 *
 * Off the ORDER, not off the board, and the difference is the whole reason this is a tray
 * rather than a delete: their tags and notes are intact, and bringing one back is one write.
 * He does NOT come back at the rank he had — the board has moved past it — but at the slot
 * the consensus implies, flagged like a new arrival, which is the backend's rule and worth
 * saying on screen so it isn't a surprise.
 *
 * It renders as nothing at all when nobody is in it: an empty tray is not news.
 */

const BADGE = "rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase";

export function SetAsideTray({
  players,
  onRestore,
  busy,
}: {
  players: MasterPlayerRow[];
  onRestore: (row: MasterPlayerRow) => void;
  busy: boolean;
}) {
  if (players.length === 0) return null;

  return (
    <section aria-labelledby="set-aside" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h2 id="set-aside" className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Set aside
        </h2>
        <p className="text-xs text-zinc-500">
          {players.length} player{players.length === 1 ? "" : "s"} out of the order. Bringing
          one back puts him at the slot the field implies, not where he used to be.
        </p>
      </div>

      <ul className="flex flex-col gap-1 rounded-lg border border-dashed border-zinc-300 p-2 dark:border-zinc-700">
        {players.map((row) => {
          const tag = tagStyle(row.tag);
          return (
            <li
              key={row.espn_player_id}
              data-player={row.espn_player_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded px-2 py-1 text-[13px] hover:bg-zinc-50 dark:hover:bg-zinc-900/60"
            >
              <span className="font-medium text-zinc-800 dark:text-zinc-200">{row.name}</span>
              <span className="text-xs text-zinc-500">
                {positionList(row.positions)} · {whole(row.age)} · {row.nba_team ?? MISSING}
              </span>
              <span
                className="font-mono text-xs text-zinc-500"
                title="Where the field has him — the slot he would come back at"
              >
                field {row.consensus_rank ?? MISSING}
              </span>
              {tag ? <span className={`${BADGE} ${tag.className}`}>{tag.label}</span> : null}
              {row.note ? (
                <span className="truncate text-xs text-zinc-500 italic">{row.note}</span>
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() => onRestore(row)}
                aria-label={`Restore ${row.name}`}
                className="ml-auto rounded-md px-2 py-0.5 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-500 dark:text-zinc-400 dark:hover:bg-zinc-800"
              >
                Restore
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
