/**
 * The waiver plan's rules, as functions: how the plan list groups and orders, what each move
 * row says, which status chip it wears, what the composer offers as a drop, how its pickup
 * list reorders, what the acquisitions meter reads, and what each entry point prefills.
 *
 * Pure, like `lib/team.ts`: the plan itself is server state (`GET /team/plan`), and nothing
 * here caches it. These only read what the server sent.
 *
 * MOVE NUMBERS are positions in plan order (the server's `(sp, sequence, id)`), 1-based: "move
 * 1" is the first move in the list, whatever its database id. They are what the list prints
 * and what the composer's chain options and the delete prompt refer to.
 */

import type {
  Acquisitions,
  CalendarMatchup,
  FreeAgentRow,
  PlanAcquisitions,
  PlanMove,
  PlanMoveCreateBody,
  PlanMovePatchBody,
  PlanPlayerRef,
  PlanWarning,
  TeamCalendarResponse,
  TeamDayResponse,
} from "@/lib/api";
import { dayLabel, weekday } from "@/lib/team";

/* --- order and grouping ------------------------------------------------------------- */

/** Plan order: day, then sequence, then id (the server's own order, re-applied). */
export function sortMoves(moves: readonly PlanMove[]): PlanMove[] {
  return [...moves].sort((a, b) => a.sp - b.sp || a.sequence - b.sequence || a.id - b.id);
}

/** Each move's number in plan order, by id. */
export function moveNumbers(moves: readonly PlanMove[]): Map<number, number> {
  return new Map(sortMoves(moves).map((move, index) => [move.id, index + 1]));
}

export type MatchupGroupOfMoves = {
  matchupId: number | null;
  label: string;
  acquisition: PlanAcquisitions | null;
  moves: PlanMove[];
};

/**
 * The plan list's sections: one per matchup that has moves or an acquisition row, in calendar
 * order, each with its moves in plan order. A move outside every matchup gets a last section.
 */
export function groupMovesByMatchup(
  moves: readonly PlanMove[],
  acquisitions: readonly PlanAcquisitions[],
  matchups: readonly CalendarMatchup[],
): MatchupGroupOfMoves[] {
  const order = new Map(matchups.map((m, index) => [m.id, index]));
  const ids = new Set<number | null>([
    ...acquisitions.map((a) => a.matchup_id),
    ...moves.map((m) => m.matchup_id),
  ]);
  // Unknown matchups after the calendar's, the no-matchup section last.
  const rank = (id: number | null) => (id === null ? 2e9 : (order.get(id) ?? 1e9));
  return [...ids]
    .sort((a, b) => rank(a) - rank(b) || (a ?? 0) - (b ?? 0))
    .map((matchupId) => {
      const acquisition = acquisitions.find((a) => a.matchup_id === matchupId) ?? null;
      const matchup = matchups.find((m) => m.id === matchupId);
      return {
        matchupId,
        label: acquisition?.label ?? matchup?.label ?? "Outside the schedule",
        acquisition,
        moves: sortMoves(moves.filter((m) => m.matchup_id === matchupId)),
      };
    });
}

/* --- what a row says ---------------------------------------------------------------- */

/** `Jerami Grant (or Draymond Green, Cam Whitmore)`. */
export function withAlternatives(name: string, alternatives: readonly PlanPlayerRef[]): string {
  return alternatives.length > 0
    ? `${name} (or ${alternatives.map((p) => p.name).join(", ")})`
    : name;
}

const PROBLEM: Record<NonNullable<PlanMove["drop"]["problem"]>, string> = {
  missing: "that move no longer exists",
  later: "that move lands after this one",
  skipped: "that move was skipped",
  cycle: "the chain loops back on itself",
};

/** Why a chained drop can't be trusted, in words, or null when it can. */
export function dropProblem(move: PlanMove): string | null {
  return move.drop.problem ? PROBLEM[move.drop.problem] : null;
}

/**
 * The drop half: `Drop Rudy Gobert`, or for a chain `Drop move 1's pickup: Jerami Grant (or
 * Draymond Green)` — the server's `display`, which settles to one name once move 1 is done.
 */
