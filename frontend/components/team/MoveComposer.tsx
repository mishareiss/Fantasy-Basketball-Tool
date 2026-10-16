"use client";

import { useEffect, useRef, useState } from "react";

import { FIELD, PRIMARY_BUTTON, QUIET_BUTTON } from "@/components/draft/DraftStates";
import { DIALOG_HEADING, DIALOG_SECTION, PlayerDialog } from "@/components/PlayerDialog";
import {
  ApiError,
  api,
  type FreeAgentRow,
  type PlanMove,
  type PlanMutationResponse,
  type TeamCalendarResponse,
  type TeamDayResponse,
  type WatchlistEntry,
} from "@/lib/api";
import { decimal } from "@/lib/format";
import {
  addCandidate,
  createBody,
  draftProblems,
  dropKey,
  dropOptions,
  moveCandidate,
  moveNumbers,
  patchBody,
  removeCandidate,
  type Candidate,
  type ComposerDraft,
  type DropChoice,
} from "@/lib/plan";
import { dayLabel } from "@/lib/team";
import { GameCell } from "./GameCell";

/**
 * Plan one move, or change one: the day, who goes (a player on the PLAN roster that day, or
 * "the pickup from move N", which follows whichever of that move's candidates ends up added),
 * the pickups in rank order (the primary, then the backups), and a note.
 *
 * The drop list is read from `GET /team/day?view=plan` for the chosen day, so it is the roster
 * as the plan leaves it on that day, not as it is now. Pickups come from a search over the
 * free agents (on the chosen day, so each shows his game that day) or the watchlist.
 *
 * A 422 is a malformed move and its reasons show here; league rules never block a save (they
 * come back as warnings, which the Plan tab shows).
 */

const DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 8;

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

function asCandidate(row: {
  id: number;
  name: string;
  nba_team: string | null;
  positions: string[];
  injury_status: string | null;
}): Candidate {
  return {
    id: row.id,
    name: row.name,
    nba_team: row.nba_team,
    positions: row.positions,
    injury_status: row.injury_status,
  };
}

