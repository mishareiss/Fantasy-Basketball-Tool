"use client";

import { Command, Panel } from "@/components/Panel";
import { API_BASE_URL, ApiError } from "@/lib/api";

/**
 * What the board shows when it has no rows to show.
 *
 * Three different nothings, and telling them apart is the whole point: the backend is down,
 * the backend is up but nothing has been synced into it, or something else went wrong.
 * A blank table would look identical in all three cases and be useful in none.
 *
 * `Panel`/`Command` moved to components/Panel when the importer wanted the same card.
 */

export function BoardLoading() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <p className="text-sm text-zinc-500">Loading board…</p>
      <div aria-hidden className="mt-4 flex flex-col gap-2">
        {Array.from({ length: 8 }, (_, index) => (
          <div
            key={index}
            className="h-6 animate-pulse rounded bg-zinc-100 dark:bg-zinc-900"
            style={{ opacity: 1 - index * 0.1 }}
          />
        ))}
      </div>
    </div>
  );
}

/** The backend answered 404: it is running, it just has no projections stored yet. */
export function BoardEmpty({ detail }: { detail?: string }) {
  return (
    <Panel title="No board yet — nothing is synced">
      <p>
        The API is up but has no projections stored, so there is nothing to rank. Pull the
        league and the player pool in, then reload:
      </p>
      <ul className="flex list-none flex-col gap-1.5">
        <li>
          <Command>make sync</Command> — scoring settings + projections from ESPN
        </li>
        <li>
          <Command>make sync-ages</Command> — birthdates, so dynasty values are age-adjusted
        </li>
      </ul>
      {detail ? <p className="font-mono text-xs text-zinc-500">{detail}</p> : null}
    </Panel>
  );
}

/** No response at all: wrong port, backend not started, or CORS said no. */
export function BoardUnreachable() {
  return (
    <Panel title="Can’t reach the API">
      <p>
        Nothing answered at <Command>{API_BASE_URL}</Command>. Start it with{" "}
        <Command>make backend</Command> (and <Command>make db-up</Command> if Postgres isn’t
        running), then reload.
      </p>
      <p className="text-xs">
        If the API lives somewhere else, set <Command>NEXT_PUBLIC_API_BASE_URL</Command> in{" "}
        <Command>.env</Command> and restart the dev server.
      </p>
    </Panel>
  );
}

export function BoardFailed({ error }: { error: ApiError }) {
  return (
    <Panel title="The board request failed">
      <p>{error.detail ?? error.message}</p>
      <p className="font-mono text-xs text-zinc-500">{error.message}</p>
    </Panel>
  );
}

/** Route an ApiError to the state that explains it. */
export function BoardError({ error }: { error: ApiError }) {
  if (error.status === undefined) return <BoardUnreachable />;
  if (error.status === 404) return <BoardEmpty detail={error.detail} />;
  return <BoardFailed error={error} />;
}

/** The backend has a board, but this filter matched nobody on it. */
export function BoardNoMatches({ position }: { position: string | null }) {
  return (
    <Panel title="No players match this filter">
      <p>
        The board has rows, but none at {position ?? "this position"}. Clear the position
        filter to see the full ranking.
      </p>
    </Panel>
  );
}
