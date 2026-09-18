/**
 * The market page's vocabulary and the pure half of its form logic.
 *
 * Two things here are worth more than the ten lines they cost:
 *
 * * **Odds are text, not numbers.** A book writes `-115` and `+105`, and the plus sign is
 *   information — it is what says "underdog" at a glance. `parseOdds` reads either, and an
 *   empty box is a deliberate `null` (no price) rather than a zero, which is the distinction
 *   `app.ranking.market.implied_probability` is built on: an unpriced line derives itself.
 * * **A name is resolved by the IMPORTER, not here.** `resolveText` builds the one-row table
 *   that `POST /import/market_line` (dry run) parses and matches, so adding a line by hand
 *   and pasting a file go through exactly one name matcher, one candidate list and one alias
 *   fix. A second matcher in the browser would be a second set of answers.
 */

import type { MarketLineRow, MarketPlayer } from "@/lib/api";

/** The default book, and therefore `projection:market` on the consensus board. */
export const MARKET_SOURCE = "market";

/** Tab-separated, always: a player name can contain a comma ("Gilgeous-Alexander, Shai"). */
export const RESOLVE_DELIMITER = "\t";

/** What the add form holds. Strings throughout — it is what the inputs contain. */
export type AddForm = {
  /** The player's name as anyone writes it; the matcher does the rest. */
  name: string;
  /** A stat NAME ('PTS'), from the league's scored stats. */
  stat: string;
  line: string;
  over: string;
  under: string;
};

export const EMPTY_ADD: AddForm = { name: "", stat: "", line: "", over: "", under: "" };

/**
 * American odds as typed -> a number, or null for "no price".
 *
 * `undefined` means "that isn't odds" — which the form refuses rather than sending, because
 * a NaN over the wire would come back as a 422 about a field nobody typed wrong on purpose.
 */
export function parseOdds(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (!/^[+-]?\d+$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  // A literal 0 is not a price anyone writes; the backend reads it as absent, so say so here.
  return value === 0 ? null : value;
}

/** A stored price for display: `-115`, `+105`, or an em dash for a side nobody priced. */
export function formatOdds(odds: number | null): string {
  if (odds === null || odds === undefined) return "—";
  return odds > 0 ? `+${odds}` : String(odds);
}

/** What the odds input should start at, so editing one side doesn't blank the other. */
export function oddsInput(odds: number | null): string {
  return odds === null || odds === undefined ? "" : String(odds);
}

/**
 * The one-row table the importer's preview resolves the name against.
 *
 * A header line, because that is what the parser reads first and finds its columns by — the
 * aliases used here are `market_line`'s own (`app/ingest/market_line.py`).
 */
export function resolveText(form: AddForm): string {
  const cells = [form.name.trim(), form.stat, form.line.trim(), form.over.trim(), form.under.trim()];
  return [
    ["player", "stat", "line", "over odds", "under odds"].join(RESOLVE_DELIMITER),
    cells.join(RESOLVE_DELIMITER),
  ].join("\n");
}

/** Why this line can't be sent yet, or null. */
export function validateAdd(form: AddForm): string | null {
  if (!form.name.trim()) return "Who is the line on?";
  if (!form.stat) return "Pick the stat the prop is on.";
  if (!form.line.trim() || !Number.isFinite(Number(form.line))) {
    return "The line itself has to be a number — 24.5, not “o24.5”.";
  }
  if (parseOdds(form.over) === undefined || parseOdds(form.under) === undefined) {
    return "Odds are American and whole: -115, +105, or left empty for an unpriced side.";
  }
  return null;
}

/** Why an edited line can't be saved yet, or null. The same rules, minus the name. */
export function validateEdit(line: string, over: string, under: string): string | null {
  if (!line.trim() || !Number.isFinite(Number(line))) return "The line has to be a number.";
  if (parseOdds(over) === undefined || parseOdds(under) === undefined) {
    return "Odds are American and whole: -115, +105, or empty.";
  }
  return null;
}

/**
 * How partial this player's market value is, in words.
 *
 * Always said, never only when it looks wrong: a market projection is built from the stats
 * that have lines and NOTHING else, so a player priced on points alone is ranked on points
 * alone. That is the honest reading of what a book published, and the number is unreadable
 * without it.
 */
export function partialNote(player: MarketPlayer): string {
  const stats = player.stats_priced;
  return `${stats} stat${stats === 1 ? "" : "s"} priced`;
}

/** The stats this player already has a line on — so the add form can say "moves it" instead. */
export function pricedStats(player: MarketPlayer | undefined): Set<string> {
  return new Set((player?.lines ?? []).map((line: MarketLineRow) => line.stat));
}