export function dropText(move: PlanMove, numbers: ReadonlyMap<number, number>): string {
  const drop = move.drop;
  if (drop.from_move_id === null) return `Drop ${drop.player?.name ?? drop.display}`;
  const number = numbers.get(drop.from_move_id);
  const which = number === undefined ? "a deleted move's pickup" : `move ${number}'s pickup`;
  if (drop.problem) return `Drop ${which}`;
  return `Drop ${which}: ${drop.display}`;
}

/** The pickup half: `Add Jerami Grant (backups: Draymond Green)`. */
export function pickupText(move: PlanMove): string {
  const ranked = [...move.candidates].sort((a, b) => a.rank - b.rank).map((c) => c.player);
  const [primary, ...backups] = ranked;
  if (!primary) return "Add nobody yet";
  return backups.length > 0
    ? `Add ${primary.name} (backups: ${backups.map((p) => p.name).join(", ")})`
    : `Add ${primary.name}`;
}

/** `Wed 10/21 · Drop Rudy Gobert → Add Jerami Grant (backups: Draymond Green)`. */
export function moveSentence(move: PlanMove, numbers: ReadonlyMap<number, number>): string {
  return `${dayLabel(move.date)} · ${dropText(move, numbers)} → ${pickupText(move)}`;
}

export type StatusChip = {
  kind: "planned" | "done" | "skipped" | "overdue" | "claim_pending";
  label: string;
  tone: "zinc" | "sky" | "amber" | "rose";
};

/**
 * The row's chip. Done and Skipped are final; a planned move is Claim pending when ESPN shows
 * our claim (it's in motion, which matters more than its being late), else Overdue, else
 * Planned. A move done with a backup says who it actually got.
 */
export function statusChip(move: PlanMove): StatusChip {
  if (move.status === "done") {
    const primary = move.candidates.find((c) => c.rank === 1)?.player.id;
    const got =
      move.resolved_player_id !== null && move.resolved_player_id !== primary
        ? move.candidates.find((c) => c.player.id === move.resolved_player_id)?.player.name
        : undefined;
    return { kind: "done", label: got ? `Done ✓ · got ${got}` : "Done ✓", tone: "sky" };
  }
  if (move.status === "skipped") return { kind: "skipped", label: "Skipped", tone: "zinc" };
  if (move.claim_pending) return { kind: "claim_pending", label: "Claim pending", tone: "amber" };
  if (move.overdue) return { kind: "overdue", label: "Overdue", tone: "rose" };
  return { kind: "planned", label: "Planned", tone: "zinc" };
}

/** Warnings about one move, and those about none (the top of the Plan tab). */
export function splitWarnings(warnings: readonly PlanWarning[]): {
  byMove: Map<number, PlanWarning[]>;
  general: PlanWarning[];
} {
  const byMove = new Map<number, PlanWarning[]>();
  const general: PlanWarning[] = [];
  for (const warning of warnings) {
    if (warning.move_id === null) {
      general.push(warning);
      continue;
    }
    byMove.set(warning.move_id, [...(byMove.get(warning.move_id) ?? []), warning]);
  }
  return { byMove, general };
}

/* --- reorder ------------------------------------------------------------------------ */

/**
 * ▲/▼ within one day: the `sequence` each of the day's moves needs so `moveId` trades places
 * with its neighbour. Every move of the day is renumbered 0..n-1 in the new order and only the
 * ones that change are returned (sequences need not be contiguous on the server). Empty when
 * the move is already at that end.
 */
export function reorderWithinDay(
  moves: readonly PlanMove[],
  moveId: number,
  delta: -1 | 1,
): { id: number; sequence: number }[] {
  const move = moves.find((m) => m.id === moveId);
  if (!move) return [];
  const day = sortMoves(moves.filter((m) => m.sp === move.sp));
  const index = day.findIndex((m) => m.id === moveId);
  const swap = index + delta;
  if (swap < 0 || swap >= day.length) return [];
  const next = [...day];
  [next[index], next[swap]] = [next[swap], next[index]];
  return next.flatMap((m, sequence) => (m.sequence === sequence ? [] : [{ id: m.id, sequence }]));
}

/** Can this move go up / down within its day? */
export function canReorder(moves: readonly PlanMove[], moveId: number, delta: -1 | 1): boolean {
  return reorderWithinDay(moves, moveId, delta).length > 0;
}

/* --- the waiver list ---------------------------------------------------------------- */

