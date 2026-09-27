"use client";

import { useMemo } from "react";

import { SCOPE_OVERALL, type MasterPlayerRow, type TierScopeRow } from "@/lib/api";
import { availabilityOf, rankingsColumns, tierRuns, type BoardColumn } from "@/lib/draft";
import { scopeLabel } from "@/lib/masterboard";
import { AvailableRow } from "./AvailableRow";

/**
 * Six columns of who is left: the whole board, then one per position.
 *
 * This is the view you sit in on the clock. The sidebar answers "who is the best man left";
 * this answers "and who is the best man left AT EACH SPOT", side by side, which is the
 * comparison that actually decides a pick when two positions are both thin.
 *
 * EVERY COLUMN IS MY ORDER AND MY RANKS. The numbers down a position column are BOARD ranks,
 * not "3rd point guard" — the ranking is one board, sliced. What changes per column is the
 * TIER: a divider in the PG column is where my point-guard tiers break, and those cuts count
 * point guards (`lib/draft.ts:boardColumn` argues the full-order arithmetic that makes the
 * third-best AVAILABLE guard still read as tier 2 when four tier-1 guards are gone).
 *
 * The availability percentage and the draft button are the same row component the sidebar
 * uses, in its compact form. Everything here is derived by pure functions in `lib/draft.ts`;
 * this file is six boxes and a divider.
 */

export function DraftRankings({
  available,
  catalog,
  tiers,
  availability,
  targetPick,
  picksAway,
  clock,
  canDraft,
  isBusy,
  onDraft,
}: {
  available: MasterPlayerRow[];
  catalog: MasterPlayerRow[];
  tiers: TierScopeRow[];
  availability: Record<string, number>;
  /** The pick the percentages are about — always a later one than the clock. */
  targetPick: number | null;
  picksAway: number | null;
  /** Whose pick a Draft button would enter, in words. See `AvailableRow`. */
  clock: string | null;
  canDraft: boolean;
  isBusy: boolean;
  onDraft: (playerId: number) => void;
}) {
  const columns = useMemo(
    () => rankingsColumns(available, catalog, tiers),
    [available, catalog, tiers],
  );

  return (
    <div
      data-view="rankings"
      className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6"
    >
      {columns.map((column) => (
        <RankingColumn
          key={column.scope}
          column={column}
          availability={availability}
          targetPick={targetPick}
          picksAway={picksAway}
          clock={clock}
          canDraft={canDraft}
          isBusy={isBusy}
          onDraft={onDraft}
        />
      ))}
    </div>
  );
}

/** "Best available" for the whole board; the position's own name otherwise. */
function columnTitle(column: BoardColumn): string {
  return column.scope === SCOPE_OVERALL ? "Best available" : column.scope;
}

function RankingColumn({
  column,
  availability,
  targetPick,
  picksAway,
  clock,
  canDraft,
  isBusy,
  onDraft,
}: {
  column: BoardColumn;
  availability: Record<string, number>;
  targetPick: number | null;
  picksAway: number | null;
  clock: string | null;
  canDraft: boolean;
  isBusy: boolean;
  onDraft: (playerId: number) => void;
}) {
  const runs = useMemo(() => tierRuns(column.rows), [column.rows]);

  return (
    <section
      data-column={column.scope}
      className="flex min-w-0 flex-col rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
    >
      <header className="flex items-baseline justify-between gap-2 border-b border-zinc-200 px-2 py-1.5 dark:border-zinc-800">
        <h3
          className="truncate text-[11px] font-semibold tracking-wide text-zinc-600 uppercase dark:text-zinc-400"
          title={`Still available, in your board order — tiers are ${scopeLabel(column.scope)}.`}
        >
          {columnTitle(column)}
        </h3>
        <span className="shrink-0 font-mono text-[10px] text-zinc-500 tabular-nums">
          {column.rows.length}
        </span>
      </header>

      {column.rows.length === 0 ? (
        <p className="px-2 py-3 text-[11px] text-zinc-500">
          {column.size === 0
            ? "Nobody on your board plays there."
            : "Every one of them is gone."}
        </p>
      ) : (
        <ul className="flex max-h-[60vh] flex-col overflow-y-auto">
          {runs.map((run, index) => (
            <li key={`${run.tier ?? "none"}-${index}`}>
              {run.tier === null ? null : (
                // The divider carries the tier's NUMBER, because a line alone cannot say
                // that the men under it are tier 3 rather than tier 2 with four gone.
                <p
                  data-tier-divider={run.tier}
                  className="flex items-center gap-1.5 bg-zinc-50 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase dark:bg-zinc-900"
                >
                  Tier {run.tier}
                  <span aria-hidden className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
                </p>
              )}
              <ul className="flex flex-col divide-y divide-zinc-100 dark:divide-zinc-900">
                {run.rows.map((row) => (
                  <li key={row.player.espn_player_id}>
                    <AvailableRow
                      player={row.player}
                      availability={availabilityOf(availability, row.player.espn_player_id)}
                      targetPick={targetPick}
                      picksAway={picksAway}
                      clock={clock}
                      canDraft={canDraft}
                      isBusy={isBusy}
                      onDraft={onDraft}
                      compact
                    />
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
