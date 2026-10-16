import type { Acquisitions, PlanAcquisitions } from "@/lib/api";
import { meter } from "@/lib/plan";

/**
 * Adds used + planned against the matchup's limit: `1 used + 3 planned · 4/7`. Over the limit
 * it turns red AND says "over limit", so the state never rests on colour alone. The small
 * label beside it says where `used` comes from: ESPN's count, or an override set on the Plan
 * tab.
 *
 * `compact` (the Actual view) keeps the old `2 / 7 this matchup` while nothing is planned.
 */
export function AcquisitionMeter({
  acquisitions,
  compact = false,
}: {
  acquisitions: Acquisitions | PlanAcquisitions;
  compact?: boolean;
}) {
  const reading = meter(acquisitions, compact);
  const short = compact && (acquisitions.planned ?? 0) === 0;
  return (
    <span className="flex flex-wrap items-baseline gap-x-1.5 text-xs tabular-nums">
      <span
        data-acquisitions
        data-over={reading.over ? "true" : undefined}
        className={reading.over ? "font-semibold text-rose-700 dark:text-rose-300" : "text-zinc-500"}
      >
        {short ? `Acquisitions ${reading.text} this matchup` : `Acquisitions ${reading.text}`}
      </span>
      {reading.over ? (
        <span className="text-[10px] font-semibold text-rose-700 uppercase dark:text-rose-300">
          over limit
        </span>
      ) : null}
      <span data-acquisitions-source={reading.source} className="text-[10px] text-zinc-400">
        ({reading.sourceLabel})
      </span>
    </span>
  );
}
