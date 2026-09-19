"use client";

import Link from "next/link";

import { Command, Panel } from "@/components/Panel";
import { API_BASE_URL, ApiError } from "@/lib/api";

/**
 * What the master board shows instead of an order.
 *
 * The empty one is the interesting case and it is NOT an error: the board is seeded from the
 * consensus on the first read, so "nothing here" can only mean there is no consensus to seed
 * from yet — nothing synced, nothing imported. The useful thing to say is which of those.
 */

export function MasterLoading({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <p className="text-sm text-zinc-500">{label}</p>
      <div aria-hidden className="mt-4 flex flex-col gap-2">
        {Array.from({ length: 6 }, (_, index) => (
          <div
            key={index}
            className="h-6 animate-pulse rounded bg-zinc-100 dark:bg-zinc-900"
            style={{ opacity: 1 - index * 0.15 }}
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
      title: "That order is out of date",
      advice: (
        <>
          The board changed underneath this page — most likely a new player was reconciled in,
          or one was set aside in another tab. Nothing was saved. Reload to pick up the
          current board and make the move again.
        </>
      ),
    };
  }
  if (error.status === 409) {
    return {
      title: "The board already has entries",
      advice: (
        <>
          Re-seeding throws away every rank, tag and note on it, so it has to be asked for
          explicitly. Nothing was changed.
        </>
      ),
    };
  }
  if (error.status === 404) {
    return {
      title: "That player isn’t there any more",
      advice: <>He was removed from the catalog. Reload to see what the board actually holds.</>,
    };
  }
  if (error.status === 400) {
    return {
      title: "Unknown horizon",
      advice: <>The board reads against dynasty or win-now, and that was neither.</>,
    };
  }
  return { title: "That didn’t work", advice: null };
}

export function MasterFailed({ error }: { error: ApiError }) {
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
 * A clean 200 with nobody on it — the honest starting state of the feature, not a 404.
 *
 * The board seeds itself from the consensus, so an empty one means the consensus is empty
 * too: there is nothing to have an opinion about yet.
 */
export function MasterEmpty() {
  return (
    <Panel title="Nothing to rank yet">
      <p>
        Your board is built from the consensus the first time you open it, and no source
        ranks anybody right now. Run <Command>make sync</Command> to pull the player pool and
        ESPN&rsquo;s projection, or bring in a list through the{" "}
        <Link href="/import" className="underline">
          importer
        </Link>
        .
      </p>
      <p>
        Once one source has an opinion, this page opens on it — in consensus order, once — and
        every move you make from there is yours and stays put.
      </p>
    </Panel>
  );
}

/**
 * The filter found nobody. Distinct from an empty board, and worth its own panel: the board
 * is fine and full, there is simply nobody on it listed at that position — which on a real
 * board means the source it was seeded from doesn't list that position at all.
 */
export function NoneAtPosition({ position }: { position: string }) {
  return (
    <Panel title={`Nobody on your board is listed at ${position}`}>
      <p>
        Your board still has everyone on it — this is a view of it, not a filter that removed
        anyone. Switch back to <strong>All</strong> to see the whole order, or pick another
        position.
      </p>
      <p>
        Positions come from the player catalog, so a position with nobody in it means no
        source we have loaded lists one. Run <Command>make sync</Command> if that looks wrong.
      </p>
    </Panel>
  );
}

/** Search found nobody. Distinct from an empty board: the board is fine, the term isn't. */
export function NoSearchMatch({ term }: { term: string }) {
  return (
    <Panel title={`Nobody on your board matches “${term}”`}>
      <p>
        Search covers the whole order, not just the rows on screen, so he really isn&rsquo;t
        on it. A player no source ranks is never added — check the spelling, or clear the box
        to get the board back.
      </p>
    </Panel>
  );
}
