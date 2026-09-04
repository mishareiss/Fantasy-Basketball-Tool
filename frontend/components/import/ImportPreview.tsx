"use client";

import Link from "next/link";

import type { ImportResponse, ImportRowOutcome, MatchCandidate } from "@/lib/api";
import { STATUS_HINT, STATUS_STYLE } from "@/lib/importing";

/**
 * Step four: what the import would do, row by row.
 *
 * Four things, in the order they answer questions. The counters say how it went at a glance.
 * The **detected column map** comes next and not last, because a silently mis-detected column
 * is the one failure a row-by-row list can't reveal — every row will look matched and every
 * number will be off. The handler's notes say what the counters can't ("replacing 11 entries
 * with 9, 2 player(s) drop off"). Then the rows themselves, colour-coded by status.
 *
 * Review and unmatched rows are the working half. A row we couldn't place carries its
 * candidates, and picking one records an alias and re-previews — after which that row lands
 * as `alias` at confidence 1.0, this time and every time after. The rows with no candidate at
 * all are pulled out into their own list: nobody is going to resolve those from here, and
 * pretending otherwise wastes a scroll.
 */

function Count({
  label,
  value,
  tone,
  title,
}: {
  label: string;
  value: number;
  tone?: string;
  title?: string;
}) {
  return (
    <div
      title={title}
      className={`flex min-w-20 flex-col gap-0.5 rounded-md px-2.5 py-1.5 ${tone ?? "bg-zinc-100 dark:bg-zinc-900"}`}
    >
      <span className="text-[10px] tracking-wide uppercase opacity-70">{label}</span>
      <span className="font-mono text-sm font-semibold tabular-nums">{value}</span>
    </div>
  );
}

function Values({ row }: { row: ImportRowOutcome }) {
  const shown = Object.entries(row.values).filter(([, value]) => value !== null);
  if (shown.length === 0) return <span className="text-zinc-400">—</span>;
  return (
    <span className="font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
      {shown.map(([field, value]) => `${field}=${value}`).join("  ")}
    </span>
  );
}

function Candidates({
  row,
  onResolve,
  resolving,
}: {
  row: ImportRowOutcome;
  onResolve: (row: ImportRowOutcome, candidate: MatchCandidate) => void;
  resolving: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 pt-1">
      <span className="text-[11px] text-zinc-500">Is this…</span>
      {row.candidates.map((candidate) => (
        <button
          key={candidate.player_id}
          type="button"
          disabled={resolving}
          onClick={() => onResolve(row, candidate)}
          title={`Record an alias: this source calls ${candidate.full_name} "${row.source_name}"`}
          className="rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-700 hover:border-sky-500 hover:bg-sky-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:border-sky-400 dark:hover:bg-sky-950/40"
        >
          {candidate.full_name}
          <span className="ml-1 font-mono text-zinc-500">
            {candidate.nba_team ?? "—"} {candidate.score.toFixed(2)}
          </span>
        </button>
      ))}
    </div>
  );
}

