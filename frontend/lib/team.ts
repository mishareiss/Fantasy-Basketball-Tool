/**
 * The Team tab's rules, as functions: which slot a player may move to, the lineup a move
 * sends, how the waiver list's day chips group, and the badge texts.
 *
 * Pure, and separate from the components, for the reason every `lib/` module here is: the
 * claims worth testing (a swap is legal only when BOTH players fit, a move sends every
 * rostered player exactly once) are about data, not markup.
 *
 * THE LEGALITY RULE MIRRORS THE BACKEND (`app/team/grid.py: validate_lineup`). The bench is
 * always allowed; every other slot needs the slot id in the player's `eligible_slots`. IR is
 * no different: ESPN puts IR in everyone's eligible slots, and a healthy player on IR is only
 * a server WARNING, so the page doesn't second-guess it. Room is never an issue here because a
 * move is one-for-one by ROW: a player goes into an empty row, or trades rows with its
 * occupant, so no slot ever holds more players than it has rows.
 */

import type {
  AutoFillResponse,
  CalendarDay,
  CalendarMatchup,
  DayView,
  LineupAssignment,
  LineupSource,
  TeamBasis,
  TeamSlotRow,
} from "@/lib/api";

/** ESPN's slot ids (espn_api POSITION_MAP) for the two slots with special rules. */
export const BENCH_SLOT = 12;
export const IR_SLOT = 13;

/** Healthy, as ESPN spells it (grid.py: HEALTHY_INJURY_STATUSES). */
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

/** May a player with these eligible slots sit in `slotId`? The bench takes anyone. */
export function canPlay(eligibleSlots: readonly number[], slotId: number): boolean {
  return slotId === BENCH_SLOT || eligibleSlots.includes(slotId);
}

/**
 * Is row `to` a legal destination for the player in row `from`?
 *
 * An empty row: the mover has to fit it. An occupied row: a SWAP, so the mover has to fit the
 * target's slot AND the occupant has to fit the mover's. A row in the same slot as the mover's
 * (bench to bench, UT to UT) is never a target: the lineup it would send is the same one.
 */
export function canMoveTo(slots: readonly TeamSlotRow[], from: number, to: number): boolean {
  const source = slots[from];
  const target = slots[to];
  if (!source?.player || !target || from === to) return false;
  if (source.slot_id === target.slot_id) return false;
  if (!canPlay(source.player.eligible_slots, target.slot_id)) return false;
  return target.player === null || canPlay(target.player.eligible_slots, source.slot_id);
}

/** Every row the player in row `from` could move to, by index. */
export function legalTargets(slots: readonly TeamSlotRow[], from: number): Set<number> {
  const out = new Set<number>();
  slots.forEach((_, index) => {
    if (canMoveTo(slots, from, index)) out.add(index);
  });
  return out;
}

/** The whole lineup as it stands: every player in the grid, in his row's slot. */
export function currentAssignments(slots: readonly TeamSlotRow[]): LineupAssignment[] {
  return slots.flatMap((row) =>
    row.player ? [{ player_id: row.player.id, slot_id: row.slot_id }] : [],
  );
}

/**
 * The FULL assignment list after moving row `from`'s player to row `to` (swapping with its
 * occupant, if any). Every player in the grid appears exactly once, which is what
 * `PUT /team/lineup/{sp}` requires: it replaces the day's lineup, it doesn't patch it.
 */
export function buildMove(
  slots: readonly TeamSlotRow[],
  from: number,
  to: number,
): LineupAssignment[] {
  const mover = slots[from]?.player;
  const target = slots[to];
  if (!mover || !target) return currentAssignments(slots);
  const occupant = target.player;
  return slots.flatMap((row, index) => {
    if (!row.player) return [];
    if (index === from) return [{ player_id: mover.id, slot_id: target.slot_id }];
    if (occupant && index === to) return [{ player_id: occupant.id, slot_id: slots[from].slot_id }];
    return [{ player_id: row.player.id, slot_id: row.slot_id }];
  });
}