/** `In plan · Wed` (`· backup` when he is only a backup), one entry per planned move. */
export function inPlanLabel(
  row: Pick<FreeAgentRow, "in_plan">,
  calendar: Pick<TeamCalendarResponse, "days">,
): string | null {
  const entries = row.in_plan ?? [];
  if (entries.length === 0) return null;
  const days = entries
    .slice()
    .sort((a, b) => a.sp - b.sp)
    .map((entry) => {
      const date = calendar.days.find((d) => d.sp === entry.sp)?.date;
      const label = date ? weekday(date) : `day ${entry.sp}`;
      return entry.rank > 1 ? `${label} (backup)` : label;
    });
  return `In plan · ${days.join(", ")}`;
}

/* --- the acquisitions meter --------------------------------------------------------- */

export type Meter = {
  text: string;
  over: boolean;
  source: "espn" | "override";
  sourceLabel: string;
};

/**
 * `1 used + 3 planned · 4/7`, red when the total passes the limit.
 *
 * With nothing planned and `compact` (the Actual view), it keeps task 29a's `2 / 7`: the
 * planned half only earns its words when there is something planned, or when the reader is
 * looking at the plan.
 */
export function meter(
  acq: Pick<Acquisitions, "used" | "limit"> & {
    planned?: number;
    used_source?: "espn" | "override";
    over?: boolean;
  },
  compact = false,
): Meter {
  const planned = acq.planned ?? 0;
  const total = acq.used + planned;
  const limit = acq.limit ?? null;
  const source = acq.used_source ?? "espn";
  const over = acq.over ?? (limit !== null && total > limit);
  const text =
    compact && planned === 0
      ? `${acq.used} / ${limit ?? "—"}`
      : `${acq.used} used + ${planned} planned · ${total}/${limit ?? "—"}`;
  return {
    text,
    over,
    source,
    sourceLabel: source === "override" ? "used: your override" : "used: ESPN",
  };
}

/* --- the composer ------------------------------------------------------------------- */

/** A player in the composer's pickup list. Extra fields are what the list prints. */
export type Candidate = PlanPlayerRef;

export type DropChoice = { kind: "player"; id: number; name: string } | { kind: "move"; moveId: number };

export type ComposerDraft = {
  /** Null for a new move; set when editing. */
  moveId: number | null;
  sp: number;
  drop: DropChoice | null;
  pickups: Candidate[];
  note: string;
};

export type DropOption = {
  key: string;
  choice: DropChoice;
  label: string;
  /** The roster slot the player is in that day (named drops only). */
  slot: string | null;
  player: TeamDayResponse["slots"][number]["player"];
};

export function dropKey(choice: DropChoice): string {
  return choice.kind === "player" ? `player:${choice.id}` : `move:${choice.moveId}`;
}

/**
 * What the composer can drop on day `sp`: every player on the PLAN roster that day who is
 * really mine (a planned pickup is offered as his move's chain option instead, so the chain
 * follows whichever candidate is added), then "Pickup from move N" for each planned move
 * landing on or before `sp` whose pickup no other move already drops. The move being edited
 * is never its own option.
 */
export function dropOptions(
  planDay: TeamDayResponse | null,
  moves: readonly PlanMove[],
  sp: number,
  editingId: number | null = null,
): DropOption[] {
  const numbers = moveNumbers(moves);
  const named: DropOption[] = (planDay?.slots ?? []).flatMap((row) => {
    const player = row.player;
    if (!player || player.plan?.status === "planned") return [];
    const choice: DropChoice = { kind: "player", id: player.id, name: player.name };
    return [{ key: dropKey(choice), choice, label: player.name, slot: row.slot, player }];
  });
  const taken = new Set(
    moves
      .filter((m) => m.id !== editingId && m.status !== "skipped" && m.drop.from_move_id !== null)
      .map((m) => m.drop.from_move_id),
  );
  const chained: DropOption[] = sortMoves(moves).flatMap((move) => {
    if (move.id === editingId || move.status !== "planned" || taken.has(move.id)) return [];
    if ((move.lands_sp ?? move.sp) > sp) return [];
    const pickup = move.pickup ?? move.candidates[0]?.player;
    if (!pickup) return [];
    const choice: DropChoice = { kind: "move", moveId: move.id };
    return [
      {
        key: dropKey(choice),
        choice,
        label: `Pickup from move ${numbers.get(move.id)}: ${withAlternatives(pickup.name, move.alternatives)}`,
        slot: null,
        player: null,
      },
    ];
  });
  return [...named, ...chained];
}