export function MoveComposer({
  calendar,
  initial,
  moves,
  onClose,
  onSaved,
}: {
  calendar: TeamCalendarResponse;
  initial: ComposerDraft;
  /** The plan's moves, for the chain options. */
  moves: readonly PlanMove[];
  onClose: () => void;
  onSaved: (answer: PlanMutationResponse) => void;
}) {
  const [draft, setDraft] = useState<ComposerDraft>(initial);
  const [errors, setErrors] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const sp = draft.sp;
  const numbers = moveNumbers(moves);

  // The plan roster on the chosen day, tagged with its day.
  const [planDay, setPlanDay] = useState<{ sp: number; day: TeamDayResponse } | null>(null);
  const [planDayError, setPlanDayError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .teamDay({ sp, view: "plan" })
      .then((day) => {
        if (!cancelled) {
          setPlanDay({ sp, day });
          setPlanDayError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) setPlanDayError(asApiError(caught).detail ?? asApiError(caught).message);
      });
    return () => {
      cancelled = true;
    };
  }, [sp]);
  const roster = planDay?.sp === sp ? planDay.day : null;
  const options = dropOptions(roster, moves, sp, draft.moveId);
  const chosenKey = draft.drop ? dropKey(draft.drop) : null;
  const chosenMissing =
    draft.drop !== null && roster !== null && !options.some((o) => o.key === chosenKey);

  // The search, on the chosen day.
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), DEBOUNCE_MS);
  const [results, setResults] = useState<{ key: string; rows: FreeAgentRow[] } | null>(null);
  const searchKey = `${sp}|${q}`;
  useEffect(() => {
    if (!q) return;
    let cancelled = false;
    api
      .freeAgents({ days: [sp], q, limit: SEARCH_LIMIT })
      .then((answer) => {
        if (!cancelled) setResults({ key: `${sp}|${q}`, rows: answer.players });
      })
      .catch(() => {
        if (!cancelled) setResults({ key: `${sp}|${q}`, rows: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [sp, q]);
  const shownResults = q && results?.key === searchKey ? results.rows : [];

  // The watchlist's available players, read once.
  const [watchlist, setWatchlist] = useState<WatchlistEntry[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .watchlist()
      .then((answer) => {
        if (!cancelled) setWatchlist(answer.entries);
      })
      .catch(() => {
        if (!cancelled) setWatchlist([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const watchable = (watchlist ?? []).filter(
    (entry) =>
      typeof entry.owner === "string" && !draft.pickups.some((p) => p.id === entry.player_id),
  );

  // Each pickup's game and FPTS/G on the chosen day, by `${sp}:${id}`. A search result brings
  // its own; anyone else (a prefilled or watchlist pickup, or any after a change of day) is
  // looked up by name on that day. Null: he isn't in the pool (already rostered, say).
  const [info, setInfo] = useState<Record<string, FreeAgentRow | null>>({});
  const asked = useRef(new Set<string>());
  useEffect(() => {
    for (const pickup of draft.pickups) {
      const key = `${sp}:${pickup.id}`;
      if (key in info || asked.current.has(key)) continue;
      asked.current.add(key);
      api
        .freeAgents({ days: [sp], q: pickup.name, limit: 10 })
        .then((answer) => {
          const row = answer.players.find((p) => p.id === pickup.id) ?? null;
          setInfo((current) => ({ ...current, [key]: row }));
        })
        .catch(() => setInfo((current) => ({ ...current, [key]: null })));
    }
  }, [draft.pickups, sp, info]);

  function update(next: Partial<ComposerDraft>) {
    setDraft((current) => ({ ...current, ...next }));
    setErrors(null);
  }

  function add(player: Candidate, row?: FreeAgentRow) {
    if (row) setInfo((current) => ({ ...current, [`${sp}:${player.id}`]: row }));
    setDraft((current) => ({ ...current, pickups: addCandidate(current.pickups, player) }));
    setSearch("");
    setErrors(null);
  }

  async function save() {
    const problems = draftProblems(draft);
    if (problems.length > 0 || draft.drop === null) {
      setErrors(problems);
      return;
    }
    const ready = { ...draft, drop: draft.drop };
    setSaving(true);
    setErrors(null);
    try {
      const answer =
        draft.moveId === null
          ? await api.createPlanMove(createBody(ready))
          : await api.updatePlanMove(draft.moveId, patchBody(ready));
      onSaved(answer);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setErrors(error.details ?? [error.detail ?? error.message]);
      setSaving(false);
    }
  }

  const editingNumber = draft.moveId === null ? null : numbers.get(draft.moveId);
  const title = draft.moveId === null ? "Plan a move" : `Edit move ${editingNumber ?? ""}`.trim();
  const day = calendar.days.find((d) => d.sp === sp);

  return (
    <PlayerDialog
      headingId="move-composer-title"
      title={title}
      subtitle={day ? `${dayLabel(day.date)} · drop one, add the first of the pickups who clears` : ""}
      closeLabel="Close the move composer"
      onClose={onClose}
    >
      <div data-move-composer className="flex flex-col gap-5">
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <span className={DIALOG_HEADING}>Day</span>
          <select
            aria-label="Move day"
            className={`${FIELD} py-1 text-sm`}
            value={sp}
            onChange={(event) => update({ sp: Number(event.target.value) })}
          >
            {calendar.days.map((d) => {
              const matchup = calendar.matchups.find((m) => m.id === d.matchup_id);
              return (
                <option key={d.sp} value={d.sp}>
                  {dayLabel(d.date)}
                  {matchup ? ` · ${matchup.label}` : ""}
                </option>
              );
            })}
          </select>
        </label>

        <fieldset className={DIALOG_SECTION}>
          <legend className={`${DIALOG_HEADING} mb-1`}>Drop</legend>
          {planDayError ? (
            <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">
              Couldn’t read the plan roster for that day: {planDayError}
            </p>
          ) : roster === null ? (
            <p role="status" className="text-xs text-zinc-500">
              Reading the plan roster…
            </p>
          ) : (
            <div className="flex flex-col gap-0.5" role="radiogroup" aria-label="Drop">
              {options.map((option) => (
                <label
                  key={option.key}
                  data-drop-option={option.key}
                  className="flex items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-900"
                >
                  <input
                    type="radio"
                    name="drop"
                    checked={chosenKey === option.key}
                    onChange={() => update({ drop: option.choice })}
                  />
                  {option.slot ? (
                    <span className="w-8 font-mono text-[11px] font-semibold text-zinc-500">{option.slot}</span>
                  ) : (
                    <span className="w-8 text-[11px] text-sky-700 dark:text-sky-300">chain</span>
                  )}
                  <span className="min-w-0 flex-1 text-zinc-900 dark:text-zinc-100">{option.label}</span>
                  {option.player ? (
                    <span className="w-20 text-[11px] text-zinc-600 dark:text-zinc-400">
                      {option.player.game ? <GameCell game={option.player.game} /> : "no game"}
                    </span>
                  ) : null}
                </label>
              ))}
              {chosenMissing && draft.drop ? (
                <p className="text-[11px] text-amber-800 dark:text-amber-200" data-drop-missing>
                  {missingDropText(draft.drop, numbers)}
                </p>
              ) : null}
            </div>
          )}
        </fieldset>

        <div className={DIALOG_SECTION}>
          <h3 className={DIALOG_HEADING}>Pickups</h3>
          {draft.pickups.length === 0 ? (
            <p className="text-xs text-zinc-500">
              Nobody yet. The first is the primary; the rest are backups, in order.
            </p>
          ) : (
            <ol className="flex flex-col gap-1" data-pickups>
              {draft.pickups.map((pickup, index) => {
                const row = info[`${sp}:${pickup.id}`];
                const game = row?.games.find((g) => g.sp === sp)?.game ?? null;
                return (
                  <li
                    key={pickup.id}
                    data-pickup={pickup.id}
                    className="flex items-center gap-2 rounded border border-zinc-200 px-2 py-1 text-sm dark:border-zinc-800"
                  >
                    <span className="w-16 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">
                      {index === 0 ? "Primary" : `Backup ${index}`}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-zinc-900 dark:text-zinc-100">{pickup.name}</span>
                    <span className="w-20 text-[11px] text-zinc-600 dark:text-zinc-400" data-pickup-game>
                      {row === undefined ? "" : game ? <GameCell game={game} /> : "no game"}
                    </span>
                    <span className="w-10 text-right font-mono text-xs tabular-nums" data-pickup-fpts>
                      {decimal(row?.stats?.fantasy_ppg, 1)}
                    </span>
                    <button
                      type="button"
                      aria-label={`Move ${pickup.name} up`}
                      disabled={index === 0}
                      className="rounded px-1 text-xs disabled:opacity-30"
                      onClick={() => update({ pickups: moveCandidate(draft.pickups, index, -1) })}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      aria-label={`Move ${pickup.name} down`}
                      disabled={index === draft.pickups.length - 1}
                      className="rounded px-1 text-xs disabled:opacity-30"
                      onClick={() => update({ pickups: moveCandidate(draft.pickups, index, 1) })}
                    >
                      ▼
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove ${pickup.name}`}
                      className="rounded px-1 text-xs text-zinc-500 hover:text-rose-700"
                      onClick={() => update({ pickups: removeCandidate(draft.pickups, index) })}
                    >
                      ✕
                    </button>
                  </li>
                );
              })}
            </ol>
          )}

          <input
            type="search"
            aria-label="Search free agents"
            placeholder="Add a pickup: search by name"
            className={`${FIELD} py-1 text-xs`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {shownResults.length > 0 ? (
            <ul className="flex flex-col gap-0.5" data-search-results>
              {shownResults.map((row) => {
                const listed = draft.pickups.some((p) => p.id === row.id);
                const game = row.games.find((g) => g.sp === sp)?.game ?? null;
                return (
                  <li key={row.id} className="flex items-center gap-2 px-1 text-xs">
                    <span className="min-w-0 flex-1 truncate">{row.name}</span>
                    <span className="w-20 text-[11px] text-zinc-600 dark:text-zinc-400">
                      {game ? <GameCell game={game} /> : "no game"}
                    </span>
                    <span className="w-10 text-right font-mono tabular-nums">
                      {decimal(row.stats?.fantasy_ppg, 1)}
                    </span>
                    <button
                      type="button"
                      aria-label={`Add ${row.name}`}
                      disabled={listed}
                      className="rounded border border-zinc-300 px-1.5 text-[11px] font-semibold disabled:opacity-40 dark:border-zinc-700"
                      onClick={() => add(asCandidate(row), row)}
                    >
                      {listed ? "Added" : "Add"}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {watchable.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5" data-watchlist-quick-add>
              <span className="text-[11px] text-zinc-500">Add from watchlist:</span>
              {watchable.slice(0, 8).map((entry) => (
                <button
                  key={entry.player_id}
                  type="button"
                  aria-label={`Add ${entry.name} from watchlist`}
                  className="rounded-full border border-zinc-300 px-2 py-0.5 text-[11px] hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
                  onClick={() =>
                    add(
                      asCandidate({
                        id: entry.player_id,
                        name: entry.name,
                        nba_team: entry.nba_team,
                        positions: entry.positions,
                        injury_status: entry.injury_status,
                      }),
                    )
                  }
                >
                  + {entry.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <label className="flex flex-col gap-1">
          <span className={DIALOG_HEADING}>Note</span>
          <input
            type="text"
            aria-label="Move note"
            maxLength={500}
            className={`${FIELD} py-1 text-sm`}
            placeholder="Optional"
            value={draft.note}
            onChange={(event) => update({ note: event.target.value })}
          />
        </label>

        {errors && errors.length > 0 ? (
          <div
            role="alert"
            data-composer-errors
            className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
          >
            <p className="font-medium">That move wasn’t saved:</p>
            <ul className="mt-1 list-disc pl-4">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-2">
          <button type="button" className={QUIET_BUTTON} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={PRIMARY_BUTTON} disabled={saving} onClick={save}>
            {saving ? "Saving…" : draft.moveId === null ? "Save move" : "Save changes"}
          </button>
        </div>
      </div>
    </PlayerDialog>
  );
}

function missingDropText(drop: DropChoice, numbers: ReadonlyMap<number, number>): string {
  return drop.kind === "player"
    ? `${drop.name} isn’t on the plan roster that day. Saving keeps him as the drop (the plan will warn).`
    : `Move ${numbers.get(drop.moveId) ?? drop.moveId}’s pickup doesn’t land by that day. Saving keeps the chain (the plan will warn).`;
}
