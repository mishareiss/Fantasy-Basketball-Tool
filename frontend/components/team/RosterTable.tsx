"use client";

import { useEffect, useState } from "react";

import { PRIMARY_BUTTON, QUIET_BUTTON } from "@/components/draft/DraftStates";
import type { LineupAssignment, TeamDayResponse, TeamSlotRow } from "@/lib/api";
import { decimal } from "@/lib/format";
import { buildMove, legalTargets, lineupSourceLabel } from "@/lib/team";
import { GameCell } from "./GameCell";
import { PlayerCell } from "./PlayerCell";
import { Badge } from "./TeamStates";

/**
 * My roster on one day, in ESPN's slot grid, and the ESPN-style move.
 *
 * EVERY SLOT IS A ROW, empty ones included, in the order the server sends them: an open UT
 * spot is the most important thing on the page on a busy night, and it is invisible if empty
 * slots aren't drawn. A player with no game that day is dimmed, as ESPN dims him.
 *
 * THE MOVE is ESPN's: Move on a player lights up every row he can legally go to (an empty slot
 * he's eligible for, or a player who could take his slot in a swap; the bench always), and a
 * click on one sends the WHOLE lineup (`lib/team.ts: buildMove`). Nothing on screen changes
 * until the server has the lineup and the page has read the day back: no optimistic grid.
 */

const TH = "px-1.5 py-1.5 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase";
const TD = "px-1.5 py-1.5 align-middle";
const NUM = `${TD} text-right font-mono tabular-nums`;

export function RosterHeader({
  day,
  syncing,
  onSync,
}: {
  day: TeamDayResponse;
  syncing: boolean;
  onSync: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const manual = day.lineup_source === "manual";
  const { used, limit } = day.acquisitions;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2" data-roster-header>
      <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{day.team.name}</h2>
      <Badge tone={manual ? "amber" : "zinc"} data-lineup-source={day.lineup_source}>
        {lineupSourceLabel(day.lineup_source)}
      </Badge>
      <span className="text-xs text-zinc-500 tabular-nums" data-acquisitions>
        Acquisitions {used} / {limit ?? "—"} this matchup
      </span>
      {/* Task 31's Actual | Plan toggle goes here. */}
      <div className="ml-auto flex items-center gap-2">
        {confirming ? (
          <div
            data-sync-confirm
            className="flex flex-wrap items-center gap-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"
          >
            <span>This day has edits made here. ESPN’s lineup will replace them.</span>
            <button
              type="button"
              className={PRIMARY_BUTTON}
              disabled={syncing}
              onClick={() => {
                setConfirming(false);
                onSync();
              }}
            >
              Replace with ESPN
            </button>
            <button type="button" className={QUIET_BUTTON} onClick={() => setConfirming(false)}>
              Keep my edits
            </button>
          </div>
        ) : (
          <button
            type="button"
            className={QUIET_BUTTON}
            disabled={syncing}
            onClick={() => (manual ? setConfirming(true) : onSync())}
          >
            {syncing ? "Syncing…" : "Sync lineup from ESPN"}
          </button>
        )}
      </div>
    </div>
  );
}