function RowTable({
  rows,
  onResolve,
  resolving,
}: {
  rows: ImportRowOutcome[];
  onResolve: (row: ImportRowOutcome, candidate: MatchCandidate) => void;
  resolving: number | null;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full min-w-[44rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-left text-[11px] tracking-wide text-zinc-500 uppercase dark:border-zinc-800">
            <th scope="col" className="px-2 py-1.5 text-right font-medium">
              Line
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              Status
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              In the file
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              Our player
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              Values
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.line}
              className="border-b border-zinc-100 align-top last:border-b-0 dark:border-zinc-900"
            >
              <td className="px-2 py-1.5 text-right font-mono text-xs text-zinc-500 tabular-nums">
                {row.line}
              </td>
              <td className="px-2 py-1.5">
                <span
                  title={STATUS_HINT[row.status]}
                  className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                    STATUS_STYLE[row.status] ?? STATUS_STYLE.invalid
                  }`}
                >
                  {row.status}
                </span>
              </td>
              <td className="px-2 py-1.5">
                <span className="text-zinc-800 dark:text-zinc-200">{row.source_name}</span>
                {row.team ? (
                  <span className="ml-1.5 font-mono text-[11px] text-zinc-500">{row.team}</span>
                ) : null}
                {row.note ? (
                  <p className="text-[11px] text-zinc-500 italic">{row.note}</p>
                ) : null}
              </td>
              <td className="px-2 py-1.5">
                {row.player_name ? (
                  <span className="text-zinc-800 dark:text-zinc-200">{row.player_name}</span>
                ) : (
                  <span className="text-zinc-400">—</span>
                )}
                {row.method ? (
                  <span className="ml-1.5 font-mono text-[11px] text-zinc-500">
                    {row.method} {row.confidence.toFixed(2)}
                  </span>
                ) : null}
                {row.candidates.length > 0 && row.status !== "matched" ? (
                  <Candidates
                    row={row}
                    onResolve={onResolve}
                    resolving={resolving !== null}
                  />
                ) : null}
              </td>
              <td className="px-2 py-1.5">
                <Values row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Unmatched rows with nothing to click: a name nothing in our pool resembles. */
function ManualMatches({ rows }: { rows: ImportRowOutcome[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/40">
      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        Needs a manual match ({rows.length})
      </h3>
      <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
        Nothing in our player pool resembles these names, so there is no candidate to pick.
        Most are players we don&rsquo;t carry at all, which is not a decision anyone needs to
        make — the rest need an alias against a player looked up by hand, which the board
        can&rsquo;t do yet.
      </p>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {rows.map((row) => (
          <li key={row.line} className="font-mono text-xs text-zinc-600 dark:text-zinc-400">
            <span className="text-zinc-400">{row.line}</span> {row.source_name}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ImportPreview({
  response,
  onResolve,
  resolving,
}: {
  response: ImportResponse;
  onResolve: (row: ImportRowOutcome, candidate: MatchCandidate) => void;
  /** The line currently being resolved, so the candidate buttons can't be double-clicked. */
  resolving: number | null;
}) {
  const committed = !response.dry_run;
  const detected = Object.entries(response.columns);
  const manual = response.rows.filter(
    (row) => row.status === "unmatched" && row.candidates.length === 0,
  );

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          {committed ? "Committed" : "Preview — nothing written yet"}
        </h2>
        <span className="font-mono text-xs text-zinc-500">
          {response.kind} · {response.source} · {response.season} · delimiter{" "}
          {JSON.stringify(response.delimiter)}
          {Object.entries(response.options).map(([key, value]) => ` · ${key}=${value}`)}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <Count label="Parsed" value={response.rows_parsed} title="Data rows read from the file" />
        <Count
          label="Matched"
          value={response.matched}
          tone={STATUS_STYLE.matched}
          title={STATUS_HINT.matched}
        />
        <Count
          label="Review"
          value={response.review}
          tone={STATUS_STYLE.review}
          title={STATUS_HINT.review}
        />
        <Count
          label="Unmatched"
          value={response.unmatched}
          tone={STATUS_STYLE.unmatched}
          title={STATUS_HINT.unmatched}
        />
        <Count
          label="Duplicate"
          value={response.duplicate}
          tone={STATUS_STYLE.duplicate}
          title={STATUS_HINT.duplicate}
        />
        <Count
          label="Invalid"
          value={response.invalid}
          tone={STATUS_STYLE.invalid}
          title={STATUS_HINT.invalid}
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <Count
          label={committed ? "Rows created" : "Would create"}
          value={response.rows_created}
        />
        <Count label={committed ? "Updated" : "Would update"} value={response.rows_updated} />
        <Count label="Unchanged" value={response.rows_unchanged} />
        <Count
          label={committed ? "Aliases made" : "New aliases"}
          value={response.aliases_created}
          title="Every accepted match is remembered, so the next import never re-guesses it"
        />
        <Count
          label="Aliases known"
          value={response.aliases_existing}
          title="Already recorded — the cheap proof this file has landed before"
        />
      </div>

      <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/40">
        <h3 className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
          Detected columns
        </h3>
        <p className="mt-1 text-[11px] text-zinc-500">
          Check this first: a column read as the wrong field makes every row look fine and
          every number wrong.
        </p>
        <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
          {detected.length === 0 ? (
            <dd className="text-xs text-zinc-500">None.</dd>
          ) : (
            detected.map(([field, header]) => (
              <div key={field} className="flex items-baseline gap-1.5 font-mono text-xs">
                <dt className="font-semibold text-zinc-800 dark:text-zinc-200">{field}</dt>
                <dd className="text-zinc-500">←&nbsp;{header}</dd>
              </div>
            ))
          )}
        </dl>
        {response.rows_skipped_blank > 0 ? (
          <p className="mt-2 text-[11px] text-zinc-500">
            {response.rows_skipped_blank} blank/headerless row(s) skipped.
          </p>
        ) : null}
      </div>

      {response.notes.length > 0 ? (
        <ul className="flex list-none flex-col gap-1 rounded-lg border border-zinc-200 bg-white p-3 text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300">
          {response.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {committed ? (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Written.{" "}
          <Link href="/" className="text-sky-600 hover:underline dark:text-sky-400">
            Back to the board →
          </Link>
        </p>
      ) : null}

      <ManualMatches rows={manual} />

      {response.rows.length > 0 ? (
        <RowTable rows={response.rows} onResolve={onResolve} resolving={resolving} />
      ) : null}
    </section>
  );
}
