import type { PlanFlag, PlanMove, PlanWarning, PlannedDrop } from "@/lib/api";
import { withAlternatives } from "@/lib/plan";
import { dayLabel } from "@/lib/team";
import { Badge } from "./TeamStates";

/**
 * The Plan view's additions to the roster: the tag on a planned (not yet real) pickup, the
 * strip of players the plan drops that day, and the day's plan warnings.
 *
 * A planned pickup is marked in words ("Planned · Wed 10/21") as well as by the row's tint,
 * so he can't be mistaken for someone already on the roster by a reader who can't see the
 * colour.
 */

/** Under a planned pickup's name: the tag, his backups, a pending claim. */
export function PlannedTag({ flag, move }: { flag: PlanFlag; move: PlanMove | null }) {
  return (
    <span className="flex flex-col gap-0.5" data-planned-tag>
      <span className="flex flex-wrap items-center gap-1">
        <Badge tone="sky" data-plan-status="planned">
          {move ? `Planned · ${dayLabel(move.date)}` : "Planned"}
        </Badge>
        {flag.claim_pending ? (
          <Badge tone="amber" data-claim-pending="true">
            Claim pending
          </Badge>
        ) : null}
      </span>
      {flag.alternatives.length > 0 ? (
        <span className="text-[11px] text-zinc-500" data-plan-alternatives>
          or {flag.alternatives.map((p) => p.name).join(", ")}
        </span>
      ) : null}
    </span>
  );
}

/** The day's plan warnings, above the grid: warnings amber, info muted. They block nothing. */
export function DayPlanWarnings({ warnings }: { warnings: readonly PlanWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1" data-day-plan-warnings>
      {warnings.map((warning, index) => (
        <PlanWarningLine key={`${warning.code}-${warning.move_id ?? "x"}-${index}`} warning={warning} />
      ))}
    </ul>
  );
}

export function PlanWarningLine({ warning }: { warning: PlanWarning }) {
  const tone =
    warning.severity === "warning"
      ? "bg-amber-50 text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"
      : "bg-zinc-50 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400";
  return (
    <li
      data-plan-warning={warning.code}
      data-severity={warning.severity}
      className={`rounded-md px-2.5 py-1 text-xs ${tone}`}
    >
      <span className="font-semibold">{warning.severity === "warning" ? "Warning" : "Note"}:</span>{" "}
      {warning.message}
    </li>
  );
}

/** Players leaving the roster that day, under the grid. */
export function DroppedTodayStrip({ drops }: { drops: readonly PlannedDrop[] }) {
  if (drops.length === 0) return null;
  return (
    <div
      data-dropped-today
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-dashed border-zinc-300 px-2.5 py-1.5 text-xs dark:border-zinc-700"
    >
      <span className="text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">
        Dropped today
      </span>
      {drops.map((drop) => (
        <span key={`${drop.move_id}-${drop.player.id}`} data-dropped-player={drop.player.id} className="text-zinc-700 line-through decoration-zinc-400 dark:text-zinc-300">
          {withAlternatives(drop.player.name, drop.alternatives)}
        </span>
      ))}
    </div>
  );
}
