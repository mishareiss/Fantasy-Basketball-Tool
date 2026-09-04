"use client";

import { Command, Panel } from "@/components/Panel";
import { API_BASE_URL, ApiError } from "@/lib/api";

/**
 * What the importer shows instead of a preview.
 *
 * The backend already writes the useful half of every one of these — "run `make sync` first",
 * "unknown option(s) for a ranking import" — so the job here is to put its `detail` where it
 * can be read and add the one sentence it can't know: which of the caller's decisions to go
 * back and change.
 */

export function ImportLoading({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <p className="text-sm text-zinc-500">{label}</p>
      <div aria-hidden className="mt-4 flex flex-col gap-2">
        {Array.from({ length: 5 }, (_, index) => (
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
          isn’t running), then try again. Your table is still in the box.
        </>
      ),
    };
  }
  if (error.status === 409) {
    return {
      title: "Nothing to price these rows with",
      advice: (
        <>
          A projection import needs our league&rsquo;s scoring coefficients, and none are
          stored. Run <Command>make sync</Command> once, then import again — nothing was
          written.
        </>
      ),
    };
  }
  if (error.status === 422) {
    return {
      title: "That table, or that option, can’t be used",
      advice: (
        <>
          The request was fine; its content wasn&rsquo;t. Either no usable columns were found
          — check the header row against the column hints above — or an option is wrong for
          this kind. Nothing was written.
        </>
      ),
    };
  }
  if (error.status === 404) {
    return {
      title: "No such import kind",
      advice: <>Pick one of the kinds above; the greyed-out ones aren&rsquo;t built yet.</>,
    };
  }
  if (error.status === 400) {
    return {
      title: "Missing a season",
      advice: (
        <>
          Type one in, or set <Command>ESPN_SEASON</Command> on the backend. A stored row with
          no season can&rsquo;t be compared to anything later.
        </>
      ),
    };
  }
  return { title: "The import failed", advice: null };
}

export function ImportFailed({ error }: { error: ApiError }) {
  const { title, advice } = explain(error);
  return (
    <div role="alert">
      <Panel title={title}>
        {/* The backend's own words first: they are the specific half. */}
        <p className="font-medium text-zinc-800 dark:text-zinc-200">
          {error.detail ?? error.message}
        </p>
        {advice ? <p>{advice}</p> : null}
        <p className="font-mono text-xs text-zinc-500">{error.message}</p>
      </Panel>
    </div>
  );
}

/** The kind list itself failed, so there is nothing to choose from and no form to show. */
export function KindsUnavailable({ error }: { error: ApiError }) {
  return (
    <Panel title="Can’t list what’s importable">
      <p>{error.detail ?? error.message}</p>
      <p>
        The importer reads its kinds from <Command>GET /import/kinds</Command> rather than
        hardcoding them, so without that there is nothing safe to offer.
      </p>
    </Panel>
  );
}
