/**
 * The vocabulary of our own board: how a move is applied, how the gap to the field is read,
 * and how deep the page renders.
 *
 * All of it is pure, and that is the point. The page's one hard requirement is that a reorder
 * persists as the WHOLE order — so the array these functions return is both what is drawn and
 * what is sent — and a permutation is much easier to trust when the function producing it can
 * be tested without a DOM.
 */

import {
  MASTER_TAGS,
  SCOPE_OVERALL,
  type MasterPlayerRow,
  type MasterTag,
  type Position,
  type TierScope,
  type TierScopeRow,
} from "@/lib/api";

/**
 * How many rows the board draws before you ask for more.
 *
 * The board is ~1000 deep and a draft is fought over the top of it. Rendering all of it costs
 * a second of layout every time a rank changes, so the list is a window with a "show more"
 * under it, and SEARCH — not scrolling — is how you reach the 700th man. The full order still
 * lives in state either way: what is windowed is the DOM, never the thing that gets saved.
 */
export const PAGE = 175;

export const TAG_LABEL: Record<MasterTag, string> = {
  target: "Target",
  fade: "Fade",
};

/**
 * The two tags, in the app's own palette rather than a red/green pair: sky for a player we
 * are trying to end up with, amber for one we would rather the room took. Both carry their
 * word, so the colour is never the only thing saying which is which.
 */
export const TAG_CLASS: Record<MasterTag, string> = {
  target: "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200",
  fade: "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200",
};

/**
 * The tag button's cycle: none -> target -> fade -> none.
 *
 * One control rather than a menu, because tagging happens mid-draft and two of the three
 * states are one click apart. A tag the frontend doesn't know about (one added server-side)
 * cycles to the first known one rather than getting stuck.
 */
export function nextTag(current: string | null): MasterTag | null {
  const index = MASTER_TAGS.findIndex((candidate) => candidate === current);
  if (index === -1) return MASTER_TAGS[0];
  return index + 1 < MASTER_TAGS.length ? MASTER_TAGS[index + 1] : null;
}

export type TagStyle = { label: string; className: string };

/**
 * How a stored tag renders, or null for a player with none.
 *
 * `tag` arrives as a bare `str` (the backend's own type), so this is also the narrowing: a
 * tag this build doesn't know about renders as no tag rather than as a crash.
 */
export function tagStyle(tag: string | null): TagStyle | null {
  const known = MASTER_TAGS.find((candidate) => candidate === tag);
  return known ? { label: TAG_LABEL[known], className: TAG_CLASS[known] } : null;
}

/**
 * Renumber an order so every rank is its place in it, 1-based.
 *
 * The same arithmetic `PUT /master/order` applies server-side, done locally so the optimistic
 * board shows the ranks the save is about to make true. Rows whose rank already matches are
 * returned by identity, which is what lets the memoised rows skip a re-render on a move that
 * only disturbed the top of the board.
 */
export function renumber(rows: MasterPlayerRow[]): MasterPlayerRow[] {
  return rows.map((row, index) => (row.rank === index + 1 ? row : { ...row, rank: index + 1 }));
}

/**
 * Move one player to another place in the FULL order.
 *
 * Indices are always into the whole board, never into the window or the search results — a
 * drag from a filtered list has to land where the player actually is, and "move to #40" means
 * the 40th player on the board whether or not the 40th player is on screen. Out-of-range
 * targets clamp rather than throw: typing 9999 into "move to" means "last", which is what
 * anyone typing it meant.
 */
export function moveTo(
  rows: MasterPlayerRow[],
  from: number,
  to: number,
): MasterPlayerRow[] {
  if (from < 0 || from >= rows.length) return rows;
  const target = Math.max(0, Math.min(rows.length - 1, to));
  if (target === from) return rows;

  const next = [...rows];
  const [moved] = next.splice(from, 1);
  next.splice(target, 0, moved);
  return renumber(next);
}

/**
 * How many spots ABOVE the field we have him — the number the board is actually for.
 *
 * The backend's `delta` is `rank - consensus_rank`, so a player we rate higher than the room
 * carries a negative one (our 1 against their 4 is -3). That sign reads backwards on a page
 * where the interesting direction is "how far out on a limb am I", so it is flipped here,
 * once, and the flip lives in exactly this function: positive means higher than the field.
 *
 * Null when either half is missing — a stale player has no field opinion to be out on.
 */
