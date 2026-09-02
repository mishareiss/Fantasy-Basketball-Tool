"use client";

import type { BoardResponse } from "@/lib/api";
import { HORIZON_HINT, HORIZON_LABEL } from "@/lib/board";
import { isoDate } from "@/lib/format";

/**
 * What this board is built from, in one line.
 *
 * Every number below is only true relative to a source, a season, and a date the ages were
 * computed at. Printing them beside the board is what stops a stale sync from looking like
 * a fresh opinion.
 */

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex flex-col gap-0.5" title={title}>
      <span className="text-[10px] tracking-wide text-zinc-400 uppercase dark:text-zinc-600">
        {label}
      </span>
      <span className="font-mono text-xs text-zinc-700 dark:text-zinc-300">{value}</span>
    </div>
  );
}

export function BoardMeta({ response }: { response: BoardResponse }) {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900/40">
      <Fact
        label="Projections"
        value={`${response.source} ${response.season}`}
        title={`kind: ${response.kind}`}
      />
      <Fact
        label="Ranked by"
        value={HORIZON_LABEL[response.horizon]}
        title={HORIZON_HINT[response.horizon]}
      />
      <Fact
        label="ADP"
        value={`${response.adp_source}${response.adp_season ? ` ${response.adp_season}` : " —"}`}
        title="Whose redraft market is in the ADP column"
      />
      <Fact
        label="Ages as of"
        value={isoDate(response.age_as_of)}
        title="Every age on this board is a whole-year age at this date"
      />
      <Fact
        label="Ranked"
        value={`${response.players.length} of ${response.total_ranked}`}
        title="Rows shown of players matching this filter"
      />
      <Fact
        label="Tiered"
        value={
          response.tiers === "auto"
            ? `${response.tier_summary.length} tiers · top ${response.tier_pool}`
            : "off"
        }
        title="Tiers are cut from the overall ranking, before any position filter"
      />
    </div>
  );
}
