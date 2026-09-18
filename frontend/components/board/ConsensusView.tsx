"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  api,
  type ConsensusMethod,
  type ConsensusResponse,
  type Horizon,
  type Position,
  type SourcesResponse,
} from "@/lib/api";
import { HORIZON_LABEL } from "@/lib/board";
import { METHOD_HINT } from "@/lib/consensus";
import { isoDate } from "@/lib/format";
import {
  BoardError,
  BoardLoading,
  BoardNoMatches,
  ConsensusNoSources,
} from "./BoardStates";
import { ConsensusTable } from "./ConsensusTable";
import { SourcePanel } from "./SourcePanel";

/**
 * The multi-source half of the board: pick whose opinions are on it, then average them.
 *
 * Two fetches, and they are deliberately not one. `GET /sources` depends only on the horizon,
 * because which sources are ELIGIBLE is a property of the question being asked — a dynasty
 * top-200 is not an answer to "who helps me this season" and disappears from the panel under
 * the win-now horizon. `GET /board/consensus` then depends on the horizon AND on what was
 * picked. Collapsing them would mean re-listing the sources on every tick of a checkbox.
 *
 * SELECTION RECONCILIATION, in one sentence: whenever the available list changes, keep
 * whichever selected sources are still available — and if none are (the first load, or a
 * horizon flip that retired the only board you had picked), select all of them. So switching
 * to win-now leaves your ESPN sources ticked and quietly drops the dynasty list, rather than
 * either keeping a dead id or resetting everything you chose.
 */

type Settled<T> = { status: "ready"; response: T } | { status: "error"; error: ApiError };
type Fetching<T> = Settled<T> | { status: "loading" };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex flex-col gap-0.5" title={title}>
      <span className="text-[10px] tracking-wide text-zinc-400 uppercase dark:text-zinc-600">
        {label}
      </span>
      <span className="font-mono text-xs text-zinc-700 dark:text-zinc-300">{value}</span>
    </div>
  );
}

/** What this consensus is built from — the same idea as BoardMeta, for a different board. */
export function ConsensusMeta({ response }: { response: ConsensusResponse }) {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900/40">
      <Fact
        label="Horizon"
        value={HORIZON_LABEL[response.horizon]}
        title={`Imported lists tagged '${response.ranking_horizon}' are eligible under it`}
      />
      <Fact
        label="Averaging"
        value={response.method}
        title={METHOD_HINT[response.method]}
      />
      <Fact
        label="Sources"
        value={String(response.sources.length)}
        title={response.sources.map((source) => source.label).join(", ")}
      />
      <Fact
        label="Pool"
        value={String(response.pool_size)}
        title="Every player at least one eligible source ranks — the percentile denominator"
      />
      <Fact
        label="Ranked"
        value={`${response.players.length} of ${response.total_ranked}`}
        title="Rows shown of players at least one selected source ranks"
      />
      <Fact
        label="Ages as of"
        value={isoDate(response.age_as_of)}
        title="Every age here is a whole-year age at this date"
      />
    </div>
  );
}

export function ConsensusView({
  horizon,
  position,
  limit,
  method,
}: {
  horizon: Horizon;
  position: Position | null;
  limit: number;
  method: ConsensusMethod;
}) {
  const [settledCatalog, setSettledCatalog] = useState<
    (Settled<SourcesResponse> & { horizon: Horizon }) | null
  >(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [board, setBoard] = useState<(Settled<ConsensusResponse> & { key: string }) | null>(null);

  // --- which sources are eligible, and which of them stay picked ---------------------------
  useEffect(() => {
    let cancelled = false;

    api
      .sources(horizon)
      .then((response) => {
        if (cancelled) return;
        setSettledCatalog({ horizon, status: "ready", response });
        setSelected((current) => {
          const available = response.sources.map((source) => source.id);
          const kept = current.filter((id) => available.includes(id));
          return kept.length > 0 ? kept : available;
        });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setSettledCatalog({ horizon, status: "error", error: asApiError(caught) });
      });

    return () => {
      cancelled = true;
    };
  }, [horizon]);

  // Derived, not stored — the same trick BoardView uses: a catalog settled for a DIFFERENT
  // horizon is, by definition, a request still in flight for this one. That is also the guard
  // that stops the board below from firing with the retired list's id: `selected` is only
  // reconciled when the answer lands, and until it does there is no ready catalog to fire on.
  const catalog: Fetching<SourcesResponse> =
    settledCatalog?.horizon === horizon ? settledCatalog : { status: "loading" };

  // --- the board itself ---------------------------------------------------------------------
  const key = `${horizon}|${method}|${position ?? "ALL"}|${limit}|${[...selected].sort().join(",")}`;
  const nothingPicked = selected.length === 0;
  const catalogReady = catalog.status === "ready";

  useEffect(() => {
    if (nothingPicked || !catalogReady) return;
    let cancelled = false;

    api
      .boardConsensus({ horizon, sources: selected, method, position, limit })
      .then((response) => {
        if (!cancelled) setBoard({ key, status: "ready", response });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setBoard({ key, status: "error", error: asApiError(caught) });
      });

    return () => {
      cancelled = true;
    };
    // `key` is the whole request; `selected` is in it, spelled out so the array identity
    // changing without its contents changing can't refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nothingPicked, catalogReady]);

  // Loading is derived, not stored: anything the CURRENT request has not been answered for
  // yet is in flight, which covers the first render and every toggle since.
  const state: Fetching<ConsensusResponse> = board?.key === key ? board : { status: "loading" };

  return (
    <div className="flex flex-col gap-4">
      {catalog.status === "loading" ? <BoardLoading /> : null}
      {catalog.status === "ready" ? (
        <SourcePanel
          sources={catalog.response.sources}
          selected={selected}
          onChange={setSelected}
          poolSize={catalog.response.pool_size}
        />
      ) : null}
      {catalog.status === "error" ? <BoardError error={catalog.error} /> : null}

      {catalog.status === "ready" && nothingPicked ? (
        <ConsensusNoSources count={catalog.response.sources.length} />
      ) : null}

      {catalogReady && !nothingPicked ? (
        <>
          {state.status === "ready" ? <ConsensusMeta response={state.response} /> : null}
          {state.status === "loading" ? <BoardLoading /> : null}
          {state.status === "error" ? <BoardError error={state.error} /> : null}
          {state.status === "ready" ? (
            state.response.players.length === 0 ? (
              <BoardNoMatches position={state.response.position} />
            ) : (
              <ConsensusTable response={state.response} />
            )
          ) : null}
        </>
      ) : null}
    </div>
  );
}