export function edge(row: MasterPlayerRow): number | null {
  return row.delta === null ? null : -row.delta;
}

/** Positive/negative/level, for a chip that must not rely on its colour alone. */
export type EdgeTone = "above" | "below" | "level";

export function edgeTone(value: number): EdgeTone {
  if (value > 0) return "above";
  if (value < 0) return "below";
  return "level";
}

export const EDGE_GLYPH: Record<EdgeTone, string> = {
  above: "▲",
  below: "▼",
  level: "=",
};

export const EDGE_CLASS: Record<EdgeTone, string> = {
  above: "bg-emerald-100 text-emerald-900 dark:bg-emerald-500/20 dark:text-emerald-200",
  below: "bg-rose-100 text-rose-900 dark:bg-rose-500/20 dark:text-rose-200",
  level: "text-zinc-500",
};

/** "+10 spots above the field", spelled out for the chip's tooltip and its screen-reader text. */
export function edgeDescription(row: MasterPlayerRow): string {
  const value = edge(row);
  if (value === null || row.rank === null || row.consensus_rank === null) {
    return "No source ranks him, so there is nothing to compare your place to";
  }
  if (value === 0) return `You and the field both have him at ${row.rank}`;
  const direction = value > 0 ? "higher" : "lower";
  return (
    `You have him at ${row.rank}, the field at ${row.consensus_rank} — ` +
    `${Math.abs(value)} ${Math.abs(value) === 1 ? "spot" : "spots"} ${direction}`
  );
}

/**
 * Does this player answer the search box?
 *
 * Name or team, case- and accent-insensitive, so "jokic" finds Jokić and "MIL" finds the
 * Bucks. Deliberately a substring rather than the fuzzy matcher the importer uses: this is
 * navigation on a list you already own, not resolution of a name someone else wrote.
 */
export function matches(row: MasterPlayerRow, term: string): boolean {
  const needle = normalize(term);
  if (!needle) return true;
  return (
    normalize(row.name).includes(needle) || normalize(row.nba_team ?? "").includes(needle)
  );
}

