"use client";

import { Segment, Segmented } from "@/components/board/BoardControls";
import type { DayView } from "@/lib/api";

/**
 * `Actual | Plan`: my roster as it is, or as if the waiver plan had run. The page keeps the
 * choice in the URL (`?view=plan`); Actual is the default and is left out of it.
 *
 * The segments carry their own names ("Actual view", "Plan view") because the roster's stat
 * toggle already has an "Actual" button beside this one.
 */
export function ViewToggle({ view, onChange }: { view: DayView; onChange: (view: DayView) => void }) {
  return (
    <Segmented label="View">
      <Segment active={view === "actual"} name="Actual view" onClick={() => onChange("actual")}>
        Actual
      </Segment>
      <Segment active={view === "plan"} name="Plan view" onClick={() => onChange("plan")}>
        Plan
      </Segment>
    </Segmented>
  );
}
