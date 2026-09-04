/**
 * The importer's vocabulary and the pure half of its form logic.
 *
 * The page is a form over `POST /import/{kind}`, and the only genuinely tricky part is what
 * ends up in `options`: a per-kind mapping the backend refuses to guess at. `projection`
 * reads `basis`, `ranking` reads `name` and `horizon`, and `adp` reads nothing — send a kind
 * an option it doesn't know and the import is a 422, deliberately, so that lives here as one
 * function rather than in three branches of JSX.
 *
 * Everything in this file is pure and framework-free, which is what makes the awkward cases
 * (a season typed as "20", a horizon left unchosen) testable without rendering anything.
 */

import type { ImportRequestBody, ProjectionBasis, RankingHorizon } from "@/lib/api";

/** The kinds that take options, spelled out so the form doesn't guess from the kind name. */
export const KIND_ADP = "adp";
export const KIND_PROJECTION = "projection";
export const KIND_RANKING = "ranking";

/** What each kind is, in the one line the picker shows under its name. */
export const KIND_HINT: Record<string, string> = {
  [KIND_ADP]: "Where the room is drafting them. One number per player, per source, per season.",
  [KIND_PROJECTION]:
    "Someone's stat line per player, priced under our scoring rules. Needs a league sync first.",
  [KIND_RANKING]:
    "An ordered board. Re-importing REPLACES the set it names — players who fell off are gone.",
};

/** The two horizons a rank-only list can declare, and why it has to. */
export const RANKING_HORIZON_LABEL: Record<RankingHorizon, string> = {
  dynasty: "Dynasty",
  redraft: "Redraft",
};

export const RANKING_HORIZON_HINT =
  "A ranking carries no stats to age-adjust, so it has to say which question it answers. " +
  "It is part of the set's identity too: one source can publish both boards under one name.";

export const BASIS_LABEL: Record<ProjectionBasis, string> = {
  per_game: "Per game",
  season: "Season totals",
};

export const BASIS_HINT =
  "Are the stat columns per-game averages (the usual export) or season totals? Read the " +
  "wrong way round, a projection is off by a factor of seventy and still looks plausible.";

/** Tailwind classes per row status. Colour carries the same meaning as the counter above it. */
export const STATUS_STYLE: Record<string, string> = {
  matched:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400",
  review: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400",
  unmatched: "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-400",
  duplicate: "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-400",
  invalid: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400",
};

export const STATUS_HINT: Record<string, string> = {
  matched: "Resolved and accepted. Written on commit.",
  review: "Resolved but unconfirmed, or ambiguous. Not written — pick the right candidate.",
  unmatched: "Nothing in our pool looks like this name. Not written.",
  duplicate: "An earlier row already claimed this player. Not written.",
  invalid: "No value in a column this kind requires. Not written.",
};

/** Everything the form holds. Strings throughout: this is what the inputs actually contain. */
export type ImportForm = {
  kind: string;
  source: string;
  /** Empty means "let the backend use ESPN_SEASON". */
  season: string;
  /** Empty means "sniff it". */
  delimiter: string;
  text: string;
  /** projection only. */
  basis: ProjectionBasis;
  /** ranking only; empty falls back to the source name, which the backend does for us. */
  name: string;
  /** ranking only, and required there — no default, on purpose. */
  horizon: RankingHorizon | "";
};

export const EMPTY_FORM: ImportForm = {
  kind: KIND_ADP,
  source: "",
  season: "",
  delimiter: "",
  text: "",
  basis: "per_game",
  name: "",
  horizon: "",
};

/**
 * The `options` for this kind, or undefined when the kind takes none.
 *
 * Only what was actually asked for, exactly as the CLI does it: an ADP import has no basis
 * and no set name, and sending one would be a 422 rather than a shrug.
 */
export function buildOptions(form: ImportForm): Record<string, string> | undefined {
  if (form.kind === KIND_PROJECTION) return { basis: form.basis };
  if (form.kind === KIND_RANKING) {
    const options: Record<string, string> = {};
    if (form.name.trim()) options.name = form.name.trim();
    if (form.horizon) options.horizon = form.horizon;
    return options;
  }
  return undefined;
}

/** The request body, with the fields the caller left blank omitted rather than sent empty. */
export function buildRequest(form: ImportForm): ImportRequestBody {
  const season = Number(form.season.trim());
  return {
    source: form.source.trim(),
    text: form.text,
    // A season the backend can't read is left out entirely, so ESPN_SEASON applies rather
    // than a NaN going over the wire.
    season: form.season.trim() && Number.isFinite(season) ? season : null,
    delimiter: form.delimiter || null,
    options: buildOptions(form) ?? null,
  };
}

/**
 * Why this form can't be sent yet, or null.
 *
 * Three rules, and no more: everything else the backend judges better than we can (whether
 * the table parses, whether a column was found, whether the scoring rules are loaded), and
 * duplicating that here would mean two answers that drift apart.
 */
export function validate(form: ImportForm): string | null {
  if (!form.text.trim()) return "Paste a table, or drop a CSV in, first.";
  if (!form.source.trim()) return "A source is required — it's how the rows are attributed.";
  if (form.kind === KIND_RANKING && !form.horizon) {
    return "A ranking has to say whether it is a dynasty or a redraft board.";
  }
  return null;
}

/**
 * Identifies the request a preview answers, so a preview of the *previous* form can't be
 * committed. Same trick as the board's `controlsKey`, for a much more expensive mistake:
 * a ranking commit replaces a whole set.
 */
export function formKey(form: ImportForm): string {
  const request = buildRequest(form);
  return JSON.stringify([form.kind, request]);
}
