"use client";

import { useState } from "react";

import type { MarketLineRow, MarketPlayer } from "@/lib/api";
import { oddsInput, partialNote, validateEdit } from "@/lib/market";

/**
 * One player's card: his lines, each editable in place, and what they derive to.
 *
 * Grouped by player rather than listed as rows of a flat table, because a player is what the
 * board ranks: five props on one man are one opinion about him, and his value is a property
 * of the SET — change the assists price and the number at the top of this card moves.
 *
 * The derived value is shown with how many stats it is built from, always. A market
 * projection is partial by construction — it prices the stats a book posted and nothing else
 * — so "18.2 · 1 stat priced" is the whole truth about a player with only a points prop, and
 * the value alone would be a lie about where he belongs.
 */

const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2 py-1 text-right font-mono text-xs text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

const ACTION =
  "rounded-md px-2 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

export type LineEdit = { line: string; over: string; under: string };

function LineRow({
  player,
  line,
  onSave,
  onDelete,
  busy,
}: {
  player: MarketPlayer;
  line: MarketLineRow;
  onSave: (line: MarketLineRow, edit: LineEdit) => void;
  onDelete: (line: MarketLineRow) => void;
  busy: boolean;
}) {
  const stored: LineEdit = {
    line: String(line.line),
    over: oddsInput(line.over_odds),
    under: oddsInput(line.under_odds),
  };
  // The draft starts at what is stored. It is re-seeded by REMOUNTING rather than by an
  // effect: `PlayerLines` keys each row on the stored values, so the server's answer to a
  // save (or to someone else's edit) replaces the draft outright. A draft is a draft, never
  // a second source of truth about what the book said.
  const [edit, setEdit] = useState<LineEdit>(stored);

  const dirty =
    edit.line !== stored.line || edit.over !== stored.over || edit.under !== stored.under;
  const problem = validateEdit(edit.line, edit.over, edit.under);

  return (
    <tr className="border-t border-zinc-100 dark:border-zinc-900">
      <th scope="row" className="py-1.5 pr-3 text-left font-mono text-xs font-semibold">
        {line.stat}
      </th>
      <td className="py-1.5 pr-3">
        <input
          aria-label={`${player.name} ${line.stat} line`}
          value={edit.line}
          inputMode="decimal"
          onChange={(event) => setEdit({ ...edit, line: event.target.value })}
          className={`${FIELD} w-20`}
        />
      </td>
      <td className="py-1.5 pr-3">
        <input
          aria-label={`${player.name} ${line.stat} over odds`}
          value={edit.over}
          inputMode="numeric"
          placeholder="—"
          onChange={(event) => setEdit({ ...edit, over: event.target.value })}
          className={`${FIELD} w-20`}
        />
      </td>
      <td className="py-1.5 pr-3">
        <input
          aria-label={`${player.name} ${line.stat} under odds`}
          value={edit.under}
          inputMode="numeric"
          placeholder="—"
          onChange={(event) => setEdit({ ...edit, under: event.target.value })}
          className={`${FIELD} w-20`}
        />
      </td>
      <td className="py-1.5 pr-3 text-right">
        <button
          type="button"
          disabled={!dirty || problem !== null || busy}
          onClick={() => onSave(line, edit)}
          title={problem ?? undefined}
          className={`${ACTION} border border-zinc-300 text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900`}
        >
          Save {player.name} {line.stat}
        </button>
      </td>
      <td className="py-1.5 text-right">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDelete(line)}
          className={`${ACTION} text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40`}
        >
          Delete {player.name} {line.stat}
        </button>
      </td>
    </tr>
  );
}

export function PlayerLines({
  player,
  onSave,
  onDelete,
  onClear,
  busy,
}: {
  player: MarketPlayer;
  onSave: (line: MarketLineRow, edit: LineEdit) => void;
  onDelete: (line: MarketLineRow) => void;
  onClear: (player: MarketPlayer) => void;
  busy: boolean;
}) {
  const value = player.fantasy_points_per_game;

  return (
    <section
      aria-label={player.name}
      className="flex flex-col gap-2 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {player.name}
          </h3>
          <span className="text-xs text-zinc-500">
            {[player.nba_team, player.positions.join("/"), player.age ? `${player.age}y` : null]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm text-zinc-900 dark:text-zinc-100">
            {value === null ? "—" : value.toFixed(1)}
            <span className="ml-1 text-[11px] font-normal text-zinc-500">fpts/g</span>
          </span>
          {/* Partial by construction — see the module comment. Never hidden. */}
          <span className="text-[11px] text-zinc-500">{partialNote(player)}</span>
          <button
            type="button"
            disabled={busy}
            onClick={() => onClear(player)}
            className={`${ACTION} text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900`}
          >
            Clear {player.name}
          </button>
        </div>
      </header>

      <table className="w-full text-sm">
        <caption className="sr-only">{player.name}&rsquo;s stored lines</caption>
        <thead>
          <tr className="text-[11px] tracking-wide text-zinc-500 uppercase">
            <th scope="col" className="pb-1 pr-3 text-left font-medium">
              Stat
            </th>
            <th scope="col" className="pb-1 pr-3 text-left font-medium">
              Line
            </th>
            <th scope="col" className="pb-1 pr-3 text-left font-medium">
              Over
            </th>
            <th scope="col" className="pb-1 pr-3 text-left font-medium">
              Under
            </th>
            <th scope="col" className="pb-1 pr-3" />
            <th scope="col" className="pb-1" />
          </tr>
        </thead>
        <tbody>
          {player.lines.map((line) => (
            <LineRow
              // Keyed on the VALUES, not just the id: a save that changes the number gives a
              // new key, which remounts the row over the server's answer. See `LineRow`.
              key={`${line.id}:${line.line}:${line.over_odds}:${line.under_odds}`}
              player={player}
              line={line}
              onSave={onSave}
              onDelete={onDelete}
              busy={busy}
            />
          ))}
        </tbody>
      </table>
    </section>
  );
}
