"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  api,
  type DraftCreateBody,
  type DraftMode,
  type DraftStateResponse,
  type MasterPlayerRow,
} from "@/lib/api";
import { draftedIds } from "@/lib/draft";
import { DraftBoard } from "./DraftBoard";
import { DraftControls, OnTheClock } from "./DraftControls";
import { DraftSetup } from "./DraftSetup";
import { CatalogUnavailable, DraftFailed, DraftLoading } from "./DraftStates";
import { PickSearch } from "./PickSearch";

/**
 * The draft room: the snake board, the clock, and the two things that move them.
 *
 * THE SERVER'S BOARD WINS, and here that is not even a discipline — it is the only thing the
 * page does. Every verb (`applyPick`, `editPick`, `simulate`, `undoPick`, `resetDraft`)
 * answers with the WHOLE `DraftStateResponse`, replayed from the stored log, so this
 * component never patches its own state: it replaces it with what came back. There is no
 * optimistic update anywhere below, unlike /my-board, because there is nothing to be
 * optimistic about — an advance's result is sixteen picks this page could not have guessed,
 * and a manual pick's result includes whose clock it is now.
 *
 * TWO READS, and they are read differently. The draft is live and re-read from every write.
 * The master board is a CATALOG — the names the search box offers — and it is fetched once
 * on mount, because it cannot change while a draft is running and re-reading it per
 * keystroke would be a thousand-row request to filter a dozen names out of. What makes that
 * safe is that the catalog is never the source of who is GONE: the drafted set comes off the
 * live state's log, so it is current for free.
 *
 * A 404 FROM `GET /draft` IS NOT AN ERROR. There is one draft at a time and none of them
 * exists until somebody starts one, so the 404 is the setup form's cue — the page's ordinary
 * opening state, not a failure panel.
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

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

/** The draft's stored mode, narrowed. Anything unrecognised reads as simulation. */
function storedMode(state: DraftStateResponse | null): DraftMode {
  return state?.mode === "manual" ? "manual" : "simulation";
}

export function DraftRoomPage() {
  const [settled, setSettled] = useState<Settled | null>(null);
  const [catalog, setCatalog] = useState<MasterPlayerRow[] | null>(null);
  const [catalogError, setCatalogError] = useState<ApiError | null>(null);

  // Null means "whatever the draft was started in" — derived rather than copied into state,
  // so a draft replaced with a manual one doesn't keep showing the old mode's controls.
  const [chosenMode, setChosenMode] = useState<DraftMode | null>(null);
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

  // Once, on mount: the names the search offers. See the header — it is a catalog, not a
  // live read, and who is GONE comes off the draft state instead.
  useEffect(() => {
    let cancelled = false;
    api
      .masterBoard()
      .then((board) => {
        if (!cancelled) setCatalog(board.players);
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

  const drafted = useMemo(
    () => (state ? draftedIds(state) : new Set<number>()),
    [state],
  );
  const players = catalog ?? [];

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
      const made = advanced.picks.length;
      return {
        state: advanced.state,
        message:
          made === 0
            ? "Nothing to advance — the room is waiting on you."
            : `${made} pick${made === 1 ? "" : "s"} by the room, seed ${advanced.seed}.`,
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

  return (
    <div className="flex flex-col gap-6" aria-busy={saving}>
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

      {live.is_complete ? null : (
        <PickSearch
          label={
            live.is_my_pick
              ? `Your pick — ${live.next_pick_number}`
              : `Pick ${live.next_pick_number} for team ${live.on_the_clock}`
          }
          hint={
            live.is_my_pick
              ? "Find him and take him. Only players still on the board are offered."
              : "The snake only lets the seat on the clock pick, so this one box enters every team’s pick — type in what the room just did."
          }
          players={players}
          drafted={drafted}
          onChoose={makePick}
          isDisabled={busy}
          key={`clock-${live.picks_made}`}
        />
      )}

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

      <DraftBoard state={live} editing={editing} onEdit={setEditing} isDisabled={busy} />
    </div>
  );
}

function RoomHeader() {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="text-xl font-semibold tracking-tight">Draft</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        The room, live. Enter a pick and the clock walks along the snake; in simulation, let
        the other nine seats draft themselves up to your turn. Every pick is stored, so the
        board here is the draft rather than a picture of it — and it replays, which is why
        undo and edit are real rather than cosmetic.
      </p>
    </header>
  );
}
