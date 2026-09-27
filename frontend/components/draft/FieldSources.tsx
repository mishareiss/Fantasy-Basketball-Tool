"use client";

import { useEffect, useState } from "react";

import { Segment, Segmented } from "@/components/board/BoardControls";
import { ApiError, HORIZONS, api, type Horizon, type SourceInfo } from "@/lib/api";
import { HORIZON_LABEL } from "@/lib/board";

/**
 * Whose board the OTHER nine seats are assumed to be reading.
 *
 * The single most consequential setting in the draft room, and the one that used to be a
 * paragraph saying there was nothing to set. Every availability percentage on this page is a
 * statement about a simulated room, and the room is only as good as the guess about what it
 * drafts off: a league where everybody visibly reaches off ESPN's ADP is modelled by ticking
 * `adp:espn` alone, and one where the same dynasty list gets passed around is modelled by
 * ticking that. The default — every source, equally weighted — is the honest guess about nine
 * strangers, and it is what "all selected" means, so it is what the form starts at.
 *
 * ALL SELECTED IS NOT A SELECTION. Ticking every box sends no `field_source_ids` at all, and
 * the backend reads absent as "every source the horizon offers". That matters beyond
 * tidiness: a stored list of today's source ids would pin the draft to the sources that
 * happened to exist when it started, so a list imported tomorrow would be silently left out
 * of a room that asked for everybody.
 *
 * THE HORIZON DECIDES WHAT IS ON OFFER, not just how the numbers read. A rank-only list
 * declares itself dynasty or redraft at import and appears under one horizon only, so
 * flipping the toggle genuinely changes the catalog — which is why the list is re-fetched
 * rather than filtered client-side.
 *
 * Shared between the setup form and the in-room panel deliberately: choosing the field before
 * the draft starts and correcting it before the first pick are the same decision, and the
 * backend gives them the same rule (`PUT /draft/config` accepts both only while the draft is
 * empty, exactly as it does the seat).
 */

type Settled =
  | { horizon: Horizon; status: "ready"; sources: SourceInfo[] }
  | { horizon: Horizon; status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

const KIND_LABEL: Record<string, string> = {
  projection: "projection",
  adp: "ADP",
  ranking: "ranking",
};

/** What a selection means, in one sentence — the thing printed under the boxes. */
export function fieldSummary(selected: string[], available: SourceInfo[]): string {
  if (available.length === 0) return "No sources are stored yet — the room will draft off nothing.";
  if (selected.length === 0) {
    return "Nothing ticked. Tick at least one, or tick them all for the default.";
  }
  if (selected.length === available.length) {
    return `The room drafts off the consensus of all ${available.length} sources, equally weighted — the best guess at what nine strangers collectively believe.`;
  }
  const names = available
    .filter((source) => selected.includes(source.id))
    .map((source) => source.label);
  return `The room drafts off the consensus of ${names.join(", ")} — ${selected.length} of ${available.length} sources.`;
}

export function FieldSources({
  horizon,
  onHorizon,
  selected,
  onSelected,
  isDisabled = false,
  legend = "The field drafts off",
}: {
  horizon: Horizon;
  onHorizon: (next: Horizon) => void;
  /** The ticked ids. Null means "everything the catalog offers", which is also the default. */
  selected: string[] | null;
  onSelected: (next: string[] | null) => void;
  isDisabled?: boolean;
  legend?: string;
}) {
  /**
   * The catalog, tagged with the horizon it is for — the request-key guard this codebase uses
   * everywhere. Tagged rather than cleared on every change, so a catalog for a horizon that is
   * no longer selected is dropped rather than rendered under the other one's toggle, and
   * "loading" is derived rather than written.
   */
  const [settled, setSettled] = useState<Settled | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .sources(horizon)
      .then((body) => {
        if (!cancelled) setSettled({ horizon, status: "ready", sources: body.sources });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setSettled({ horizon, status: "error", error: asApiError(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [horizon]);

  const fresh = settled?.horizon === horizon ? settled : null;
  const sources = fresh?.status === "ready" ? fresh.sources : null;
  const error = fresh?.status === "error" ? fresh.error : null;

  const available = sources ?? [];
  // Null means all, and "all" is resolved against the catalog actually on offer — so flipping
  // the horizon does not leave a redraft list ticked under the dynasty one.
  const ticked =
    selected === null
      ? available.map((source) => source.id)
      : available.filter((source) => selected.includes(source.id)).map((source) => source.id);

  function toggle(id: string) {
    const next = ticked.includes(id)
      ? ticked.filter((current) => current !== id)
      : [...ticked, id];
    // Every box ticked is stored back as null, so the two ways of saying "all of them" cannot
    // drift apart inside this component either.
    onSelected(next.length === available.length ? null : next);
  }

  return (
    <fieldset className="flex flex-col gap-3" data-field-sources>
      <legend className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
        {legend}
      </legend>

      <Segmented label="Horizon">
        {HORIZONS.map((option) => (
          <Segment
            key={option}
            active={horizon === option}
            onClick={() => {
              if (isDisabled) return;
              // Back to the default when the catalog changes underneath: a subset of dynasty
              // source ids means nothing under the win-now catalog, and silently keeping the
              // ones that happen to exist in both would be a selection nobody made.
              onSelected(null);
              onHorizon(option);
            }}
            title={
              option === "dynasty"
                ? "The room is assumed to be drafting for the long run."
                : "The room is assumed to be drafting to win this season."
            }
          >
            {HORIZON_LABEL[option]}
          </Segment>
        ))}
      </Segmented>

      {error !== null ? (
        <p
          role="status"
          data-field-sources="error"
          className="rounded-md bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400"
        >
          Couldn’t read the source list{error.detail ? ` — ${error.detail}` : ""}. Start the
          draft anyway and the room drafts off every source there is, which is the default.
        </p>
      ) : sources === null ? (
        <p role="status" aria-live="polite" className="text-xs text-zinc-500">
          Reading the sources…
        </p>
      ) : available.length === 0 ? (
        <p className="text-xs text-zinc-500">
          Nothing is stored under this horizon yet. Sync the league or import a list, and the
          sources appear here as chips.
        </p>
      ) : (
        <div className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
          {available.map((source) => (
            <label
              key={source.id}
              data-source={source.id}
              className="flex items-baseline gap-2 text-xs text-zinc-600 dark:text-zinc-400"
            >
              <input
                type="checkbox"
                checked={ticked.includes(source.id)}
                disabled={isDisabled}
                onChange={() => toggle(source.id)}
                aria-label={source.label}
                className="mt-0.5 shrink-0 accent-zinc-900 dark:accent-zinc-100"
              />
              <span className="flex flex-wrap items-baseline gap-x-1.5">
                <span className="font-medium text-zinc-800 dark:text-zinc-200">
                  {source.label}
                </span>
                <span className="text-zinc-500">
                  {KIND_LABEL[source.kind] ?? source.kind} ·{" "}
                  <span className="font-mono tabular-nums">{source.player_count}</span> players
                </span>
              </span>
            </label>
          ))}
        </div>
      )}

      {sources === null ? null : (
        <p className="text-xs text-zinc-500" data-field-summary>
          {fieldSummary(ticked, available)}
        </p>
      )}
    </fieldset>
  );
}
