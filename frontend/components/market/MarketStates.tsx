"use client";

import Link from "next/link";

import { Command, Panel } from "@/components/Panel";
import { API_BASE_URL, ApiError } from "@/lib/api";

/**
 * What the market page shows instead of a list of lines.
 *
 * The empty state is the one that matters, and it is not an error: a book you have never
 * entered a line for is the starting state of this page, and the useful thing to say is what
 * to do next rather than that something went wrong.
 */

export function MarketLoading({ label }: { label: string }) {
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
  if (error.status === 409) {
    return {
      title: "Nothing to price a line with",
      advice: (
        <>
          A market line is scored with our league&rsquo;s own coefficients, and none are
          stored. Run <Command>make sync</Command> once and come back — nothing was written.
        </>
      ),
    };
  }
  if (error.status === 422) {
    return {
      title: "That stat can’t be priced",
      advice: (
        <>
          Combination props (PRA, pts+reb) have no coefficient of their own, and rate stats
          (FG%, TS%) can&rsquo;t be multiplied by a per-game number. Pick one of the stats in
          the list — they are the ones our league actually pays for.
        </>
      ),
    };
  }
  if (error.status === 404) {
    return {
      title: "That’s not there any more",
      advice: (
        <>
          The line or the player is gone — most likely deleted in another tab. Reload to see
          what is actually stored.
        </>
      ),
    };
  }
  if (error.status === 400) {
    return {
      title: "Missing a season",
      advice: (
        <>
          Type one in, or set <Command>ESPN_SEASON</Command> on the backend. A stored line
          without a season can&rsquo;t be compared to anything later.
        </>
      ),
    };
  }
  return { title: "That didn’t work", advice: null };
}

export function MarketFailed({ error }: { error: ApiError }) {
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

/** No lines yet for this book and season — the starting state, not a failure. */
export function MarketEmpty({ source, season }: { source: string; season: number }) {
  return (
    <Panel title={`No lines stored for ${source}, season ${season}`}>
      <p>
        Add one above: a player, the stat the prop is on, the number, and the price on each
        side if the book published one. An unpriced line is still a line — it derives itself.
      </p>
      <p>
        A whole page of props is faster through the{" "}
        <Link href="/import" className="underline">
          importer
        </Link>{" "}
        as a <Command>market_line</Command> paste; this page is for keeping them right
        afterwards.
      </p>
    </Panel>
  );
}

/** A league sync has never run, so there is no stat that could be priced. */
export function NoScoringRules() {
  return (
    <Panel title="No scoring rules stored yet">
      <p>
        A market line is only worth something under OUR scoring — the same coefficients that
        price ESPN&rsquo;s projection and every imported one. Run <Command>make sync</Command>{" "}
        once and the stats you can enter a line on will appear here.
      </p>
    </Panel>
  );
}
