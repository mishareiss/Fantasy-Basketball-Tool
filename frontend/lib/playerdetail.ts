/**
 * Reading a stat line: which stats a box score shows, in what order, and the two rates that
 * are arithmetic rather than data.
 *
 * Pure, and separate from the dialog that draws it, for the reason every `lib/` module here is
 * separate: the interesting claims — a missing stat stays missing, a percentage only exists
 * when both halves of it do — are assertions about numbers, not about markup.
 *
 * THE ONE RULE. `undefined` in, `null` out, all the way through. The backend omits a stat it
 * has no number for (`app/api/players.py`), and every function here preserves that rather than
 * substituting a zero: on a page whose whole job is to show somebody what a player actually
 * did, a 0 he didn't earn is worse than a blank.
 */

import type { SeasonLine } from "@/lib/api";

/**
 * The box score, in the order it reads.
 *
 * Minutes first because everything else is a function of them, then the five counting
 * categories a fantasy manager scans for, then the shooting pairs, then games played — which
 * is last because it is the denominator rather than a performance.
 *
 * `key` is the stat NAME the backend keys `per_game` by (ESPN's vocabulary, so turnovers are
 * 'TO' and threes are '3PM'). `label` is what the tile says.
 */
export const BOX_SCORE: { key: string; label: string; digits?: number }[] = [
  { key: "MIN", label: "MIN" },
  { key: "PTS", label: "PTS" },
  { key: "REB", label: "REB" },
  { key: "AST", label: "AST" },
  { key: "STL", label: "STL" },
  { key: "BLK", label: "BLK" },
  { key: "TO", label: "TO" },
  { key: "3PM", label: "3PM" },
  { key: "FGM", label: "FGM" },
  { key: "FGA", label: "FGA" },
  { key: "FTM", label: "FTM" },
  { key: "FTA", label: "FTA" },
  // A whole number wherever it lands, because it is a count of games and not a rate.
  { key: "GP", label: "GP", digits: 0 },
];

/** One stat's value, or null when the source published none. Never 0 for "unknown". */
export function statValue(
  perGame: Record<string, number> | undefined,
  key: string,
): number | null {
  const value = perGame?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * A shooting percentage, derived — makes over attempts, as a percentage.
 *
 * Derived rather than read, because ESPN's percentage stats are exactly the ones the parser
 * throws away before anything is scored (`app/espn/statsplits.py`: a rate cannot be multiplied
 * by a coefficient), so the only honest way to show FG% is to divide the two counts we DO
 * hold. Null unless both exist and there were attempts: a player with no attempts has no
 * percentage, and 0% would say he missed.
 */
export function shootingPct(
  perGame: Record<string, number> | undefined,
  made: string,
  attempted: string,
): number | null {
  const makes = statValue(perGame, made);
  const attempts = statValue(perGame, attempted);
  if (makes === null || attempts === null || attempts <= 0) return null;
  return (makes / attempts) * 100;
}

/** The two percentages a box score shows, in the order the pairs above appear. */
export function shootingSplits(
  perGame: Record<string, number> | undefined,
): { label: string; value: number | null }[] {
  return [
    { label: "FG%", value: shootingPct(perGame, "FGM", "FGA") },
    { label: "FT%", value: shootingPct(perGame, "FTM", "FTA") },
  ];
}

/**
 * American odds as they are written: +110, -135, or an em dash for a side nobody priced.
 *
 * The sign is the whole content of the number, so a positive price has to carry its `+`
 * explicitly — 110 and +110 are the same odds and only one of them reads as odds.
 */
export function americanOdds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value > 0 ? `+${value}` : String(value);
}

/**
 * "65 GP · 2026" — the line under a season's heading, built only from what is there.
 *
 * A season with no games count still has a season, and saying "— GP" beside it would be
 * noise; the parts that exist are joined and the parts that don't are left out.
 */
export function seasonSummary(season: SeasonLine): string {
  const parts = [String(season.season)];
  if (season.games !== null) parts.push(`${Math.round(season.games)} GP`);
  return parts.join(" · ");
}
