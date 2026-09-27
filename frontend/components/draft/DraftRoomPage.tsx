"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Segment, Segmented } from "@/components/board/BoardControls";
import {
  ApiError,
  api,
  type DraftAvailabilityResponse,
  type DraftConfigBody,
  type DraftCreateBody,
  type DraftMode,
  type DraftStateResponse,
  type MasterBoardResponse,
} from "@/lib/api";
import { availableBoard, draftedIds } from "@/lib/draft";
import { DraftBoard } from "./DraftBoard";
import { DraftControls, OnTheClock } from "./DraftControls";
import { DraftList } from "./DraftList";
import { DraftRankings } from "./DraftRankings";
import { DraftRosters } from "./DraftRosters";
import { DraftSeats } from "./DraftSeats";
import { DraftSetup } from "./DraftSetup";
import { DraftSidebar } from "./DraftSidebar";
import { CatalogUnavailable, DraftFailed, DraftLoading } from "./DraftStates";
import { PickSearch } from "./PickSearch";

/**
 * The draft room: a standing list of who is left, and four ways to look at the room.
 *
 * THE SHAPE OF THE PAGE is a sidebar and a tabbed panel, and both halves are answers to the
 * same complaint about the version before it. The sidebar is "who is still there" — the
 * question you ask continuously, which therefore cannot live in a panel that scrolls off — and
 * behind its second tab, "what has that seat got". It is also the room's ONLY player search:
 * there is no entry box above the board any more, and its Draft button therefore enters the
 * pick on the clock whoever owns it, which is how a manual draft takes down what the room just
 * did. The edit-a-made-pick panel keeps a search of its own, because changing pick 7 is a
 * different act from making pick 12.
 * The tabs are the four different things "the draft" means: the BOARD (the room's shape),
 * the LIST (what has happened), the ROSTERS (what everyone has, and what they still own),
 * and the RANKINGS (who is left at each position, with the odds). The old stacked plan
 * panels are gone: they answered a narrower question — my next four picks, fifteen names
 * each — and `GET /draft/availability` answers it for every name on the board at once.
 *
 * THE SERVER'S BOARD WINS, and here that is not even a discipline — it is the only thing the
 * page does. Every verb (`applyPick`, `editPick`, `simulate`, `undoPick`, `resetDraft`,
 * `updateDraftConfig`) answers with the WHOLE `DraftStateResponse`, replayed from the stored
 * log, so this component never patches its own state: it replaces it with what came back.
 *
 * THREE READS, and they are read differently.
 *
 * * The DRAFT is live and re-read from every write.
 * * The master board is a CATALOG — ranks, tags, positions and the tier cut-ranks — and it
 *   is fetched ONCE on mount, because none of that can change while a draft is running and
 *   re-reading a thousand rows per pick would be absurd. What makes it safe is that the
 *   catalog is never the source of who is GONE: the drafted set comes off the live log, so
 *   the available list is current for free.
 * * AVAILABILITY is re-read whenever the draft moves, because it is a statement about the
 *   picks already made and one more pick changes every number in it. Keyed on
 *   `picks_made` + `is_complete` and request-key guarded with the same `pending` ticket the
 *   writes use, so a slow answer about an old state cannot land on top of a fresh one. It is
 *   kept OUT of `commit` on purpose: a Monte Carlo between the click and the board would put
 *   a thousand simulated rooms in front of every pick.
 *
 * A 404 FROM `GET /draft` IS NOT AN ERROR. There is one draft at a time and none of them
 * exists until somebody starts one, so the 404 is the setup form's cue — the page's ordinary
 * opening state, not a failure panel. A failed availability read is likewise a missing
 * COLUMN, not a broken page: the percentages go quiet and everything else still works.
 *
 * MODE IS ADVISORY. The backend enforces neither mode (`app/api/draft.py` says so at
 * length), so the toggle is local UI state seeded from the draft's stored mode and nothing
 * is written when it changes. It decides which controls are on screen.
 */

type Settled =
  | { status: "ready"; state: DraftStateResponse }
  /** `GET /draft` was a 404: there is no draft, which is a state and not a failure. */
  | { status: "none" }
  | { status: "error"; error: ApiError };

/** The four things "the draft" means, and the order they are offered in. */
const VIEWS = ["board", "list", "roster", "rankings"] as const;
type View = (typeof VIEWS)[number];

const VIEW_LABEL: Record<View, string> = {
  board: "Board",
  list: "List",
  roster: "Roster",
  rankings: "Rankings",
};

