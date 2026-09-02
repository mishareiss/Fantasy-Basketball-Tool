"use client";

import { useEffect, useState } from "react";

import { ApiError, api, type BoardResponse } from "@/lib/api";
import { HORIZON_HINT } from "@/lib/board";
import { BoardControls, type BoardControlValues } from "./BoardControls";
import { BoardMeta } from "./BoardMeta";
import { BoardError, BoardLoading, BoardNoMatches } from "./BoardStates";
import { BoardTable } from "./BoardTable";
import { CalibrationInspector } from "./CalibrationInspector";

/**
 * The board view: controls, what the board is built from, and the ranked table.
 *
 * It owns the control values and re-fetches whenever they change, because every one of them
 * (horizon, position, limit, tiers) is a server-side parameter — filtering client-side would
 * mean the visible board and the tier structure behind it were computed from different sets.
 *
 * URL syncing is deliberately *not* done here: this component takes its starting values and
 * reports changes back, so the router lives in one place (app/page.tsx) and the board itself
 * is testable without one.
 */

export const DEFAULT_CONTROLS: BoardControlValues = {
  horizon: "dynasty",
  position: null,
  limit: 100,
  tiers: "auto",
};

type Settled =
  | { status: "ready"; response: BoardResponse }
  | { status: "error"; error: ApiError };

type Fetching = Settled | { status: "loading" };

/** Identifies the request a settled result answers, so a stale one can't be shown as fresh. */
function controlsKey({ horizon, position, limit, tiers }: BoardControlValues): string {
  return `${horizon}|${position ?? "ALL"}|${limit}|${tiers}`;
}

export function BoardView({
  initialControls = DEFAULT_CONTROLS,
  onControlsChange,
}: {
  initialControls?: BoardControlValues;
  onControlsChange?: (controls: BoardControlValues) => void;
}) {
  const [controls, setControls] = useState<BoardControlValues>(initialControls);
  const [settled, setSettled] = useState<(Settled & { key: string }) | null>(null);

  const { horizon, position, limit, tiers } = controls;
  const key = controlsKey(controls);

  useEffect(() => {
    let cancelled = false;

    api
      .board({ horizon, position, limit, tiers })
      .then((response) => {
        if (!cancelled) setSettled({ key, status: "ready", response });
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setSettled({
          key,
          status: "error",
          error: caught instanceof ApiError ? caught : new ApiError(String(caught)),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [key, horizon, position, limit, tiers]);

  // Loading is derived, not stored: anything the *current* controls have not been answered
  // for yet is in flight, which covers the first render and every toggle since without a
  // second setState racing the fetch that follows it.
  const state: Fetching = settled?.key === key ? settled : { status: "loading" };

  const update = (next: Partial<BoardControlValues>) => {
    setControls((current) => {
      const merged = { ...current, ...next };
      onControlsChange?.(merged);
      return merged;
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">Draft board</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {HORIZON_HINT[controls.horizon]}
        </p>
      </header>

      <BoardControls values={controls} onChange={update} />

      {state.status === "ready" ? <BoardMeta response={state.response} /> : null}

      <CalibrationInspector horizon={controls.horizon} />

      {state.status === "loading" ? <BoardLoading /> : null}
      {state.status === "error" ? <BoardError error={state.error} /> : null}
      {state.status === "ready" ? (
        state.response.players.length === 0 ? (
          <BoardNoMatches position={state.response.position} />
        ) : (
          <BoardTable response={state.response} />
        )
      ) : null}
    </div>
  );
}