export function RosterTable({
  day,
  busy,
  onOpenPlayer,
  onMove,
}: {
  day: TeamDayResponse;
  busy: boolean;
  onOpenPlayer: (player: { id: number; name: string }) => void;
  onMove: (assignments: LineupAssignment[]) => void;
}) {
  // The row being moved, tagged with its day so a move never survives a change of day.
  const [moving, setMoving] = useState<{ sp: number; index: number } | null>(null);
  const from = moving && moving.sp === day.sp ? moving.index : null;
  const targets = from === null ? new Set<number>() : legalTargets(day.slots, from);

  useEffect(() => {
    if (from === null) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMoving(null);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [from]);

  function moveTo(index: number) {
    if (from === null) return;
    const assignments = buildMove(day.slots, from, index);
    setMoving(null);
    onMove(assignments);
  }

  const columns = day.stat_columns;

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm" data-roster-table>
        <caption className="sr-only">
          My roster on {day.date}, one row per lineup slot, empty slots included.
        </caption>
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th scope="col" className={`${TH} text-left`}>
              Slot
            </th>
            <th scope="col" className={TH}>
              <span className="sr-only">Move</span>
            </th>
            <th scope="col" className={`${TH} text-left`}>
              Player
            </th>
            <th scope="col" className={`${TH} w-16 text-left`}>
              Game
            </th>
            {columns.map((column) => (
              <th key={column} scope="col" className={`${TH} text-right`}>
                {column}
              </th>
            ))}
            <th scope="col" className={`${TH} text-right`}>
              FPTS/G
            </th>
          </tr>
        </thead>
        <tbody>
          {day.slots.map((row, index) => (
            <SlotRowView
              key={`${row.slot_id}-${row.player?.id ?? `empty-${index}`}`}
              row={row}
              columns={columns}
              isMover={from === index}
              isTarget={targets.has(index)}
              moveMode={from !== null}
              busy={busy}
              onMove={() =>
                setMoving(from === index ? null : { sp: day.sp, index })
              }
              onHere={() => moveTo(index)}
              onOpenPlayer={onOpenPlayer}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SlotRowView({
  row,
  columns,
  isMover,
  isTarget,
  moveMode,
  busy,
  onMove,
  onHere,
  onOpenPlayer,
}: {
  row: TeamSlotRow;
  columns: string[];
  isMover: boolean;
  isTarget: boolean;
  moveMode: boolean;
  busy: boolean;
  onMove: () => void;
  onHere: () => void;
  onOpenPlayer: (player: { id: number; name: string }) => void;
}) {
  const player = row.player;
  const noGame = player !== null && player.game === null;
  const tone = isMover
    ? "bg-amber-50 dark:bg-amber-500/10"
    : isTarget
      ? "bg-sky-50 dark:bg-sky-500/10"
      : "";

  let action: React.ReactNode = null;
  if (isTarget) {
    action = (
      <button
        type="button"
        data-move-target
        disabled={busy}
        onClick={onHere}
        className="rounded border border-sky-400 px-2 py-0.5 text-[11px] font-semibold text-sky-800 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-600 dark:text-sky-200 dark:hover:bg-sky-500/20"
      >
        Here
      </button>
    );
  } else if (player && (!moveMode || isMover)) {
    action = (
      <button
        type="button"
        aria-pressed={isMover}
        aria-label={`Move ${player.name}`}
        title={isMover ? "Click again (or Escape) to cancel" : undefined}
        disabled={busy}
        onClick={onMove}
        className={`rounded border px-2 py-0.5 text-[11px] font-semibold disabled:opacity-50 ${
          isMover
            ? "border-amber-500 bg-amber-100 text-amber-900 dark:border-amber-500 dark:bg-amber-500/20 dark:text-amber-100"
            : "border-zinc-300 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
        }`}
      >
        Move
      </button>
    );
  }

  return (
    <tr
      data-slot={row.slot}
      data-slot-player={player?.id ?? ""}
      data-no-game={noGame ? "true" : undefined}
      data-target={isTarget ? "true" : undefined}
      className={`border-b border-zinc-100 dark:border-zinc-900 ${tone}`}
    >
      <td className={`${TD} w-10 font-mono text-xs font-semibold text-zinc-500`}>{row.slot}</td>
      <td className={`${TD} w-12`}>{action}</td>
      {player === null ? (
        <td className={`${TD} text-xs text-zinc-400 italic`} colSpan={3 + columns.length}>
          Empty
        </td>
      ) : (
        <>
          <td className={`${TD} ${noGame ? "opacity-45" : ""}`}>
            <PlayerCell
              player={player}
              onOpen={() => onOpenPlayer({ id: player.id, name: player.name })}
            />
          </td>
          <td
            className={`${TD} w-16 text-xs text-zinc-700 dark:text-zinc-300 ${noGame ? "opacity-45" : ""}`}
            data-game
          >
            <GameCell game={player.game} />
          </td>
          {columns.map((column) => (
            <td key={column} className={`${NUM} ${noGame ? "opacity-45" : ""}`}>
              {decimal(player.stats?.per_game[column], 1)}
            </td>
          ))}
          <td
            className={`${NUM} font-semibold text-zinc-900 dark:text-zinc-100 ${noGame ? "opacity-45" : ""}`}
            data-fpts
          >
            {decimal(player.stats?.fantasy_ppg, 1)}
          </td>
        </>
      )}
    </tr>
  );
}