function normalize(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

/* ---------------------------------------------------------------------------------------- *
 * Tiers: bands over the order, stored as the rank each one STARTS at.
 *
 * `cut_ranks` is the whole vocabulary — `[1, 4, 12]` is three tiers (1-3, 4-11, 12-N) — and
 * every edit below is a list-to-list function over it, for two reasons. One: `PUT
 * /master/tiers` takes the complete list back, exactly like a reorder takes the complete
 * order, so what is drawn and what is sent are the same array. Two: the backend 422s a list
 * that is unsorted, duplicated, out of range or missing its leading 1, and the honest way to
 * make sure that never fires is for the client to be incapable of building one — hence
 * `normalizeCuts`, which every one of these ends in.
 *
 * Cut ranks are always within ONE SCOPE. On the whole board they are board ranks; under a
 * position filter they count that position, so the 3rd point guard is rank 3 there whatever
 * his board rank is. The page's position filter and its tier scope are therefore the same
 * choice, which is why `activeScope` exists rather than each caller doing the `?? "overall"`.
 * ---------------------------------------------------------------------------------------- */

/** The scope a position filter is looking at — "All" is the whole board's own scope. */
export function activeScope(position: Position | null): TierScope {
  return position ?? SCOPE_OVERALL;
}

/** One scope's row out of the response's full set, or null if the board didn't carry it. */
export function scopeTiers(tiers: TierScopeRow[], scope: TierScope): TierScopeRow | null {
  return tiers.find((row) => row.scope === scope) ?? null;
}

/**
 * Force any list of cut ranks into one the backend will accept.
 *
 * Sorted, de-duplicated, dropped if outside 1..size, and always led by 1 — a board's first
 * tier starts at the top whether or not the edit that produced this remembered to say so.
 * A scope with nobody in it has no bands at all, which is the empty list rather than `[1]`.
 */
export function normalizeCuts(cuts: number[], size: number): number[] {
  if (size <= 0) return [];
  const inside = cuts
    .map((rank) => Math.round(rank))
    .filter((rank) => Number.isFinite(rank) && rank >= 1 && rank <= size);
  return [...new Set([1, ...inside])].sort((a, b) => a - b);
}

/** Which band a rank falls in, 1-based. 0 for a rank no band covers (an empty scope). */
export function tierAt(cuts: number[], rank: number): number {
  let tier = 0;
  for (const cut of cuts) {
    if (cut > rank) break;
    tier += 1;
  }
  return tier;
}

export type TierBand = { tier: number; start: number; end: number };

/** Every band this scope is cut into, as the ranks it covers — what a divider labels. */
export function tierBands(cuts: number[], size: number): TierBand[] {
  const starts = normalizeCuts(cuts, size);
  return starts.map((start, index) => ({
    tier: index + 1,
    start,
    end: index + 1 < starts.length ? starts[index + 1] - 1 : size,
  }));
}

/**
 * Start a new tier at `rank` — the "+ tier break here" between two rows.
 *
 * A break at rank 1 is a no-op rather than an error: tier 1 already starts there, and the
 * affordance above the first row would otherwise write a list identical to the stored one.
 */
export function addCut(cuts: number[], rank: number, size: number): number[] {
  return normalizeCuts([...cuts, rank], size);
}

/**
 * Merge a tier into the one above it — the × on a divider.
 *
 * Removing the leading 1 is refused (it would describe a board whose first tier is tier 2),
 * so the top divider has no × on it and this is the second line of that defence.
 */
export function removeCut(cuts: number[], rank: number, size: number): number[] {
  if (rank <= 1) return normalizeCuts(cuts, size);
  return normalizeCuts(
    cuts.filter((cut) => cut !== rank),
    size,
  );
}

/**
 * Drag or nudge one divider to another gap.
 *
 * Clamped to 2..size, because rank 1 is not a gap — it is the top of the board, where tier 1
 * starts and no divider can be moved on top of or away from. Landing on a rank that already
 * carries a divider collapses to a no-op (the de-dupe in `normalizeCuts` would otherwise
 * quietly delete one of the two).
 */
export function moveCut(cuts: number[], from: number, to: number, size: number): number[] {
  if (from <= 1 || size <= 1) return normalizeCuts(cuts, size);
  const target = Math.max(2, Math.min(size, Math.round(to)));
  if (target === from) return normalizeCuts(cuts, size);
  const rest = cuts.filter((cut) => cut !== from);
  if (rest.includes(target)) return normalizeCuts(cuts, size);
  return normalizeCuts([...rest, target], size);
}

/** Where a divider can be nudged to next, or null when that direction is against the wall. */
export function nudgedCut(cuts: number[], rank: number, size: number, step: -1 | 1): number | null {
  const target = rank + step;
  if (target < 2 || target > size) return null;
  if (cuts.includes(target)) return null;
  return target;
}

/** How a scope names itself in a sentence: "the board" or "point guards". */
export const SCOPE_LABEL: Record<string, string> = {
  overall: "the whole board",
  PG: "point guards",
  SG: "shooting guards",
  SF: "small forwards",
  PF: "power forwards",
  C: "centres",
};

export function scopeLabel(scope: TierScope): string {
  return SCOPE_LABEL[scope] ?? scope;
}

/**
 * The tier a row prints: his band in the scope on screen.
 *
 * Derived from the ACTIVE scope's cut ranks and his place in the shown order rather than read
 * off `overall_tier` / `position_tier`, and the difference matters in exactly one moment —
 * the beat between an optimistic move and the board that answers it. Dragging a player up
 * across a line has to re-tier him immediately and with NOTHING written (the bands didn't
 * move, he did), and a pill fed from the last response would lag the divider directly above
 * it by a round trip. The server computes the same arithmetic over the same cuts, so the two
 * agree the rest of the time; what this guarantees is that the pill and the divider on screen
 * can never disagree.
 */
export function shownTier(cuts: number[], scopeRank: number): number | null {
  const tier = tierAt(cuts, scopeRank);
  return tier === 0 ? null : tier;
}
