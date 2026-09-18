"use client";

import { useMemo, useState } from "react";

import type { ConsensusMethod, ConsensusResponse, ConsensusRow, SourceInfo } from "@/lib/api";
import { compareBy, type SortDirection } from "@/lib/board";
import {
  METHOD_LABEL,
  cellValue,
  disagreementBand,
  disagreementRatio,
  spreadFor,
} from "@/lib/consensus";
import { MISSING, decimal, positions, whole } from "@/lib/format";

/**
 * The consensus board: a column per selected source, the average of them, and how far apart
 * they are.
 *
 * Built the same way `BoardTable` is — columns declared once and used for both the header and
 * the cell, so a sort control can never point at a different number than the one under it —
 * except that here the source columns are built from the RESPONSE rather than hardcoded. The
 * server decides the column order (it is the order the sources were asked for), so the
 * headers and the cells cannot drift apart even as sources are ticked on and off.
 *
 * What to actually look at is the Spread column. The consensus is the boring half; the rows
 * where a projection and someone's dynasty board are hundreds of places apart are the ones
 * that are either a mistake or an edge.
 */

type Column = {
  key: string;
  header: string;
  title?: string;
  align: "left" | "right";
  cell?: string;
  both?: string;
  render: (row: ConsensusRow) => React.ReactNode;
  /** Null makes the column unsortable. Rows sorting to `null` always sink to the bottom. */
  sort: ((row: ConsensusRow) => number | string | null) | null;
  first: SortDirection;
};

/** A source's cell: the rank or the percentile, or an em dash where it has no opinion. */
function SourceCell({
  row,
  source,
  method,
}: {
  row: ConsensusRow;
  source: SourceInfo;
  method: ConsensusMethod;
}) {
  const cell = row.cells[source.id];
  if (!cell) {
    return (
      <span
        className="text-zinc-400 dark:text-zinc-600"
        title={`${source.label} does not rank ${row.name} — he is left OUT of its average, not counted last`}
      >
        {MISSING}
      </span>
    );
  }
  return (
    <span title={`${source.label}: rank ${cell.rank}, percentile ${cell.percentile.toFixed(1)}`}>
      {method === "percentile" ? cell.percentile.toFixed(1) : cell.rank}
    </span>
  );
}

function SpreadCell({ row, method }: { row: ConsensusRow; method: ConsensusMethod }) {
  const spread = spreadFor(row, method);
  const band = disagreementBand(disagreementRatio(row));

  if (spread === null || band === null) {
    return (
      <span
        className="text-zinc-400 dark:text-zinc-600"
        title="Only one source ranks him — there is nothing to disagree with"
      >
        {MISSING}
      </span>
    );
  }
  return (
    <span
      data-disagreement={band.level}
      title={`${band.label} — the worst rank is ${(disagreementRatio(row) ?? 0).toFixed(1)}× the best`}
      className={`inline-block min-w-12 rounded px-1.5 py-0.5 ${band.className}`}
    >
      {decimal(spread, method === "percentile" ? 1 : 0)}
    </span>
  );
}

export function buildColumns(response: ConsensusResponse): Column[] {
  const method = response.method;

  return [
    {
      key: "rank",
      header: "#",
      title: "Place on this consensus board",
      align: "right",
      cell: "font-mono text-zinc-500 tabular-nums",
      render: (row) => row.rank,
      sort: (row) => row.rank,
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
      both: "hidden sm:table-cell",
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
      both: "hidden sm:table-cell",
      render: (row) => whole(row.age),
      sort: (row) => row.age,
      first: "asc",
    },
    {
      key: "consensus",
      header: `Consensus ${METHOD_LABEL[method]}`,
      title:
        method === "rank"
          ? "Equal-weight average of the places the selected sources give him (lower is better)"
          : "Equal-weight average of his positions in the shared pool (higher is better)",
      align: "right",
      cell: "font-mono tabular-nums font-semibold text-zinc-900 dark:text-zinc-100",
      render: (row) => decimal(row.consensus, method === "rank" ? 1 : 1),
      sort: (row) => row.consensus,
      first: method === "rank" ? "asc" : "desc",
    },
    {
      key: "spread",
      header: "Spread",
      title:
        "How far apart the sources that rank him are, shaded by how big that gap is relative " +
        "to where he sits — the darker the cell, the more they disagree",
      align: "right",
      cell: "font-mono tabular-nums",
      render: (row) => <SpreadCell row={row} method={method} />,
      // Sorted by the SHADING, not by the printed number, so clicking the column you are
      // reading the colour off gives you the rows that colour is picking out.
      sort: (row) => disagreementRatio(row),
      first: "desc",
    },
    {
      key: "coverage",
      header: "Src",
      title:
        "How many of the selected sources rank him. A player missing from a source is left " +
        "out of that source's average rather than counted last, so a thin number here is a " +
        "consensus built on less evidence, not a worse player",
      align: "right",
      cell: "font-mono tabular-nums text-zinc-500",
      render: (row) => (
        <span
          title={
            row.sources_missing.length > 0
              ? `Not ranked by: ${row.sources_missing.join(", ")}`
              : "Every selected source ranks him"
          }
          className={row.sources_present < response.sources.length ? "text-amber-600" : ""}
        >
          {row.sources_present}/{response.sources.length}
        </span>
      ),
      sort: (row) => row.sources_present,
      first: "desc",
    },
    ...response.sources.map(
      (source): Column => ({
        key: source.id,
        header: source.label,
        title: `${source.label} — ${METHOD_LABEL[method].toLowerCase()} (ranks ${source.player_count} players)`,
        align: "right",
        cell: "font-mono tabular-nums text-zinc-600 dark:text-zinc-400",
        render: (row) => <SourceCell row={row} source={source} method={method} />,
        // A source with no opinion sorts null and sinks, whichever way the column points.
        sort: (row) => {
          const cell = row.cells[source.id];
          return cell ? cellValue(cell, method) : null;
        },
        first: method === "rank" ? "asc" : "desc",
      }),
    ),
  ];
}

export function ConsensusTable({ response }: { response: ConsensusResponse }) {
  const [sort, setSort] = useState<{ key: string; direction: SortDirection } | null>(null);
  const columns = useMemo(() => buildColumns(response), [response]);

  const rows = useMemo(() => {
    if (!sort) return response.players;
    const column = columns.find((candidate) => candidate.key === sort.key);
    if (!column?.sort) return response.players;
    const ordered = compareBy(column.sort, sort.direction);
    return [...response.players].sort((a, b) => ordered(a, b) || a.rank - b.rank);
  }, [columns, response.players, sort]);

  const toggleSort = (column: Column) => {
    if (!column.sort) return;
    setSort((current) => {
      if (current?.key !== column.key) return { key: column.key, direction: column.first };
      // Second click flips, third hands the board back to the server's consensus order.
      return current.direction === column.first
        ? { key: column.key, direction: current.direction === "asc" ? "desc" : "asc" }
        : null;
    });
  };

  return (
    <div className="flex flex-col gap-2">
      {sort ? (
        <p className="text-xs text-zinc-500">
          Sorted by {columns.find((column) => column.key === sort.key)?.header}. Click it again
          to restore the consensus order.
        </p>
      ) : null}

      <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full border-collapse text-[13px]">
          <caption className="sr-only">
            Players ranked by the equal-weight consensus of{" "}
            {response.sources.map((source) => source.label).join(", ")}
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
          <tbody>
            {rows.map((row) => (
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
        </table>
      </div>
    </div>
  );
}
