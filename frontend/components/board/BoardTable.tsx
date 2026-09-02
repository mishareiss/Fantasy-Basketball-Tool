"use client";

import { useMemo, useState } from "react";

import type { BoardResponse, BoardRow, Horizon, TierSummaryRow } from "@/lib/api";
import { HORIZON_LABEL, horizonValue, otherHorizon } from "@/lib/board";
import { MISSING, decimal, multiplier, positions, whole } from "@/lib/format";

/**
 * The board itself: one dense row per player, cut into tiers.
 *
 * Two things drive every decision here. Columns are declared once and used for both the
 * header and the cells, so a sort control can never end up pointing at a different number
 * than the one under it. And the rows arrive from the server already in the selected
 * horizon's order — sorting is a local override on top of that, not a second ranking, which
 * is why turning a sort on hides the tier dividers: tiers are cuts in the *server's* order
 * and mean nothing once the rows are shuffled by ADP.
 */

type SortDirection = "asc" | "desc";

type Column = {
  key: string;
  header: string;
  /** Long-form header, shown as a tooltip. */
  title?: string;
  align: "left" | "right";
  /** Extra classes for the cell (not the header). */
  cell?: string;
  /** Extra classes for both, e.g. hiding a column on narrow screens. */
  both?: string;
  render: (row: BoardRow) => React.ReactNode;
  /** Null makes the column unsortable. Rows sorting to `null` always sink to the bottom. */
  sort: ((row: BoardRow) => number | string | null) | null;
  /** Which way the first click sorts — descending for "bigger is better" columns. */
  first: SortDirection;
};

function buildColumns(response: BoardResponse): Column[] {
  const horizon: Horizon = response.horizon;
  const secondary = otherHorizon(horizon);

  return [
    {
      key: "rank",
      header: "#",
      title: "Rank on this board",
      align: "right",
      cell: "font-mono text-zinc-500 tabular-nums",
      render: (row) => row.rank,
      sort: (row) => row.rank,
      first: "asc",
    },
    {
      key: "tier",
      header: "Tier",
      title: "Tier on the overall board",
      align: "right",
      cell: "font-mono tabular-nums",
      render: (row) =>
        row.tier === null ? (
          <span className="text-zinc-400 dark:text-zinc-600">{MISSING}</span>
        ) : (
          <span className="text-zinc-600 dark:text-zinc-400">{row.tier}</span>
        ),
      sort: (row) => row.tier,
      first: "asc",
    },
    {
      key: "name",
      header: "Player",
      align: "left",
      cell: "font-medium text-zinc-900 dark:text-zinc-100",
      render: (row) => row.name,
      sort: (row) => row.name,
      first: "asc",
    },
    {
      key: "team",
      header: "Team",
      align: "left",
      cell: "text-zinc-500",
      render: (row) => row.nba_team ?? MISSING,
      sort: (row) => row.nba_team,
      first: "asc",
    },
    {
      key: "positions",
      header: "Pos",
      align: "left",
      cell: "text-zinc-500",
      render: (row) => positions(row.positions),
      sort: (row) => (row.positions.length > 0 ? row.positions.join("/") : null),
      first: "asc",
    },
    {
      key: "age",
      header: "Age",
      title: `Whole years old at ${response.age_as_of}`,
      align: "right",
      cell: "font-mono tabular-nums text-zinc-500",
      render: (row) => whole(row.age),
      sort: (row) => row.age,
      first: "asc",
    },
    {
      key: "value",
      header: HORIZON_LABEL[horizon],
      title: `${HORIZON_LABEL[horizon]} value — the number this board is ranked by`,
      align: "right",
      cell: "font-mono tabular-nums font-semibold text-zinc-900 dark:text-zinc-100",
      render: (row) => decimal(horizonValue(row, horizon)),
      sort: (row) => horizonValue(row, horizon),
      first: "desc",
    },
    {
      key: "secondary",
      header: HORIZON_LABEL[secondary],
      title: `${HORIZON_LABEL[secondary]} value — the horizon this board is NOT ranked by`,
      align: "right",
      cell: "font-mono tabular-nums text-zinc-500",
      render: (row) => decimal(horizonValue(row, secondary)),
      sort: (row) => horizonValue(row, secondary),
      first: "desc",
    },
    {
      key: "fppg",
      header: "FP/G",
      title: "Projected fantasy points per game under our scoring",
      align: "right",
      cell: "font-mono tabular-nums text-zinc-500",
      both: "hidden sm:table-cell",
      render: (row) => decimal(row.fantasy_points_per_game),
      sort: (row) => row.fantasy_points_per_game,
      first: "desc",
    },
    {
      key: "adp",
      header: "ADP",
      title: `Redraft ADP from ${response.adp_source}${
        response.adp_season ? ` (${response.adp_season})` : ""
      }`,
      align: "right",
      cell: "font-mono tabular-nums text-zinc-500",
      render: (row) => decimal(row.adp),
      sort: (row) => row.adp,
      first: "asc",
    },
    {
      key: "age_multiplier",
      header: "×Age",
      title: "The age curve's multiplier on this player's dynasty value",
      align: "right",
      cell: "font-mono tabular-nums",
      both: "hidden sm:table-cell",
      render: (row) => (
        <span
          className={
            row.age_adjusted ? "text-zinc-500" : "text-zinc-400 italic dark:text-zinc-600"
          }
          title={row.age_adjusted ? undefined : "No birthdate stored — not age-adjusted"}
        >
          {multiplier(row.age_multiplier)}
        </span>
      ),
      sort: (row) => row.age_multiplier,
      first: "desc",
    },
  ];
}