/* --- dates -------------------------------------------------------------------------- */

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `2026-10-21` as a UTC midnight, so no viewer's time zone slides it a day. */
function parseDay(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

/** `2026-10-21` -> `Wed`. */
export function weekday(iso: string): string {
  return WEEKDAY[parseDay(iso).getUTCDay()] ?? "";
}

/** `2026-10-21` -> `10/21`. */
export function monthDay(iso: string): string {
  const day = parseDay(iso);
  return `${day.getUTCMonth() + 1}/${day.getUTCDate()}`;
}

/** `2026-10-21` -> `Wed 10/21`. */
export function dayLabel(iso: string): string {
  return `${weekday(iso)} ${monthDay(iso)}`;
}

/** A matchup's range as ESPN's schedule page writes it: `Oct 20–25`, `Oct 26–Nov 1`. */
export function matchupRange(start: string, end: string): string {
  const a = parseDay(start);
  const b = parseDay(end);
  const left = `${MONTH[a.getUTCMonth()]} ${a.getUTCDate()}`;
  const right =
    a.getUTCMonth() === b.getUTCMonth()
      ? String(b.getUTCDate())
      : `${MONTH[b.getUTCMonth()]} ${b.getUTCDate()}`;
  return `${left}–${right}`;
}

/* --- the calendar ------------------------------------------------------------------- */

/** The days of one matchup, in order. */
export function matchupDays(days: readonly CalendarDay[], matchupId: number): CalendarDay[] {
  return days.filter((day) => day.matchup_id === matchupId);
}

/** The matchup a scoring period falls in, or null outside the calendar. */
export function matchupOf(
  matchups: readonly CalendarMatchup[],
  sp: number,
): CalendarMatchup | null {
  return matchups.find((m) => sp >= m.first_sp && sp <= m.last_sp) ?? null;
}

export type DayGroup = {
  matchup: CalendarMatchup | null;
  days: CalendarDay[];
};

/**
 * The selected days, sorted and grouped under their matchups, for the waiver list's column
 * headers. A selection may span matchups (switch matchups and keep picking chips), and each
 * run of days keeps its own matchup heading.
 */
export function groupDaysByMatchup(
  selected: readonly number[],
  days: readonly CalendarDay[],
  matchups: readonly CalendarMatchup[],
): DayGroup[] {
  const bySp = new Map(days.map((day) => [day.sp, day]));
  const groups: DayGroup[] = [];
  for (const sp of [...new Set(selected)].sort((a, b) => a - b)) {
    const day = bySp.get(sp);
    if (!day) continue;
    const matchup = matchupOf(matchups, sp);
    const last = groups.at(-1);
    if (last && (last.matchup?.id ?? null) === (matchup?.id ?? null)) last.days.push(day);
    else groups.push({ matchup, days: [day] });
  }
  return groups;
}

/** A scoring period clamped into the season. */
export function clampDay(sp: number, finalPeriod: number): number {
  return Math.min(Math.max(1, Math.round(sp)), finalPeriod);
}

/**
 * The window `POST /sync/lineup` pulls when given no range, mirrored from the backend
 * (`app/team/calendar.py: default_lineup_range`): today's period, clamped into the season,
 * through the last day of the NEXT matchup.
 */
export function defaultSyncWindow(calendar: {
  effective_period: number;
  final_period: number;
  matchups: readonly CalendarMatchup[];
}): { first: number; last: number } {
  const first = calendar.effective_period;
  const current = matchupOf(calendar.matchups, first);
  const index = current ? calendar.matchups.indexOf(current) : -1;
  const next = calendar.matchups[index + 1] ?? current;
  return { first, last: Math.min(next?.last_sp ?? first, calendar.final_period) };
}

/**
 * What to send `POST /sync/lineup` from day `sp`: nothing (the server's default window) when
 * the day is inside it, else the day's own matchup, so the button always refreshes the lineup
 * on screen.
 */
export function syncRange(
  calendar: {
    effective_period: number;
    final_period: number;
    matchups: readonly CalendarMatchup[];
  },
  sp: number,
): { first_sp?: number; last_sp?: number } {
  const { first, last } = defaultSyncWindow(calendar);
  if (sp >= first && sp <= last) return {};
  const matchup = matchupOf(calendar.matchups, sp);
  return matchup ? { first_sp: matchup.first_sp, last_sp: matchup.last_sp } : { first_sp: sp, last_sp: sp };
}

/** Toggle one chip in a selection, keeping it sorted. */
export function toggleDay(selected: readonly number[], sp: number): number[] {
  return selected.includes(sp)
    ? selected.filter((day) => day !== sp)
    : [...selected, sp].sort((a, b) => a - b);
}

/* --- badges ------------------------------------------------------------------------- */

/**
 * The lineup-source badge. `carriedFrom` is the date the carried lineup was stored for; the
 * day response doesn't say which day that was, so without it the badge says so plainly.
 */
export function lineupSourceLabel(source: LineupSource, carriedFrom?: string | null): string {
  switch (source) {
    case "espn":
      return "ESPN lineup";
    case "manual":
      return "Edited here";
    case "carried":
      return carriedFrom ? `Carried from ${dayLabel(carriedFrom)}` : "Carried from an earlier day";
    case "roster":
      return "From roster";
    // The Plan view's sources (task 30).
    case "plan_edit":
      return "Plan: edited here";
    case "plan_carried":
      return carriedFrom
        ? `Plan: carried from ${dayLabel(carriedFrom)}`
        : "Plan: carried from an earlier day";
    case "from_actual":
      return "Plan: from the actual lineup";
  }
}

/**
 * Auto-fill's one-line result: "7 of 7 starters playing. Benched with games: A, B" for the
 * day on screen, "Lineup already optimal" when nothing changed, and how many days were set
 * when it ran through the matchup.
 */
export function autoFillSummary(answer: AutoFillResponse): string {
  if (!answer.changed) return "Lineup already optimal";
  const first = answer.days.find((d) => d.sp === answer.sp) ?? answer.days[0];
  const parts: string[] = [];
  if (first) {
    parts.push(`${first.starters_playing} of ${first.slots} starters playing.`);
    if (first.benched_playing.length > 0)
      parts.push(`Benched with games: ${first.benched_playing.join(", ")}.`);
  }
  if (answer.days_written > 1) parts.push(`Set ${answer.days_written} days.`);
  return parts.join(" ");
}

const INJURY_SHORT: Record<string, string> = {
  OUT: "O",
  DAY_TO_DAY: "DTD",
  QUESTIONABLE: "Q",
  DOUBTFUL: "D",
  PROBABLE: "P",
  SUSPENSION: "SSPD",
  INJURY_RESERVE: "IR",
};

/** The injury badge, or null for a healthy player (null, ACTIVE or NORMAL). */
export function injuryBadge(status: string | null | undefined): string | null {
  if (!status || HEALTHY.has(status)) return null;
  return INJURY_SHORT[status] ?? status.replaceAll("_", " ");
}

/** The waiver badge's text, from the server's `clears_at_display`. */
export function waiverBadge(waiver: {
  clears_at_display: string | null;
  clears_at_known: boolean;
}): string {
  if (!waiver.clears_at_known || !waiver.clears_at_display) return "W · clear time unknown";
  return `W · ${waiver.clears_at_display}`;
}

/* --- basis -------------------------------------------------------------------------- */

/** The four actual splits, in toggle order. */
export const ACTUAL_SPLITS: { basis: Exclude<TeamBasis, "projected">; label: string }[] = [
  { basis: "season", label: "Season" },
  { basis: "l7", label: "L7" },
  { basis: "l15", label: "L15" },
  { basis: "l30", label: "L30" },
];

export function isTeamBasis(value: string | null | undefined): value is TeamBasis {
  return (
    value === "projected" || value === "season" || value === "l7" || value === "l15" || value === "l30"
  );
}

export function isDayView(value: string | null | undefined): value is DayView {
  return value === "actual" || value === "plan";
}

/** `/team?day=2&basis=l7&view=plan`, leaving out what is default. */
export function teamHref(day: number | null, basis: TeamBasis, view: DayView = "actual"): string {
  const params = new URLSearchParams();
  if (day !== null) params.set("day", String(day));
  if (basis !== "projected") params.set("basis", basis);
  if (view !== "actual") params.set("view", view);
  const encoded = params.toString();
  return encoded ? `/team?${encoded}` : "/team";
}
