import type { StartsResponse } from "@/lib/api";
import { startsDelta, startsSummary } from "@/lib/team";

/**
 * The matchup's starts in one line, above the roster: `Starts 38/49 · 6 open · 1 benched ·
 * Proj 2,140 pts (as set 2,095)`. In the Plan view it adds the plan's gain over Actual
 * (`+5 starts, +412 pts vs Actual`): the number that says whether a streaming plan is worth
 * its adds.
 */
export function StartsSummary({ starts }: { starts: StartsResponse }) {
  const { totals, vs_actual: delta } = starts;
  const gain = delta ? delta.possible > 0 || delta.projected_points > 0 : false;
  const loss = delta ? delta.possible < 0 || delta.projected_points < 0 : false;
  return (
    <p
      data-starts-summary
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-md bg-zinc-50 px-3 py-1.5 text-xs text-zinc-700 tabular-nums dark:bg-zinc-900 dark:text-zinc-300"
      title={`${starts.matchup.label}: starts filled by the lineups as set, out of ${totals.days} days × ${totals.max / Math.max(totals.days, 1)} slots. "Open": nobody on the roster can fill them. "Benched": the lineup as set sits players who could start. Proj: the best lineup's FPTS/G summed.`}
    >
      <span className="font-medium text-zinc-900 dark:text-zinc-100">{startsSummary(totals)}</span>
      {delta ? (
        <span
          data-starts-delta
          title="The best lineup each day, plan roster against actual roster"
          className={`font-semibold ${
            gain && !loss
              ? "text-emerald-700 dark:text-emerald-300"
              : loss && !gain
                ? "text-rose-700 dark:text-rose-300"
                : "text-zinc-600 dark:text-zinc-400"
          }`}
        >
          {startsDelta(delta)}
        </span>
      ) : null}
    </p>
  );
}
