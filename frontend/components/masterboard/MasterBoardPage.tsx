"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Segment, Segmented } from "@/components/board/BoardControls";
import {
  ApiError,
  HORIZONS,
  POSITIONS,
  api,
  type Horizon,
  type MasterBoardLens,
  type MasterBoardResponse,
  type MasterPlayerRow,
  type Position,
} from "@/lib/api";
import { HORIZON_LABEL } from "@/lib/board";
import {
  PAGE,
  activeScope,
  addCut,
  matches,
  moveCut,
  moveTo,
  nextTag,
  normalizeCuts,
  nudgedCut,
  removeCut,
  scopeLabel,
  scopeTiers,
  shownTier,
  tierBands,
} from "@/lib/masterboard";
import {
  MasterEmpty,
  MasterFailed,
  MasterLoading,
  NoSearchMatch,
  NoneAtPosition,
} from "./MasterStates";
import { MasterRow, type RowHandlers } from "./MasterRow";
import { PlayerDetail } from "./PlayerDetail";
import { SetAsideTray } from "./SetAsideTray";
import { TierBreakSlot, TierDivider, type DividerHandlers } from "./TierDivider";

/**
 * My board: the one list on this site that is an opinion rather than a computation.
 *
 * Everything else here recomputes an order out of somebody's numbers on every request. This
 * page's order is STORED, and the interaction is the whole feature: drag a player to a spot
 * and he stays there while the consensus underneath him moves. The consensus is beside each
 * row as a REFERENCE — `consensus_rank` and how far above the field you have him — never as
 * the sort.
 *
 * Three things hold that up, and they are the parts worth understanding before changing any
 * of it:
 *
 * * **The full order is always in state, and a save always sends all of it.** `PUT
 *   /master/order` takes the entire non-excluded board and validates it as a permutation, so
 *   a window or a search result can never be what gets written. What is windowed is the DOM.
 * * **The server's board wins.** Every move is applied optimistically (a 1000-row round trip
 *   is not something you want between a drag and its result) and then REPLACED by the board
 *   the write answers with — which has the real ranks, and may carry a player the reconcile
 *   just inserted. A failed write throws the guess away and re-reads.
 * * **The horizon is a lens.** Flipping it refetches and changes the reference column and
 *   nothing else. If a horizon change ever reorders this page, that is a bug in the lens, not
 *   a feature of the board.
 *
 * TIERS AND THE POSITION FILTER are the two dials added on top of that, and they are really
 * one dial:
 *
 * * A tier is a BAND over the ranks, stored as the rank it starts at, so the dividers are
 *   rows interleaved into the same table and moving a player across one re-tiers him with
 *   NOTHING written. Editing a divider writes cut ranks and cannot move a player. The two
 *   edits never touch each other's endpoint.
 * * `?position=PG` narrows who is shown — in board order, board ranks intact — and switches
 *   the dividers to the point guards' own scope, whose cut ranks count point guards. So the
 *   filter picks both the rows and the bands, which is why `activeScope` is a single
 *   function and not a `?? "overall"` at each call site.
 * * Reordering is OFF under a filter. A move in a filtered sub-order ("one above the next
 *   centre") has no honest expression as a full-board permutation, and `PUT /master/order`
 *   takes nothing less than the whole board. Tags, notes, exclude and the dividers stay.
 *
 * DRAFT MODE is the third dial, and it is OFF by default: with it off the request this page
 * makes is byte-identical to the one it made before the draft room existed. On, every row
 * comes back marked with whether the live draft has taken him and by which seat; on and
 * hiding, the drafted rows are simply not in the response. It is a LENS and not a lock —
 * a drafted player can still be moved, tagged and noted, because this board outlives the
 * draft it is being read against.
 *
 * WHICH COSTS US THE "REPLACE FROM THE WRITE" PATH, in draft mode only. The annotation
 * lives on `GET /master/board` alone, so every write answers with UN-annotated rows: render
 * one and every "drafted" chip on screen vanishes until the next read. So while the lens is
 * on, a successful write bumps `reload` instead of rendering its own response. The
 * optimistic order stays on screen in the meantime, which is exactly what it is for.
 */

