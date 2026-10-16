"use client";

import { Segment, Segmented } from "@/components/board/BoardControls";
import { FIELD } from "@/components/draft/DraftStates";
import type { TeamBasis } from "@/lib/api";
import { ACTUAL_SPLITS } from "@/lib/team";

/**
 * `Projected | Actual`, and under Actual the split: Season, L7, L15, L30. One control, used by
 * the roster and the waiver list, each with its own value.
 *
 * Actual is disabled until this season has a single actual line, because before that every
 * split is empty and choosing one would only blank the stat columns.
 */
export function BasisToggle({
  label,
  basis,
  actualsAvailable,
  onChange,
}: {
  label: string;
  basis: TeamBasis;
  actualsAvailable: boolean;
  onChange: (basis: TeamBasis) => void;
}) {
  const actual = basis !== "projected";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented label={label}>
        <Segment active={!actual} onClick={() => onChange("projected")}>
          Projected
        </Segment>
        <Segment
          active={actual}
          disabled={!actualsAvailable}
          title={actualsAvailable ? undefined : "Available once games have been played"}
          onClick={() => {
            if (!actual) onChange("season");
          }}
        >
          Actual
        </Segment>
      </Segmented>
      {actual ? (
        <select
          aria-label={`${label} split`}
          className={`${FIELD} py-1 text-xs`}
          value={basis}
          onChange={(event) => onChange(event.target.value as TeamBasis)}
        >
          {ACTUAL_SPLITS.map((split) => (
            <option key={split.basis} value={split.basis}>
              {split.label}
            </option>
          ))}
        </select>
      ) : null}
      {actualsAvailable ? null : (
        <span className="text-[11px] text-zinc-500">Available once games have been played</span>
      )}
    </div>
  );
}
