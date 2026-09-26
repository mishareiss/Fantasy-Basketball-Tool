"use client";

import Link from "next/link";

import { Command, Panel } from "@/components/Panel";
import { API_BASE_URL, ApiError } from "@/lib/api";

/**
 * What the draft room shows instead of a board, and the two button styles it shares.
 *
 * The interesting state here is the 404, and it is NOT an error: there is one draft at a
 * time and none of them exists until somebody starts one, so `GET /draft` answering 404 is
 * the page's ordinary opening move. `DraftRoomPage` turns it into the setup form rather than
 * sending it here.
 */

export const BUTTON =
  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed " +
  "disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

export const QUIET_BUTTON =
  `${BUTTON} border border-zinc-300 text-zinc-700 hover:bg-zinc-100 ` +
  "dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900";

export const PRIMARY_BUTTON =
  `${BUTTON} bg-zinc-900 text-white hover:bg-zinc-700 ` +
  "dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";

export const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm text-zinc-800 " +
  "placeholder:text-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

export function DraftLoading({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <p className="text-sm text-zinc-500">{label}</p>
      <div aria-hidden className="mt-4 flex flex-col gap-2">
        {Array.from({ length: 4 }, (_, index) => (
          <div
            key={index}
            className="h-6 animate-pulse rounded bg-zinc-100 dark:bg-zinc-900"
            style={{ opacity: 1 - index * 0.2 }}
          />
        ))}
      </div>
    </div>
  );
}

/** The title and the one sentence of advice each failure mode earns. */
function explain(error: ApiError): { title: string; advice: React.ReactNode } {
  if (error.status === undefined) {
    return {
      title: "Can’t reach the API",
      advice: (
        <>
          Nothing answered at <Command>{API_BASE_URL}</Command>. Start it with{" "}
          <Command>make backend</Command> (and <Command>make db-up</Command> if Postgres
          isn’t running), then try again.
        </>
      ),
    };
  }
  if (error.status === 422) {
    return {
      title: "That pick was refused",
      advice: (
        <>
          The draft has one clock and one board, and the engine checks both: a player already
          taken, a player no source ranks (he isn’t in this draft’s universe at all), or a
          pick out of turn. Nothing was written.
        </>
      ),
    };
  }
  if (error.status === 409) {
    return {
      title: "The draft couldn’t take that",
      advice: (
        <>
          Either there was nothing to undo, or a draft already exists and replacing it has to
          be asked for explicitly. Nothing was changed.
        </>
      ),
    };
  }
  if (error.status === 400) {
    return {
      title: "That field can’t be built",
      advice: (
        <>
          The horizon or the sources this draft is modelled against no longer resolve. Start
          the draft over to model it against what we actually hold.
        </>
      ),
    };
  }
  return { title: "That didn’t work", advice: null };
}

export function DraftFailed({ error }: { error: ApiError }) {
  const { title, advice } = explain(error);
  return (
    <div role="alert">
      <Panel title={title}>
        <p className="font-medium text-zinc-800 dark:text-zinc-200">
          {error.detail ?? error.message}
        </p>
        {advice ? <p>{advice}</p> : null}
        <p className="font-mono text-xs text-zinc-500">{error.message}</p>
      </Panel>
    </div>
  );
}

/**
 * The catalog failed, but the draft didn't.
 *
 * Worth its own note rather than the error panel: the board, the clock and every control
 * still work — the one thing that doesn't is finding a player by name, because the names
 * come from the master board and that read is what failed.
 */
export function CatalogUnavailable({ error }: { error: ApiError }) {
  return (
    <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
      Couldn’t read your board, so the search box has no names to offer:{" "}
      {error.detail ?? error.message}. The draft itself is fine — the grid, the clock and the
      simulation controls all still work. Fix it on{" "}
      <Link href="/my-board" className="underline">
        My Board
      </Link>{" "}
      and reload.
    </p>
  );
}
