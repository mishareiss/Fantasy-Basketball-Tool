"use client";

import { useState } from "react";

import { FIELD, PRIMARY_BUTTON, QUIET_BUTTON } from "@/components/draft/DraftStates";
import {
  ApiError,
  api,
  type PlanAcquisitions,
  type PlanMove,
  type PlanResponse,
  type PlanWarning,
  type TeamCalendarResponse,
} from "@/lib/api";
import {
  canReorder,
  dropProblem,
  groupMovesByMatchup,
  moveNumbers,
  moveSentence,
  reorderWithinDay,
  splitWarnings,
  statusChip,
} from "@/lib/plan";
import { matchupRange } from "@/lib/team";
import { AcquisitionMeter } from "./AcquisitionMeter";
import { PlanWarningLine } from "./PlanRosterExtras";
import { Badge } from "./TeamStates";

/**
 * The whole waiver plan: moves grouped by matchup (each with its acquisitions and an override
 * of ESPN's adds-used count), in plan order inside, with every warning.
 *
 * EVERY WRITE IS FOLLOWED BY A RE-READ (docs/HANDOFF.md): a row action calls the API and then
 * `onChanged`, which has the page read the plan, the day and the waiver list again. Nothing on
 * screen changes until that read lands. NO BROWSER DIALOGS: a delete that other moves depend on
 * asks inline, and so does Mark done (which candidate was added).
 */

export type PlanNotice = { moveId: number | null; text: string; warnings: PlanWarning[] };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

function reasonOf(caught: unknown): string {
  const error = asApiError(caught);
  return error.details?.join("; ") ?? error.detail ?? error.message;
}