const VIEW_HINT: Record<View, string> = {
  board: "The snake, every seat on screen — the room's shape.",
  list: "Every pick made, by round, in the order it happened.",
  roster: "What each team has taken, and the pick numbers it still owns.",
  rankings: "Who is left overall and at each position, with your tiers and the odds.",
};

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

/** The draft's stored mode, narrowed. Anything unrecognised reads as simulation. */
function storedMode(state: DraftStateResponse | null): DraftMode {
  return state?.mode === "manual" ? "manual" : "simulation";
}

export function DraftRoomPage() {
  const [settled, setSettled] = useState<Settled | null>(null);
  const [catalog, setCatalog] = useState<MasterBoardResponse | null>(null);
  const [catalogError, setCatalogError] = useState<ApiError | null>(null);
  const [availability, setAvailability] = useState<DraftAvailabilityResponse | null>(null);
  const [availabilityError, setAvailabilityError] = useState<ApiError | null>(null);

  // Null means "whatever the draft was started in" — derived rather than copied into state,
  // so a draft replaced with a manual one doesn't keep showing the old mode's controls.
  const [chosenMode, setChosenMode] = useState<DraftMode | null>(null);
  const [view, setView] = useState<View>("board");
  const [reconfiguring, setReconfiguring] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);

  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getDraft()
      .then((state) => {
        if (!cancelled) setSettled({ status: "ready", state });
      })
      .catch((caught: unknown) => {
        const error = asApiError(caught);
        if (cancelled) return;
        setSettled(error.status === 404 ? { status: "none" } : { status: "error", error });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Once, on mount: the board every list on this page is drawn from. See the header — it is
  // a catalog, not a live read, and who is GONE comes off the draft state instead.
  useEffect(() => {
    let cancelled = false;
    api
      .masterBoard()
      .then((board) => {
        if (!cancelled) setCatalog(board);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setCatalogError(asApiError(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const state = settled?.status === "ready" ? settled.state : null;
  const mode = chosenMode ?? storedMode(state);
  const busy = saving || settled === null;

  /** Which write is the latest one issued — an older response must not land on top of it. */
  const pending = useRef(0);
  /** The same guard for the availability read, which is slower than any write here. */
  const availabilityPending = useRef(0);

  const made = state?.picks_made ?? null;
  const complete = state?.is_complete ?? null;

  useEffect(() => {
    // `made` and `complete` ARE the draft as far as this read is concerned: every write on
    // the page moves one of them, and neither moves without a write.
    if (made === null) return;
    const ticket = (availabilityPending.current += 1);
    api
      .draftAvailability()
      .then((answer) => {
        if (ticket !== availabilityPending.current) return;
        setAvailability(answer);
        setAvailabilityError(null);
      })
      .catch((caught: unknown) => {
        if (ticket === availabilityPending.current) setAvailabilityError(asApiError(caught));
      });
  }, [made, complete]);

  /**
   * Run one write and let its answer BE the draft.
   *
   * No optimistic half: the response is the replayed state, and nothing this page could
   * compute locally would be a better guess at it (see the header).
   */
  const commit = useCallback(
    async (run: () => Promise<{ state: DraftStateResponse; message: string }>) => {
      const ticket = (pending.current += 1);
      setFailure(null);
      setSaving(true);
      try {
        const { state: next, message } = await run();
        if (ticket === pending.current) {
          setSettled({ status: "ready", state: next });
          setNote(message);
          setEditing(null);
        }
      } catch (caught: unknown) {
        if (ticket === pending.current) {
          setFailure(asApiError(caught));
          setNote(null);
        }
      } finally {
        if (ticket === pending.current) setSaving(false);
      }
    },
    [],
  );

  const drafted = useMemo(() => (state ? draftedIds(state) : new Set<number>()), [state]);
  const players = useMemo(() => catalog?.players ?? [], [catalog]);
  const available = useMemo(() => availableBoard(players, drafted), [players, drafted]);
  const odds = availability?.availability ?? {};

  function start(body: DraftCreateBody) {
    // `reset=true` only when there is a draft to replace — which is the reconfigure path,
    // and the one that needs asking about, because every pick in the old one goes.
    const replacing = state !== null;
    if (replacing) {
      const confirmed = window.confirm(
        "Replace this draft? Every pick in it goes with it, and the new one starts at pick 1. " +
          "Start over keeps the seat and the mode and only drops the picks.",
      );
      if (!confirmed) return;
    }
    void commit(async () => ({
      state: await api.createDraft(body, replacing),
      message: "Draft started.",
    }));
    setChosenMode(null);
    setReconfiguring(false);
  }

  function saveSeats(body: DraftConfigBody) {
    void commit(async () => {
      const next = await api.updateDraftConfig(body);
      return {
        state: next,
        message:
          body.my_slot === undefined
            ? "Seats renamed."
            : `You are in seat ${next.my_slot} now.`,
      };
    });
  }

  function makePick(playerId: number) {
    const seat = state?.on_the_clock;
    void commit(async () => {
      // `team_slot` is deliberately omitted: the backend defaults it to whoever is on the
      // clock, and a snake only ever lets one seat pick. Passing it would turn this into an
      // assertion, which is a check against a number this page read off the same response.
      const next = await api.applyPick({ player_id: playerId });
      const made = next.log[next.log.length - 1];
      return {
        state: next,
        message: `${made?.name ?? "Pick"} to team ${seat ?? made?.team_slot}.`,
      };
    });
  }

  function editPick(pickNumber: number, playerId: number) {
    void commit(async () => {
      const next = await api.editPick(pickNumber, { player_id: playerId });
      const row = next.log.find((pick) => pick.pick_number === pickNumber);
      return {
        state: next,
        message: `Pick ${pickNumber} is ${row?.name ?? "changed"} now.`,
      };
    });
  }

  function advance(count?: number) {
    void commit(async () => {
      const advanced = await api.simulate(count === undefined ? {} : { count });
      const madeCount = advanced.picks.length;
      return {
        state: advanced.state,
        message:
          madeCount === 0
            ? "Nothing to advance — the room is waiting on you."
            : `${madeCount} pick${madeCount === 1 ? "" : "s"} by the room, seed ${advanced.seed}.`,
      };
    });
  }

  function undo() {
    void commit(async () => {
      const next = await api.undoPick();
      return { state: next, message: "Last pick taken back." };
    });
  }

  function reset() {
    const confirmed = window.confirm(
      "Start this draft over? Every pick in it is thrown away. The seat, the mode and the " +
        "field it is modelled against all stay as they are.",
    );
    if (!confirmed) return;
    void commit(async () => ({
      state: await api.resetDraft(),
      message: "Draft reset — back to pick 1.",
    }));
  }

  if (settled === null) {
    return (
      <div className="flex flex-col gap-6">
        <RoomHeader />
        <DraftLoading label="Reading the draft…" />
      </div>
    );
  }

  if (settled.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <RoomHeader />
        <DraftFailed error={settled.error} />
      </div>
    );
  }

  if (settled.status === "none" || reconfiguring) {
    return (
      <div className="flex flex-col gap-6">
        <RoomHeader />
        {failure ? <DraftFailed error={failure} /> : null}
        <DraftSetup
          teamCount={state?.team_count ?? null}
          defaultSlot={state?.my_slot ?? null}
          defaultNames={storedNames(state)}
          isBusy={saving}
          onStart={start}
        />
        {reconfiguring ? (
          <button
            type="button"
            onClick={() => setReconfiguring(false)}
            className="self-start text-xs font-medium text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-200"
          >
            Back to the draft
          </button>
        ) : null}
      </div>
    );
  }

  const live = settled.state;
  const editRow = editing === null ? null : live.log.find((pick) => pick.pick_number === editing);
  // ANY seat's pick, not only mine. The sidebar is the room's one player search now, so it has
  // to be able to enter the pick the room just made as well as my own — `makePick` drafts for
  // `on_the_clock` either way, which the snake makes unambiguous.
  const canDraft = !busy && !live.is_complete && live.on_the_clock !== null;
  const clock = clockWords(live);
  const targetPick = availability?.pick_number ?? null;
  const picksAway =
    targetPick === null ? null : targetPick - (live.next_pick_number ?? 0);

  return (
    <div className="flex flex-col gap-4" aria-busy={saving}>
      <RoomHeader />

      <OnTheClock state={live} />

      <DraftControls
        state={live}
        mode={mode}
        onMode={setChosenMode}
        isBusy={saving}
        onAdvance={() => advance()}
        onStep={() => advance(1)}
        onUndo={undo}
        onReset={reset}
        onReconfigure={() => setReconfiguring(true)}
      />

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="font-mono text-xs text-zinc-500">
          {live.team_count} teams × {live.rounds} rounds · seat {live.my_slot} · {live.mode} ·
          field = {live.field_horizon} consensus of{" "}
          {live.field_source_ids ? `${live.field_source_ids.length} sources` : "every source"}{" "}
          over {live.universe_size} ranked
        </p>
        <p role="status" className="text-xs text-zinc-500">
          {saving ? "Working…" : note ? `Done — ${note}` : ""}
        </p>
      </div>

      {failure ? <DraftFailed error={failure} /> : null}
      {catalogError ? <CatalogUnavailable error={catalogError} /> : null}
      {availabilityError ? <AvailabilityUnavailable error={availabilityError} /> : null}

      <DraftSeats state={live} isBusy={saving} onSave={saveSeats} />

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <DraftSidebar
          state={live}
          catalog={players}
          available={available}
          availability={odds}
          targetPick={targetPick}
          picksAway={picksAway}
          clock={clock}
          canDraft={canDraft}
          isBusy={busy}
          onDraft={makePick}
        />

        <div className="flex min-w-0 flex-1 flex-col gap-3">
          {editRow ? (
            <div className="flex flex-col gap-2 rounded-lg border border-sky-300 bg-sky-50 p-4 dark:border-sky-500/40 dark:bg-sky-500/10">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm font-semibold text-sky-900 dark:text-sky-100">
                  Pick {editRow.pick_number}, team {editRow.team_slot} — {editRow.name}
                </p>
                <button
                  type="button"
                  onClick={() => setEditing(null)}
                  className="text-xs font-medium text-sky-800 underline dark:text-sky-200"
                >
                  Cancel
                </button>
              </div>
              <PickSearch
                label="Change this pick to"
                hint="The seat keeps the pick — the snake owns that. The player who was here goes back on the board."
                players={players}
                drafted={drafted}
                onChoose={(playerId) => editPick(editRow.pick_number, playerId)}
                isDisabled={busy}
                shouldAutoFocus
                key={`edit-${editRow.pick_number}`}
              />
            </div>
          ) : null}

          <Segmented label="View">
            {VIEWS.map((option) => (
              <Segment
                key={option}
                active={view === option}
                onClick={() => setView(option)}
                title={VIEW_HINT[option]}
              >
                {VIEW_LABEL[option]}
              </Segment>
            ))}
          </Segmented>

          {view === "board" ? (
            <DraftBoard state={live} editing={editing} onEdit={setEditing} isDisabled={busy} />
          ) : null}
          {view === "list" ? <DraftList state={live} /> : null}
          {view === "roster" ? <DraftRosters state={live} /> : null}
          {view === "rankings" ? (
            <DraftRankings
              available={available}
              catalog={players}
              tiers={catalog?.tiers ?? []}
              availability={odds}
              targetPick={targetPick}
              picksAway={picksAway}
              clock={clock}
              canDraft={canDraft}
              isBusy={busy}
              onDraft={makePick}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Whose pick is on the clock, in the words the sidebar and the rows print.
 *
 * ONE ENTRY SURFACE, so this sentence is load-bearing rather than decorative. The board's pick
 * search is gone and the sidebar's Draft button enters whatever pick is on the clock, which is
 * how an opponent's pick gets typed in in manual mode — so the panel holding that button has
 * to say out loud which pick it would be. Null once there is nothing left to enter.
 */
function clockWords(state: DraftStateResponse): string | null {
  if (state.is_complete || state.on_the_clock === null) return null;
  if (state.is_my_pick) return `Your pick — ${state.next_pick_number}`;
  const seat = state.teams.find((team) => team.team_slot === state.on_the_clock);
  return `Pick ${state.next_pick_number} · ${seat?.name ?? `Team ${state.on_the_clock}`}`;
}

/** The names a draft actually has stored — the computed "Team N" defaults are not names. */
function storedNames(state: DraftStateResponse | null): Record<string, string> {
  if (state === null) return {};
  return Object.fromEntries(
    state.teams
      .filter((team) => team.name !== `Team ${team.team_slot}`)
      .map((team) => [String(team.team_slot), team.name]),
  );
}

/**
 * The percentages failed, but the draft didn't.
 *
 * Sibling of `CatalogUnavailable` and the same argument: the board, the clock, the lists and
 * every control still work — the one thing that doesn't is the availability column, which is
 * a separate read and the most expensive one on the page.
 */
function AvailabilityUnavailable({ error }: { error: ApiError }) {
  return (
    <p
      data-availability-read="unavailable"
      className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"
    >
      Couldn’t simulate the room, so there are no availability percentages:{" "}
      {error.detail ?? error.message}. Everything else here is unaffected — the lists are your
      board minus who has gone, which the draft log already says.
    </p>
  );
}

function RoomHeader() {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="text-xl font-semibold tracking-tight">Draft</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        The room, live. Who is left is on the left, always; the panel beside it is the board,
        the log, the rosters or your rankings, whichever you need. Every pick is stored, so
        what is here is the draft rather than a picture of it — and it replays, which is why
        undo and edit are real rather than cosmetic.
      </p>
    </header>
  );
}
