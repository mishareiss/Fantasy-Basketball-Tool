"use client";

import { Command, Panel } from "@/components/Panel";
import { API_BASE_URL, ApiError } from "@/lib/api";

/**
 * What the Team tab shows instead of a roster: loading, the 409 that means "nothing synced
 * yet", and every other failure.
 *
 * The 409 is the interesting one and it is NOT a broken page. The Team API answers 409 until a
 * league sync has stored a team marked as ours, so it gets advice (run the sync) rather than
 * an error panel.
 */

export function TeamLoading({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-team-state="loading"
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

export function NoTeamSynced({ error }: { error: ApiError }) {
  return (
    <div data-team-state="no-team">
      <Panel title="No team synced yet">
        <p>
          The Team tab needs a league sync that has stored the rosters and marked one team as
          ours. Run <Command>make sync</Command>, then reload this page. If the sync can’t tell
          which team is ours, set <Command>ESPN_MY_TEAM_ID</Command> in the backend’s env.
        </p>
        {error.detail ? <p className="font-mono text-xs text-zinc-500">{error.detail}</p> : null}
      </Panel>
    </div>
  );
}

export function TeamFailed({ error }: { error: ApiError }) {
  const unreachable = error.status === undefined;
  return (
    <div role="alert" data-team-state="error">
      <Panel title={unreachable ? "Can’t reach the API" : "The Team tab couldn’t load"}>
        {unreachable ? (
          <p>
            Nothing answered at <Command>{API_BASE_URL}</Command>. Start it with{" "}
            <Command>make backend</Command>, then try again.
          </p>
        ) : (
          <p className="font-medium text-zinc-800 dark:text-zinc-200">
            {error.detail ?? error.message}
          </p>
        )}
        <p className="font-mono text-xs text-zinc-500">{error.message}</p>
      </Panel>
    </div>
  );
}

/** Small inline badges, shared by the roster, the waiver list and the card. */
export function Badge({
  tone,
  children,
  ...rest
}: {
  tone: "rose" | "amber" | "sky" | "zinc";
  children: React.ReactNode;
} & Record<`data-${string}`, string | undefined>) {
  const tones = {
    rose: "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200",
    amber: "bg-amber-50 text-amber-900 dark:bg-amber-500/10 dark:text-amber-200",
    sky: "bg-sky-50 text-sky-800 dark:bg-sky-500/10 dark:text-sky-200",
    zinc: "bg-zinc-100 text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300",
  };
  return (
    <span
      {...rest}
      className={`inline-flex items-center rounded px-1.5 py-px text-[10px] font-semibold tracking-wide whitespace-nowrap ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