export function PlanTab({
  plan,
  error,
  calendar,
  notice,
  onDismissNotice,
  onAdd,
  onEdit,
  onChanged,
}: {
  plan: PlanResponse | null;
  error: ApiError | null;
  calendar: TeamCalendarResponse;
  notice: PlanNotice | null;
  onDismissNotice: () => void;
  onAdd: () => void;
  onEdit: (move: PlanMove) => void;
  /** After any write: re-read the plan, the day and the waiver list. */
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      onChanged();
      return true;
    } catch (caught: unknown) {
      setFailure(reasonOf(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (plan === null) {
    return (
      <section aria-label="Waiver plan" data-plan-tab className={PANEL}>
        {error ? (
          <p role="alert" className={ALERT}>
            Couldn’t read the plan: {error.detail ?? error.message}
          </p>
        ) : (
          <p role="status" className="text-sm text-zinc-500">
            Reading the plan…
          </p>
        )}
      </section>
    );
  }

  const numbers = moveNumbers(plan.moves);
  const groups = groupMovesByMatchup(plan.moves, plan.acquisitions, calendar.matchups);
  const { byMove, general } = splitWarnings(plan.warnings);

  return (
    <section aria-label="Waiver plan" data-plan-tab className={PANEL}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">Waiver plan</h2>
        <button type="button" className={QUIET_BUTTON} onClick={onAdd}>
          Add move
        </button>
      </div>

      {notice ? (
        <div
          role="status"
          data-plan-notice
          className="flex flex-col gap-1 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900 dark:border-sky-900 dark:bg-sky-500/10 dark:text-sky-100"
        >
          <div className="flex items-start justify-between gap-2">
            <span className="font-medium">{notice.text}</span>
            <button type="button" className="text-[11px] hover:underline" onClick={onDismissNotice}>
              Dismiss
            </button>
          </div>
          {notice.warnings.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {notice.warnings.map((w, i) => (
                <PlanWarningLine key={`${w.code}-${i}`} warning={w} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {failure ? (
        <p role="alert" data-plan-error className={ALERT}>
          {failure}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className={ALERT}>
          Couldn’t read the plan again: {error.detail ?? error.message}
        </p>
      ) : null}

      {general.length > 0 ? (
        <ul className="flex flex-col gap-1" data-plan-general-warnings>
          {general.map((w, i) => (
            <PlanWarningLine key={`${w.code}-${w.matchup_id ?? "x"}-${i}`} warning={w} />
          ))}
        </ul>
      ) : null}

      {plan.moves.length === 0 ? (
        <p className="text-xs text-zinc-500" data-plan-empty>
          No moves planned. Use <span className="font-semibold">+ Plan</span> on a waiver row, or
          Add move.
        </p>
      ) : null}

      {groups.map((group) => {
        const matchup = calendar.matchups.find((m) => m.id === group.matchupId);
        return (
          <div
            key={group.matchupId ?? "none"}
            data-plan-matchup={group.matchupId ?? "none"}
            className="flex flex-col gap-2"
          >
            <div className="flex flex-wrap items-baseline gap-x-2 border-b border-zinc-200 pb-1 dark:border-zinc-800">
              <h3 className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">{group.label}</h3>
              {matchup ? (
                <span className="text-[11px] text-zinc-500">{matchupRange(matchup.start, matchup.end)}</span>
              ) : null}
            </div>
            {group.acquisition ? (
              <MatchupAcquisitionRow
                row={group.acquisition}
                busy={busy}
                onOverride={(used) =>
                  run(() => api.setAcquisitionOverride(group.acquisition!.matchup_id, used))
                }
              />
            ) : null}
            {group.moves.length > 0 ? (
              <ol className="flex flex-col gap-1.5">
                {group.moves.map((move) => (
                  <PlanMoveRow
                    key={move.id}
                    move={move}
                    number={numbers.get(move.id) ?? 0}
                    numbers={numbers}
                    moves={plan.moves}
                    warnings={byMove.get(move.id) ?? []}
                    busy={busy}
                    onEdit={() => onEdit(move)}
                    run={run}
                  />
                ))}
              </ol>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

const PANEL =
  "flex flex-col gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950";
const ALERT =
  "rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200";
const ROW_BUTTON =
  "rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] font-semibold text-zinc-600 hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900";

/** One matchup's adds: used (ESPN's or an override) + planned against the limit. */
export function MatchupAcquisitionRow({
  row,
  busy,
  onOverride,
}: {
  row: PlanAcquisitions;
  busy: boolean;
  onOverride: (used: number | null) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(row.used));
  const parsed = Number(value);
  const valid = value.trim() !== "" && Number.isInteger(parsed) && parsed >= 0;

  return (
    <div data-acquisition-row={row.matchup_id} className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <AcquisitionMeter acquisitions={row} />
        {editing ? null : (
          <button
            type="button"
            className="text-[11px] text-sky-700 hover:underline dark:text-sky-300"
            onClick={() => {
              setValue(String(row.used));
              setEditing(true);
            }}
          >
            Override used…
          </button>
        )}
        {row.used_source === "override" ? (
          <button
            type="button"
            disabled={busy}
            className="text-[11px] text-zinc-600 hover:underline disabled:opacity-50 dark:text-zinc-400"
            onClick={() => onOverride(null)}
          >
            Clear override
          </button>
        ) : null}
      </div>
      {editing ? (
        <form
          className="flex flex-wrap items-center gap-2 rounded-md bg-zinc-50 px-2 py-1.5 text-xs dark:bg-zinc-900"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!valid) return;
            if (await onOverride(parsed)) setEditing(false);
          }}
        >
          <label className="flex items-center gap-1.5">
            Adds used in {row.label}
            <input
              type="number"
              min={0}
              step={1}
              className={`${FIELD} w-16 py-0.5 text-xs`}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <button type="submit" className={PRIMARY_BUTTON} disabled={busy || !valid}>
            Set override
          </button>
          <button type="button" className={QUIET_BUTTON} onClick={() => setEditing(false)}>
            Cancel
          </button>
          <span className="w-full text-[11px] text-zinc-500">
            This replaces ESPN’s count for this matchup until you clear it.
          </span>
        </form>
      ) : null}
    </div>
  );
}

/** One move: its sentence, chip, warnings, and actions. */
export function PlanMoveRow({
  move,
  number,
  numbers,
  moves,
  warnings,
  busy,
  onEdit,
  run,
}: {
  move: PlanMove;
  number: number;
  numbers: ReadonlyMap<number, number>;
  moves: readonly PlanMove[];
  warnings: readonly PlanWarning[];
  busy: boolean;
  onEdit: () => void;
  run: (action: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [marking, setMarking] = useState(false);
  const [dependents, setDependents] = useState<number[] | null>(null);
  const chip = statusChip(move);
  const problem = dropProblem(move);
  const candidates = [...move.candidates].sort((a, b) => a.rank - b.rank);

  async function reorder(delta: -1 | 1) {
    const changes = reorderWithinDay(moves, move.id, delta);
    await run(async () => {
      for (const change of changes) {
        await api.updatePlanMove(change.id, { sequence: change.sequence });
      }
    });
  }

  async function remove(repoint: boolean) {
    setDependents(null);
    // Set inside the action when the server refuses with the moves that depend on this one.
    const outcome: { blocked: number[] | null } = { blocked: null };
    await run(async () => {
      try {
        await api.deletePlanMove(move.id, repoint);
      } catch (caught: unknown) {
        const error = asApiError(caught);
        const ids = error.data?.dependent_move_ids;
        if (error.status === 409 && !repoint && Array.isArray(ids)) {
          outcome.blocked = ids.filter((id): id is number => typeof id === "number");
          return;
        }
        throw caught;
      }
    });
    if (outcome.blocked) setDependents(outcome.blocked);
  }

  const dependentText = (ids: number[]) => {
    const names = ids.map((id) => `Move ${numbers.get(id) ?? `#${id}`}`);
    const verb = ids.length === 1 ? "drops" : "drop";
    return `${names.join(", ")} ${verb} this move’s pickup. Repoint ${ids.length === 1 ? "it" : "them"} to a named drop and delete?`;
  };

  return (
    <li
      data-plan-move={move.id}
      data-status={chip.kind}
      className="flex flex-col gap-1 rounded-md border border-zinc-200 px-2.5 py-2 dark:border-zinc-800"
    >
      <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
        <span className="font-mono text-[11px] font-semibold text-zinc-500">#{number}</span>
        <span className="min-w-0 flex-1 text-xs text-zinc-800 dark:text-zinc-200" data-move-sentence>
          {moveSentence(move, numbers)}
        </span>
        <Badge tone={chip.tone} data-status-chip={chip.kind}>
          {chip.label}
        </Badge>
      </div>
      {problem ? (
        <p className="text-[11px] text-rose-700 dark:text-rose-300" data-chain-problem={move.drop.problem ?? ""}>
          Broken chain: {problem}.
        </p>
      ) : null}
      {move.note ? <p className="text-[11px] text-zinc-500 italic">{move.note}</p> : null}
      {warnings.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {warnings.map((w, i) => (
            <PlanWarningLine key={`${w.code}-${i}`} warning={w} />
          ))}
        </ul>
      ) : null}

      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className={ROW_BUTTON} disabled={busy} onClick={onEdit}>
          Edit
        </button>
        <button
          type="button"
          className={ROW_BUTTON}
          aria-label={`Move ${number} earlier`}
          disabled={busy || !canReorder(moves, move.id, -1)}
          onClick={() => reorder(-1)}
        >
          ▲
        </button>
        <button
          type="button"
          className={ROW_BUTTON}
          aria-label={`Move ${number} later`}
          disabled={busy || !canReorder(moves, move.id, 1)}
          onClick={() => reorder(1)}
        >
          ▼
        </button>
        {move.status === "planned" ? (
          <>
            <button
              type="button"
              className={ROW_BUTTON}
              aria-pressed={marking}
              disabled={busy}
              onClick={() => setMarking((on) => !on)}
            >
              Mark done
            </button>
            <button
              type="button"
              className={ROW_BUTTON}
              disabled={busy}
              onClick={() => run(() => api.updatePlanMove(move.id, { status: "skipped" }))}
            >
              Skip
            </button>
          </>
        ) : (
          <button
            type="button"
            className={ROW_BUTTON}
            disabled={busy}
            onClick={() =>
              run(() =>
                api.updatePlanMove(move.id, { status: "planned", resolved_player_id: null }),
              )
            }
          >
            Restore to planned
          </button>
        )}
        <button
          type="button"
          className={`${ROW_BUTTON} ml-auto`}
          disabled={busy}
          onClick={() => remove(false)}
        >
          Delete
        </button>
      </div>

      {marking && move.status === "planned" ? (
        <div
          data-mark-done
          className="flex flex-wrap items-center gap-1.5 rounded-md bg-zinc-50 px-2 py-1.5 text-xs dark:bg-zinc-900"
        >
          <span className="text-zinc-600 dark:text-zinc-400">Who was added?</span>
          {candidates.map((c) => (
            <button
              key={c.player.id}
              type="button"
              className={ROW_BUTTON}
              disabled={busy}
              onClick={async () => {
                const ok = await run(() =>
                  api.updatePlanMove(move.id, { status: "done", resolved_player_id: c.player.id }),
                );
                if (ok) setMarking(false);
              }}
            >
              {c.player.name}
              {c.rank > 1 ? " (backup)" : ""}
            </button>
          ))}
          <button type="button" className="text-[11px] hover:underline" onClick={() => setMarking(false)}>
            Cancel
          </button>
        </div>
      ) : null}

      {dependents ? (
        <div
          role="alert"
          data-delete-prompt
          className="flex flex-wrap items-center gap-2 rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"
        >
          <span>{dependentText(dependents)}</span>
          <button type="button" className={PRIMARY_BUTTON} disabled={busy} onClick={() => remove(true)}>
            Repoint and delete
          </button>
          <button type="button" className={QUIET_BUTTON} onClick={() => setDependents(null)}>
            Keep it
          </button>
        </div>
      ) : null}
    </li>
  );
}