/** Rows in tier order, each group carrying the summary that labels its divider. */
type Group = {
  tier: number | null;
  summary: TierSummaryRow | null;
  rows: BoardRow[];
};

/**
 * Split the page's rows into contiguous tier groups.
 *
 * The rows arrive ranked, so a tier change is simply a change in the `tier` column; there is
 * no need to (and no reason to) re-derive the breaks from the values. Untiered players sit in
 * a trailing group of their own — they are below the tiered pool, not in a worst tier.
 */
export function groupByTier(rows: BoardRow[]): Group[] {
  const groups: Group[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.tier === row.tier) {
      last.rows.push(row);
    } else {
      groups.push({ tier: row.tier, summary: null, rows: [row] });
    }
  }
  return groups;
}

function compare(a: number | string | null, b: number | string | null): number {
  // Missing data sinks, whichever way the column is pointed — an unpriced player is not the
  // best in the league just because you clicked "descending".
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "string" || typeof b === "string") {
    return String(a).localeCompare(String(b));
  }
  return a - b;
}

function TierDivider({
  group,
  span,
}: {
  group: Group;
  span: number;
}) {
  const size = group.summary?.size ?? group.rows.length;
  const shown = group.rows.length;

  return (
    <tr className="border-t border-zinc-300 dark:border-zinc-700">
      <th
        scope="colgroup"
        colSpan={span}
        className="bg-zinc-100 px-3 py-1.5 text-left dark:bg-zinc-900"
      >
        <span className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <span className="text-xs font-semibold tracking-wide text-zinc-800 uppercase dark:text-zinc-200">
            {group.tier === null ? "Untiered" : `Tier ${group.tier}`}
          </span>
          <span className="text-[11px] font-normal text-zinc-500">
            {group.tier === null
              ? `${shown} below the tiered pool`
              : `${size} player${size === 1 ? "" : "s"}${shown === size ? "" : ` · ${shown} shown`}`}
          </span>
          {group.summary ? (
            <span className="font-mono text-[11px] font-normal text-zinc-500 tabular-nums">
              {decimal(group.summary.value_high)} → {decimal(group.summary.value_low)}
            </span>
          ) : null}
          {group.summary?.gap_ratio ? (
            <span
              className="font-mono text-[11px] font-normal text-zinc-500 tabular-nums"
              title="How big the drop into this tier was, as a multiple of the board's typical drop"
            >
              {group.summary.gap_ratio.toFixed(1)}× drop
            </span>
          ) : null}
        </span>
      </th>
    </tr>
  );
}