/** Add a pickup at the end, unless he's already listed. */
export function addCandidate(list: readonly Candidate[], player: Candidate): Candidate[] {
  return list.some((c) => c.id === player.id) ? [...list] : [...list, player];
}

/** ▲/▼ in the pickup list; a move off either end is a no-op. */
export function moveCandidate(list: readonly Candidate[], index: number, delta: -1 | 1): Candidate[] {
  const swap = index + delta;
  if (index < 0 || index >= list.length || swap < 0 || swap >= list.length) return [...list];
  const next = [...list];
  [next[index], next[swap]] = [next[swap], next[index]];
  return next;
}

export function removeCandidate(list: readonly Candidate[], index: number): Candidate[] {
  return list.filter((_, i) => i !== index);
}

/** Where the composer was opened from, and what that entry point knows. */
export type ComposerEntry =
  /** `+ Plan` on a waiver row: that player is the primary pickup. */
  | { kind: "waiver"; player: Candidate }
  /** `Drop…` on a Plan-view roster row: that player is the drop, on the roster's day. */
  | { kind: "roster-drop"; player: { id: number; name: string }; sp: number }
  /** `Add move` on the Plan tab: nothing chosen. */
  | { kind: "add" }
  /** Edit on a plan row: the move as it stands. */
  | { kind: "edit"; move: PlanMove };

/**
 * The composer's starting draft. The day: the roster's day when opened from the roster, else
 * the waiver panel's first selected chip, else today; an edit keeps the move's own day.
 */
export function composerPrefill(
  entry: ComposerEntry,
  context: { waiverDay: number | null; today: number },
): ComposerDraft {
  const fallbackDay = context.waiverDay ?? context.today;
  switch (entry.kind) {
    case "waiver":
      return { moveId: null, sp: fallbackDay, drop: null, pickups: [entry.player], note: "" };
    case "roster-drop":
      return {
        moveId: null,
        sp: entry.sp,
        drop: { kind: "player", id: entry.player.id, name: entry.player.name },
        pickups: [],
        note: "",
      };
    case "add":
      return { moveId: null, sp: fallbackDay, drop: null, pickups: [], note: "" };
    case "edit": {
      const move = entry.move;
      const drop: DropChoice | null =
        move.drop.from_move_id !== null
          ? { kind: "move", moveId: move.drop.from_move_id }
          : move.drop.player
            ? { kind: "player", id: move.drop.player.id, name: move.drop.player.name }
            : null;
      return {
        moveId: move.id,
        sp: move.sp,
        drop,
        pickups: [...move.candidates].sort((a, b) => a.rank - b.rank).map((c) => c.player),
        note: move.note ?? "",
      };
    }
  }
}

/** What the draft is missing before it can be saved, in words. Empty when it's ready. */
export function draftProblems(draft: ComposerDraft): string[] {
  const problems: string[] = [];
  if (draft.drop === null) problems.push("Choose who to drop.");
  if (draft.pickups.length === 0) problems.push("Add at least one pickup.");
  return problems;
}

function dropFields(drop: DropChoice): { drop_player_id: number } | { drop_from_move_id: number } {
  return drop.kind === "player" ? { drop_player_id: drop.id } : { drop_from_move_id: drop.moveId };
}

/** The POST body: exactly one drop reference, the candidates in rank order. */
export function createBody(draft: ComposerDraft & { drop: DropChoice }): PlanMoveCreateBody {
  return {
    sp: draft.sp,
    ...dropFields(draft.drop),
    candidate_ids: draft.pickups.map((p) => p.id),
    note: draft.note.trim() || null,
  };
}

/** The PATCH body for an edit: the same fields (sending one drop reference clears the other). */
export function patchBody(draft: ComposerDraft & { drop: DropChoice }): PlanMovePatchBody {
  return {
    sp: draft.sp,
    ...dropFields(draft.drop),
    candidate_ids: draft.pickups.map((p) => p.id),
    note: draft.note.trim() || null,
  };
}
