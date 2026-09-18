/**
 * Consensus-board vocabulary, shared by the source panel, the table and the controls.
 *
 * The same rule as lib/board.ts: three components have to agree about what a method is
 * called, which number a cell shows, and when two sources count as disagreeing — so they all
 * read it from here rather than each deciding.
 */

import type { ConsensusCell, ConsensusMethod, ConsensusRow, SourceInfo } from "@/lib/api";

export const METHOD_LABEL: Record<ConsensusMethod, string> = {
  rank: "Rank",
  percentile: "Percentile",
};

export const METHOD_HINT: Record<ConsensusMethod, string> = {
  rank: "Every cell is the place that source puts him; the consensus averages those places, lowest first.",
  percentile:
    "Every cell is that place as a position in the shared draftable pool, 100 best; the consensus averages those.",
};

/** The three storage shapes, in words — app/ranking/sources.py: KINDS. */
export const SOURCE_KIND_LABEL: Record<string, string> = {
  projection: "Projection",
  adp: "Market",
  ranking: "Imported board",
};

export function sourceKindLabel(kind: string): string {
  return SOURCE_KIND_LABEL[kind] ?? kind;
}

/** What a source is, in one line: who published it, for when, and how deep it goes. */
export function sourceHint(source: SourceInfo): string {
  const parts = [sourceKindLabel(source.kind), source.source];
  if (source.season !== null) parts.push(String(source.season));
  if (source.horizon) parts.push(`tagged ${source.horizon}`);
  return `${parts.join(" · ")} — ranks ${source.player_count} players`;
}

/** The number a cell shows under the selected method. */
export function cellValue(cell: ConsensusCell, method: ConsensusMethod): number {
  return method === "percentile" ? cell.percentile : cell.rank;
}

/** The spread in the selected method's own units, so the cell matches the columns above it. */
export function spreadFor(row: ConsensusRow, method: ConsensusMethod): number | null {
  return method === "percentile" ? row.spread : row.rank_spread;
}

/**
 * How much the sources disagree about one player, on a scale that means the same thing at
 * the top of the board and at the bottom of it.
 *
 * NOT the raw spread, deliberately. The backend's `spread`/`rank_spread` are the honest
 * numbers and they are what the cell prints — but they are absolute, and absolute spread is
 * dominated by the deep end: ESPN's ADP ranking a fringe player 1,090th while a projection
 * has him 200th is a 890-place spread and nothing to act on, while Giannis going 2nd on the
 * projection and 17th on a dynasty board is 15 places and the most interesting row on the
 * page. The ratio between the best and worst rank is scale-free, so both land where they
 * belong — and it is independent of the rank/percentile toggle, which is right: how much two
 * people disagree is not a property of how you chose to average them.
 */
export function disagreementRatio(row: ConsensusRow): number | null {
  const ranks = Object.values(row.cells).map((cell) => cell.rank);
  if (ranks.length < 2) return null;
  const best = Math.min(...ranks);
  return best <= 0 ? null : Math.max(...ranks) / best;
}

export type DisagreementLevel = "agree" | "slight" | "notable" | "severe";

/**
 * Four bands, sequential rather than red/green.
 *
 * A single amber hue deepening with disagreement reads for every form of colour vision,
 * including total colour blindness, because the channel doing the work is LIGHTNESS. Colour
 * is never the only signal either: the band has a name in the cell's tooltip and the number
 * is printed beside it.
 */
export const DISAGREEMENT_BANDS: {
  level: DisagreementLevel;
  label: string;
  /** Lowest ratio that lands in this band. */
  from: number;
  className: string;
}[] = [
  {
    level: "agree",
    label: "Sources agree",
    from: 0,
    className: "text-zinc-500",
  },
  {
    level: "slight",
    label: "Slight disagreement",
    from: 1.5,
    className: "bg-amber-100/70 text-amber-900 dark:bg-amber-500/15 dark:text-amber-200",
  },
  {
    level: "notable",
    label: "Notable disagreement",
    from: 3,
    className: "bg-amber-300/70 text-amber-950 dark:bg-amber-500/35 dark:text-amber-100",
  },
  {
    level: "severe",
    label: "Severe disagreement — the rows worth staring at",
    from: 6,
    className:
      "bg-amber-500/80 font-semibold text-amber-950 dark:bg-amber-400/55 dark:text-amber-50",
  },
];

export function disagreementBand(ratio: number | null) {
  if (ratio === null) return null;
  return [...DISAGREEMENT_BANDS].reverse().find((band) => ratio >= band.from) ?? null;
}