type Settled =
  | { status: "ready"; board: MasterBoardResponse }
  | { status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

/** The board's `horizon` arrives as a bare `str`; label the two we know and echo anything else. */
function horizonLabel(value: string): string {
  return value in HORIZON_LABEL ? HORIZON_LABEL[value as Horizon] : value;
}

/** What the cut ranks of the shown scope are counting, for a sentence like "3–8 centres". */
function scopeNoun(position: Position | null): string {
  return position === null ? "on the board" : `among ${scopeLabel(position)}`;
}

/**
 * The three states of the draft lens, and what each one asks the backend for.
 *
 * Three rather than a checkbox pair, because "annotate" and "hide" are one decision made in
 * one place: marking a drafted row and removing it are two answers to "what do I want to see
 * of what the room has taken", and nobody wants `hide_drafted` without `draft_mode`.
 */
const DRAFT_LENSES = ["off", "show", "hide"] as const;
type DraftLens = (typeof DRAFT_LENSES)[number];

const LENS_LABEL: Record<DraftLens, string> = {
  off: "Off",
  show: "Show drafted",
  hide: "Hide drafted",
};

const LENS_TITLE: Record<DraftLens, string> = {
  off: "No draft annotation at all — the board exactly as it reads with no draft running.",
  show:
    "Mark every row the live draft has taken, and which seat took him. Nothing is hidden: a board that removed him could not show that the tier above yours just emptied.",
  hide: "Leave the drafted players out entirely, for reading the board as who is actually still there. The ranks that remain are untouched, so a gap in the rank column is a player the room took.",
};

/** What each state sends. `{}` is the pre-draft request, unchanged. */
const LENS_PARAMS: Record<DraftLens, MasterBoardLens> = {
  off: {},
  show: { draft_mode: true },
  hide: { draft_mode: true, hide_drafted: true },
};

const BUTTON =
  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed " +
  "disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm text-zinc-800 " +
  "placeholder:text-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

export function MasterBoardPage() {
  const [horizon, setHorizon] = useState<Horizon>("dynasty");
  const [position, setPosition] = useState<Position | null>(null);
  const [lens, setLens] = useState<DraftLens>("off");
  const [reload, setReload] = useState(0);
  // Keyed by the request it answers, the same guard the board and market pages use: a board
  // read against the horizon you were looking at a moment ago is not this horizon's board.
  // The position rides in the key for the same reason — the centres' board is not the board.
  // So does the draft lens: an un-annotated board is not the annotated one.
  const [settled, setSettled] = useState<(Settled & { key: string }) | null>(null);

  const [term, setTerm] = useState("");
  const [visible, setVisible] = useState(PAGE);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);

  // Drag lives in a ref for the LOGIC and in state only for the highlight: a dragover fires
  // every frame, and re-deriving the row handlers on each one would defeat their memoisation
  // at exactly the moment it matters.
  //
  // TWO kinds of drag land on these rows, and they are kept apart by which ref is set rather
  // than by reading the dataTransfer (jsdom has none, and a reorder that only works in a real
  // browser is one nobody can test). `dragCut` holds the RANK of a divider being dragged;
  // `dragFrom` holds the INDEX of a player being dragged; a drop reads `dragCut` first, so a
  // divider released over a row moves the line and never the man.
  const dragFrom = useRef<number | null>(null);
  const dragCut = useRef<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const [cutDrag, setCutDrag] = useState<number | null>(null);
  const [cutDrop, setCutDrop] = useState<number | null>(null);
  /**
   * The row whose stat line is open, or null. Held as `{ id, name }` rather than as the row
   * object so a board that reloads underneath the dialog — a save, a horizon flip — cannot
   * leave a stale row rendering in it: the dialog fetches by id and shows what it fetched.
   */
  const [detail, setDetail] = useState<{ id: number; name: string } | null>(null);

  const scope = activeScope(position);
  const key = `${horizon}:${scope}:${lens}`;
  // With the lens off there is nothing to preserve across a write and the old path stands.
  const annotating = lens !== "off";

  useEffect(() => {
    let cancelled = false;
    // Rebuilt here rather than closed over, so the effect's dependencies are the three dials
    // the request is actually made of.
    const answering = `${horizon}:${activeScope(position)}:${lens}`;
    api
      .masterBoard(horizon, position, LENS_PARAMS[lens])
      .then((board) => {
        if (!cancelled) setSettled({ key: answering, status: "ready", board });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setSettled({ key: answering, status: "error", error: asApiError(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [horizon, position, lens, reload]);

  const fresh = settled?.key === key ? settled : null;
  // The board on screen: the current view's if it has arrived, otherwise the one we were
  // reading a moment ago, dimmed and locked, so flipping a dial doesn't blank a draft board.
  const shown = fresh ?? settled;
  const board = shown?.status === "ready" ? shown.board : null;
  const loading = fresh === null;
  const busy = saving || loading;
  // A filtered sub-order cannot be saved as a full-board permutation. See the header.
  const reorderable = position === null;

  // What the stable handlers below read the current order out of. A ref, because they must
  // not be re-created every time the board changes — see the drag note above.
  const boardRef = useRef<MasterBoardResponse | null>(null);
  useEffect(() => {
    boardRef.current = fresh?.status === "ready" ? fresh.board : null;
  });

  // Which write is the latest one issued. See `commit`.
  const pending = useRef(0);

  /**
   * Run one write, and let its answer be the board.
   *
   * The ticket is the ordering guarantee: two moves in quick succession both send the whole
   * order, and the older response landing second would silently undo the newer move.
   */
  const commit = useCallback(
    async (run: () => Promise<MasterBoardResponse>, message: string) => {
      const ticket = (pending.current += 1);
      setFailure(null);
      setSaving(true);
      try {
        const response = await run();
        if (ticket === pending.current) {
          // A write's response carries no draft annotation — the flags ride on
          // `GET /master/board` and nowhere else — so under the lens we re-read rather than
          // render it, and every "drafted" chip survives the save. See the header.
          if (annotating) setReload((count) => count + 1);
          else setSettled({ key, status: "ready", board: response });
          setNote(message);
        }
      } catch (caught: unknown) {
        setFailure(asApiError(caught));
        setNote(null);
        // The optimistic order was a guess and it was wrong. Nothing was written; re-read
        // rather than leave a board on screen that no longer matches what is stored.
        setReload((count) => count + 1);
      } finally {
        if (ticket === pending.current) setSaving(false);
      }
    },
    [annotating, key],
  );

  /** Apply a move locally, then save the WHOLE order it produced. */
  const applyMove = useCallback(
    (from: number, to: number) => {
      const current = boardRef.current;
      if (!current || current.position !== null) return;
      const next = moveTo(current.players, from, to);
      if (next === current.players) return;

      const moved = current.players[from];
      boardRef.current = { ...current, players: next };
      setSettled((state) =>
        state?.status === "ready"
          ? { ...state, board: { ...state.board, players: next } }
          : state,
      );

      const landed = next.findIndex((row) => row.espn_player_id === moved.espn_player_id) + 1;
      void commit(
        () =>
          api.putMasterOrder(
            next.map((row) => row.espn_player_id),
            horizon,
          ),
        `${moved.name} moved to #${landed}.`,
      );
    },
    [commit, horizon],
  );

  /* --- the dividers ------------------------------------------------------------------- */

  // The scope on screen, as the board itself reports it. Drawn from `tiers` rather than
  // inferred from the rows, so the page cannot end up disagreeing with the board about where
  // a tier starts — and normalised on the way in, so what is drawn is also what is sendable.
  const scopeRow = useMemo(
    () => (board ? scopeTiers(board.tiers, scope) : null),
    [board, scope],
  );
  const scopeSize = scopeRow?.size ?? 0;
  const cuts = useMemo(
    () => normalizeCuts(scopeRow?.cut_ranks ?? [], scopeSize),
    [scopeRow, scopeSize],
  );
  const bands = useMemo(() => tierBands(cuts, scopeSize), [cuts, scopeSize]);
  /** The band that OPENS at a rank, so a divider is drawn before exactly the right row. */
  const opensAt = useMemo(
    () => new Map(bands.map((band) => [band.start, band])),
    [bands],
  );

  /**
   * Save one scope's dividers, whole.
   *
   * Every caller hands this a list that has been through `normalizeCuts` — sorted, unique,
   * inside 1..size, leading 1 — so the backend's 422 on a bad list is a guard against a bug
   * rather than a case this page is expected to hit. A list identical to the stored one is
   * dropped here rather than sent: nudging a divider into the wall should not write.
   */
  const writeCuts = useCallback(
    (next: number[], message: string) => {
      const current = boardRef.current;
      if (!current) return;
      const held = scopeTiers(current.tiers, scope);
      const stored = normalizeCuts(held?.cut_ranks ?? [], held?.size ?? 0);
      if (next.length === stored.length && next.every((rank, i) => rank === stored[i])) return;
      void commit(() => api.putMasterTiers(scope, next, horizon), message);
    },
    [commit, horizon, scope],
  );

  const addBreak = useCallback(
    (rank: number) => {
      writeCuts(
        addCut(cuts, rank, scopeSize),
        `New tier starts at ${rank} ${scopeNoun(position)}.`,
      );
    },
    [cuts, position, scopeSize, writeCuts],
  );

  const dividers = useMemo<DividerHandlers>(
    () => ({
      onNudge: (rank, step) => {
        const target = nudgedCut(cuts, rank, scopeSize, step);
        if (target === null) return;
        writeCuts(
          moveCut(cuts, rank, target, scopeSize),
          `Tier break moved to ${target} ${scopeNoun(position)}.`,
        );
      },
      onRemove: (rank) => {
        writeCuts(
          removeCut(cuts, rank, scopeSize),
          `Tier break at ${rank} removed — it merged into the tier above.`,
        );
      },
      onDragStart: (rank) => {
        dragCut.current = rank;
        setCutDrag(rank);
        setCutDrop(rank);
      },
      onDragOver: (rank) => {
        if (dragCut.current === null) return;
        setCutDrop((current) => (current === rank ? current : rank));
      },
      onDrop: (rank) => {
        const from = dragCut.current;
        dragCut.current = null;
        setCutDrag(null);
        setCutDrop(null);
        if (from === null) return;
        writeCuts(
          moveCut(cuts, from, rank, scopeSize),
          `Tier break moved to ${rank} ${scopeNoun(position)}.`,
        );
      },
      onDragEnd: () => {
        dragCut.current = null;
        setCutDrag(null);
        setCutDrop(null);
      },
    }),
    [cuts, position, scopeSize, writeCuts],
  );

  const handlers = useMemo<RowHandlers>(
    () => ({
      onMove: applyMove,
      onTag: (row) => {
        const tag = nextTag(row.tag);
        void commit(
          () => api.putMasterEntry(row.espn_player_id, { tag }, horizon),
          tag === null ? `${row.name} untagged.` : `${row.name} tagged ${tag}.`,
        );
      },
      onNote: (row, text) => {
        // Empty means "clear it", which is an explicit null rather than an empty string —
        // the backend treats a missing key as "leave it alone" and null as "clear".
        const value = text.trim() === "" ? null : text;
        void commit(
          () => api.putMasterEntry(row.espn_player_id, { note: value }, horizon),
          value === null ? `Note on ${row.name} cleared.` : `Note on ${row.name} saved.`,
        );
      },
      onExclude: (row) => {
        void commit(
          () => api.putMasterEntry(row.espn_player_id, { excluded: true }, horizon),
          `${row.name} set aside — everyone below him moved up one.`,
        );
      },
      // Opening a stat line writes nothing and asks the board for nothing: it is the one row
      // handler here that is not a mutation, so it does not go through `commit` and cannot
      // put the page in its saving state.
      onDetail: (row) => setDetail({ id: row.espn_player_id, name: row.name }),
      onDragStart: (index) => {
        dragFrom.current = index;
        setDragIndex(index);
        setDropIndex(index);
      },
      onDragOver: (index) => {
        // A divider being dragged over a player row is a divider move, not a reorder.
        if (dragCut.current !== null) {
          dividers.onDragOver(index + 1);
          return;
        }
        if (dragFrom.current === null) return;
        setDropIndex((current) => (current === index ? current : index));
      },
      onDrop: (index) => {
        if (dragCut.current !== null) {
          dividers.onDrop(index + 1);
          return;
        }
        const from = dragFrom.current;
        dragFrom.current = null;
        setDragIndex(null);
        setDropIndex(null);
        if (from !== null) applyMove(from, index);
      },
      onDragEnd: () => {
        dragFrom.current = null;
        setDragIndex(null);
        setDropIndex(null);
      },
    }),
    [applyMove, commit, dividers, horizon],
  );

  const restore = useCallback(
    (row: MasterPlayerRow) => {
      void commit(
        () => api.putMasterEntry(row.espn_player_id, { excluded: false }, horizon),
        `${row.name} is back on the board, at the slot the field implies.`,
      );
    },
    [commit, horizon],
  );

  async function reset() {
    const confirmed = window.confirm(
      "Rebuild the board from the consensus? Every rank you have set by hand, every tag and " +
        "every note goes with it. This cannot be undone.",
    );
    if (!confirmed) return;
    await commit(() => api.resetMasterBoard(horizon), "Board re-seeded from the consensus.");
  }

  async function reseedTiers() {
    const confirmed = window.confirm(
      `Re-cut the tiers for ${scopeLabel(scope)} from the value gaps? Every divider you have ` +
        "moved, added or removed in this scope is discarded. The order, the tags and the " +
        "notes are untouched, and no other scope changes.",
    );
    if (!confirmed) return;
    await commit(
      () => api.reseedMasterTiers(scope, horizon),
      `Tiers for ${scopeLabel(scope)} re-cut from the value gaps.`,
    );
  }

  // Memoised so that the derived lists below are stable across a render that changed only a
  // drag highlight — which is every frame of a drag, over up to 175 memoised rows.
  const players = useMemo(() => board?.players ?? [], [board]);

  /** Every row paired with its index in the FULL order — the index every control reports. */
  const placed = useMemo(() => players.map((row, index) => ({ row, index })), [players]);
  const found = useMemo(
    () => (term.trim() === "" ? placed : placed.filter(({ row }) => matches(row, term))),
    [placed, term],
  );

  const searching = term.trim() !== "";
  const limit = searching ? PAGE : visible;
  const drawn = found.slice(0, limit);
  const hidden = found.length - drawn.length;
  const noun = scopeNoun(position);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">My board</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Your order, kept. Move a player and he stays where you put him — a projection
          landing or a list being re-imported moves the reference column beside him and moves
          nobody&rsquo;s rank. The board starts as the consensus, once; everything after that
          is a decision you made.
        </p>
      </header>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-4">
          <Segmented label="Read against">
            {HORIZONS.map((option) => (
              <Segment
                key={option}
                active={horizon === option}
                onClick={() => setHorizon(option)}
                title={`Compare your order to the ${HORIZON_LABEL[option].toLowerCase()} consensus. It changes the reference column, never the order.`}
              >
                {HORIZON_LABEL[option]}
              </Segment>
            ))}
          </Segmented>

          {/* The same control the value board uses, and here it does one thing more: it picks
              which scope's tiers are drawn. A position's dividers are cut over that position's
              own order, so looking at the centres and looking at the centres' tiers is one
              decision rather than two. */}
          <Segmented label="Position">
            <Segment
              active={position === null}
              onClick={() => setPosition(null)}
              title="The whole board, with the overall tiers."
            >
              All
            </Segment>
            {POSITIONS.map((option) => (
              <Segment
                key={option}
                active={position === option}
                onClick={() => setPosition(option)}
                title={`Only ${scopeLabel(option)}, in board order with their board ranks — and their own tiers.`}
              >
                {option}
              </Segment>
            ))}
          </Segmented>

          {/* The lens onto the live draft. Off by default and off is the pre-draft board:
              the request carries neither flag, so nothing about this page changes until you
              ask it to. */}
          <Segmented label="Draft">
            {DRAFT_LENSES.map((option) => (
              <Segment
                key={option}
                active={lens === option}
                onClick={() => setLens(option)}
                title={LENS_TITLE[option]}
              >
                {LENS_LABEL[option]}
              </Segment>
            ))}
          </Segmented>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="master-search"
              className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
            >
              Find a player
            </label>
            <input
              id="master-search"
              value={term}
              placeholder="name or team"
              onChange={(event) => setTerm(event.target.value)}
              className={`${FIELD} w-52`}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void reseedTiers()}
            disabled={busy || board === null}
            title={`Throw away the dividers you have set for ${scopeLabel(scope)} and cut them from the value gaps again. No other scope changes.`}
            className={`${BUTTON} border border-zinc-300 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900`}
          >
            Reset tiers to auto
          </button>
          <button
            type="button"
            onClick={() => void reset()}
            disabled={busy || board === null}
            title="Throw the board away and rebuild it from the consensus."
            className={`${BUTTON} border border-zinc-300 text-zinc-700 hover:bg-rose-50 hover:text-rose-700 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-rose-500/10 dark:hover:text-rose-300`}
          >
            Reset to consensus
          </button>
        </div>
      </div>

      {failure ? <MasterFailed error={failure} /> : null}

      {settled === null ? <MasterLoading label="Reading your board…" /> : null}
      {fresh?.status === "error" ? <MasterFailed error={fresh.error} /> : null}

      {board ? (
        <section className="flex flex-col gap-3" aria-busy={busy}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <p className="font-mono text-xs text-zinc-500">
              {board.total_ranked} ranked
              {position === null ? "" : ` ${scopeLabel(position)}`}
              {board.set_aside.length > 0 ? ` · ${board.set_aside.length} set aside` : ""}
              {board.stale > 0 ? ` · ${board.stale} stale` : ""}
              {board.added > 0 ? ` · ${board.added} new` : ""}
              {bands.length > 0
                ? ` · ${bands.length} tier${bands.length === 1 ? "" : "s"}`
                : ""}{" "}
              · field = {horizonLabel(board.horizon)} consensus of {board.sources.length}{" "}
              source{board.sources.length === 1 ? "" : "s"} over {board.pool_size}
            </p>
            {/* What the last write did. It outlives the control that caused it on purpose:
                the answer to "did that save" is the only thing this page can't show you by
                just being correct. */}
            <p role="status" className="text-xs text-zinc-500">
              {loading
                ? `Reading against the ${HORIZON_LABEL[horizon].toLowerCase()} field…`
                : saving
                  ? "Saving…"
                  : note
                    ? `Saved — ${note}`
                    : ""}
            </p>
          </div>

          {board.seeded ? (
            <p className="rounded-md bg-sky-50 px-3 py-2 text-xs text-sky-900 dark:bg-sky-500/10 dark:text-sky-200">
              This is your board&rsquo;s first read, so it was seeded from the consensus. Every
              move you make from here is yours and survives everything that lands afterwards.
            </p>
          ) : null}

          {!reorderable ? (
            <p className="rounded-md bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
              Showing {scopeLabel(position as Position)} only, at their board ranks.{" "}
              <strong className="font-semibold">Reordering is off here</strong> — a move inside
              one position can&rsquo;t say where the player lands on the whole board, and the
              order saves as a whole board or not at all. Switch to All to move anyone. Tags,
              notes, setting aside and the tier dividers below all work as usual, and these
              dividers are the {scopeLabel(position as Position)}&rsquo; own.
            </p>
          ) : null}

          {annotating ? (
            <p
              data-draft-lens={lens}
              className="rounded-md bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400"
            >
              {lens === "hide" ? (
                <>
                  Showing who is <strong className="font-semibold">still there</strong> — the
                  players the live draft has taken are left out. Their ranks go with them, so a
                  jump in the rank column is a man the room took; nothing has been renumbered.
                </>
              ) : (
                <>
                  Marked with what the live draft has taken. Drafted players{" "}
                  <strong className="font-semibold">stay in place</strong>, because a board that
                  removed them couldn&rsquo;t show you that the tier above yours just emptied.
                  You can still move, tag and note any of them — this is a lens, not a lock.
                </>
              )}{" "}
              <Link href="/draft" className="underline">
                The draft room
              </Link>{" "}
              is where the picks go in.
            </p>
          ) : null}

          {players.length === 0 && position !== null ? (
            // A full board seen through a filter that matches nobody — not an empty board.
            <NoneAtPosition position={scopeLabel(position)} />
          ) : players.length === 0 ? (
            <MasterEmpty />
          ) : found.length === 0 ? (
            <NoSearchMatch term={term.trim()} />
          ) : (
            <>
              <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
                <table className="w-full border-collapse text-[13px]">
                  <caption className="sr-only">
                    Your ranking, {board.total_ranked} players, read against the{" "}
                    {board.horizon} consensus, in {bands.length} tiers. Drag a row&rsquo;s
                    handle, use the up and down buttons, or type a rank into the move box.
                    Drag a tier divider to move where a band starts. Click a player&rsquo;s
                    name for his last season and what the market says about him.
                  </caption>
                  <thead className="sticky top-0 z-10">
                    <tr className="bg-zinc-50 text-[11px] font-semibold tracking-wide text-zinc-500 uppercase dark:bg-zinc-900/95">
                      <th scope="col" className="border-b border-zinc-200 dark:border-zinc-800">
                        <span className="sr-only">Drag handle</span>
                      </th>
                      <th
                        scope="col"
                        className="border-b border-zinc-200 px-2 py-2 text-right dark:border-zinc-800"
                        title="Where YOU have him, and which tier that rank falls in. The only number on this page that is a decision."
                      >
                        You
                      </th>
                      <th
                        scope="col"
                        className="border-b border-zinc-200 px-3 py-2 text-left dark:border-zinc-800"
                      >
                        Player
                      </th>
                      <th
                        scope="col"
                        className="hidden border-b border-zinc-200 px-3 py-2 text-right sm:table-cell dark:border-zinc-800"
                        title="Where the equal-weight consensus of every available source has him, under the horizon above"
                      >
                        Field
                      </th>
                      <th
                        scope="col"
                        className="border-b border-zinc-200 px-3 py-2 text-right dark:border-zinc-800"
                        title="How many spots ABOVE the field you have him. ▲ is a player you are out on a limb for; ▼ is one the room likes more than you do."
                      >
                        Edge
                      </th>
                      <th
                        scope="col"
                        className="hidden border-b border-zinc-200 px-3 py-2 text-right lg:table-cell dark:border-zinc-800"
                        title="What last season was worth PER GAME under our scoring — production that happened, not a forecast. An em dash means he has never completed one. Click his name for the box score."
                      >
                        Last yr FP
                      </th>
                      <th
                        scope="col"
                        className="hidden border-b border-zinc-200 px-3 py-2 text-right lg:table-cell dark:border-zinc-800"
                        title="What the sportsbooks imply, per game. Partial by construction — a player with one prop is priced on that prop alone — so read it as a reference, never as a ranking."
                      >
                        Mkt proj
                      </th>
                      <th
                        scope="col"
                        className="border-b border-zinc-200 px-3 py-2 text-left dark:border-zinc-800"
                      >
                        Tag
                      </th>
                      <th
                        scope="col"
                        className="hidden border-b border-zinc-200 px-3 py-2 text-left md:table-cell dark:border-zinc-800"
                      >
                        Note
                      </th>
                      <th
                        scope="col"
                        className="border-b border-zinc-200 px-2 py-2 text-right dark:border-zinc-800"
                      >
                        Move
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {drawn.map(({ row, index }) => {
                      // His rank WITHIN THE SHOWN SCOPE: the board rank under All, his place
                      // among that position under a filter. Both are "his index here plus
                      // one", because the backend hands back the scope's order either way.
                      const rank = index + 1;
                      const band = opensAt.get(rank);
                      return (
                        <Fragment key={row.espn_player_id}>
                          {band ? (
                            <TierDivider
                              tier={band.tier}
                              start={band.start}
                              end={band.end}
                              scopeNoun={noun}
                              canMoveUp={nudgedCut(cuts, band.start, scopeSize, -1) !== null}
                              canMoveDown={nudgedCut(cuts, band.start, scopeSize, 1) !== null}
                              removable={band.start > 1}
                              editable={!busy}
                              dragging={cutDrag === band.start}
                              dropTarget={cutDrop === band.start && cutDrag !== band.start}
                              handlers={dividers}
                            />
                          ) : searching ? null : (
                            // Only off-search: between two rows that aren't adjacent on the
                            // board, "a tier starts here" is a question about a gap that
                            // isn't on screen.
                            <TierBreakSlot
                              rank={rank}
                              scopeNoun={noun}
                              onAdd={addBreak}
                              disabled={busy}
                            />
                          )}
                          <MasterRow
                            row={row}
                            index={index}
                            total={players.length}
                            tier={shownTier(cuts, rank)}
                            positionTier={
                              position === null && row.position_scope && row.position_tier
                                ? { scope: row.position_scope, tier: row.position_tier }
                                : null
                            }
                            dragging={dragIndex === index}
                            dropTarget={
                              cutDrag !== null
                                ? cutDrop === rank
                                : dropIndex === index && dragIndex !== index
                            }
                            busy={busy}
                            reorderable={reorderable}
                            handlers={handlers}
                          />
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <p className="text-xs text-zinc-500">
                  {searching
                    ? `${found.length} of ${players.length} match “${term.trim()}”` +
                      (hidden > 0 ? ` — showing the first ${drawn.length}` : "")
                    : `Showing ${drawn.length} of ${players.length}`}
                </p>
                {/* Depth is handled by a window plus search, not by scrolling: a 1000-row
                    table costs a layout pass on every rank change, and nobody drags a player
                    from 240 to 30 anyway — they type it into the move box. */}
                {!searching && hidden > 0 ? (
                  <button
                    type="button"
                    onClick={() => setVisible((count) => count + PAGE)}
                    className={`${BUTTON} border border-zinc-300 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900`}
                  >
                    Show {Math.min(PAGE, hidden)} more
                  </button>
                ) : null}
                {!searching && hidden > 0 ? (
                  <p className="text-xs text-zinc-500">
                    Deeper than this, search by name — it covers the whole order, and the move
                    box takes any rank.
                  </p>
                ) : null}
              </div>
            </>
          )}

          <SetAsideTray players={board.set_aside} onRestore={restore} busy={busy} />
        </section>
      ) : null}

      {detail === null ? null : (
        <PlayerDetail
          playerId={detail.id}
          name={detail.name}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}