export function BoardTable({ response }: { response: BoardResponse }) {
  const [sort, setSort] = useState<{ key: string; direction: SortDirection } | null>(null);
  const columns = useMemo(() => buildColumns(response), [response]);

  // A sort re-orders the whole page, so tier runs stop being contiguous and the dividers
  // would be lying about where the breaks are. Show them only in the server's own order.
  const dividersOn = response.tiers === "auto" && sort === null;

  const rows = useMemo(() => {
    if (!sort) return response.players;
    const column = columns.find((candidate) => candidate.key === sort.key);
    if (!column?.sort) return response.players;
    const accessor = column.sort;
    const sign = sort.direction === "asc" ? 1 : -1;
    return [...response.players].sort(
      (a, b) => sign * compare(accessor(a), accessor(b)) || a.rank - b.rank,
    );
  }, [columns, response.players, sort]);

  const groups = useMemo(() => {
    if (!dividersOn) return [{ tier: null, summary: null, rows } satisfies Group];
    const summaries = new Map(response.tier_summary.map((tier) => [tier.tier, tier]));
    return groupByTier(rows).map((group) => ({
      ...group,
      summary: group.tier === null ? null : (summaries.get(group.tier) ?? null),
    }));
  }, [dividersOn, response.tier_summary, rows]);

  const toggleSort = (column: Column) => {
    if (!column.sort) return;
    setSort((current) => {
      if (current?.key !== column.key) return { key: column.key, direction: column.first };
      // Second click flips, third click hands the board back to the server's ranking.
      return current.direction === column.first
        ? { key: column.key, direction: current.direction === "asc" ? "desc" : "asc" }
        : null;
    });
  };

  return (
    <div className="flex flex-col gap-2">
      {sort ? (
        <p className="text-xs text-zinc-500">
          Sorted by {columns.find((column) => column.key === sort.key)?.header} — tier
          dividers hidden. Click the column again to restore the{" "}
          {HORIZON_LABEL[response.horizon].toLowerCase()} ranking.
        </p>
      ) : null}

      <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full border-collapse text-[13px]">
          <caption className="sr-only">
            Players ranked by {HORIZON_LABEL[response.horizon]} value
            {response.position ? `, filtered to ${response.position}` : ""}
          </caption>
          <thead className="sticky top-0 z-10">
            <tr className="bg-zinc-50 dark:bg-zinc-900/95">
              {columns.map((column) => {
                const active = sort?.key === column.key;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    aria-sort={
                      active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"
                    }
                    className={`border-b border-zinc-200 px-3 py-2 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase dark:border-zinc-800 ${
                      column.align === "right" ? "text-right" : "text-left"
                    } ${column.both ?? ""}`}
                  >
                    {column.sort ? (
                      <button
                        type="button"
                        title={column.title}
                        onClick={() => toggleSort(column)}
                        className={`cursor-pointer hover:text-zinc-900 dark:hover:text-zinc-100 ${
                          active ? "text-zinc-900 dark:text-zinc-100" : ""
                        }`}
                      >
                        {column.header}
                        <span aria-hidden className="ml-1 inline-block w-2">
                          {active ? (sort.direction === "asc" ? "▲" : "▼") : ""}
                        </span>
                      </button>
                    ) : (
                      <span title={column.title}>{column.header}</span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>

          {groups.map((group, index) => (
            <tbody key={group.tier ?? `untiered-${index}`}>
              {dividersOn ? <TierDivider group={group} span={columns.length} /> : null}
              {group.rows.map((row) => (
                <tr
                  key={row.espn_player_id}
                  className="border-b border-zinc-100 last:border-b-0 hover:bg-zinc-50 dark:border-zinc-900 dark:hover:bg-zinc-900/60"
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={`px-3 py-1.5 whitespace-nowrap ${
                        column.align === "right" ? "text-right" : "text-left"
                      } ${column.cell ?? ""} ${column.both ?? ""}`}
                    >
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </div>
  );
}
