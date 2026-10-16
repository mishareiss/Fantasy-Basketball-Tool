"use client";

import { useEffect, useState } from "react";

import { DIALOG_HEADING, DIALOG_SECTION, PlayerDialog } from "@/components/PlayerDialog";
import {
  ApiError,
  api,
  type CardPlayer,
  type StatLine,
  type TeamPlayerCardResponse,
  type WaiverInfo,
} from "@/lib/api";
import { MISSING, decimal, positions as positionList, whole } from "@/lib/format";
import { injuryBadge, matchupRange, monthDay, waiverBadge } from "@/lib/team";
import { GameCell } from "./GameCell";
import { WatchStar } from "./WatchStar";

/**
 * One player's card over the Team tab: who holds him, every stat basis side by side, and his
 * next seven days by matchup.
 *
 * The same dialog as the board's player detail (`components/PlayerDialog.tsx`): it fetches
 * itself, its loading and failure are local, and Escape closes it and puts focus back on the
 * name that opened it. `fromSp` is the first day of the seven: the roster's day when opened
 * from the roster, the first chosen day when opened from the waiver list.
 *
 * The star beside his name is the waiver list's star: optimistic, then the card is read again
 * and the page is told (`onWatchChange`) so the waiver list re-reads too. A failed write puts
 * the star back and says why, inline.
 */

type Settled =
  | { key: string; status: "ready"; card: TeamPlayerCardResponse }
  | { key: string; status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

/** 2027 -> "2026-27": the season as a basketball fan writes it. */
function seasonName(season: number): string {
  return `${season - 1}-${String(season).slice(-2)}`;
}

function ownerText(owner: CardPlayer["owner"], waiver: WaiverInfo | null): string {
  if (owner === "FREEAGENT") return "Free agent";
  if (owner === "WAIVERS") {
    const badge = waiverBadge(waiver ?? { clears_at_display: null, clears_at_known: false });
    return `Waivers · ${badge.replace(/^W · /, "")}`;
  }
  return owner.name;
}

const TH = "py-1 pr-3 text-[10px] font-semibold tracking-wide text-zinc-500 uppercase";
const NUM = "py-1 pr-3 text-right font-mono tabular-nums text-zinc-900 dark:text-zinc-100";

function StatsTable({ card }: { card: TeamPlayerCardResponse }) {
  const rows: { key: string; label: string; line: StatLine | null; empty: string }[] = [
    { key: "projected", label: "Projected", line: card.stats.projected, empty: "No projection" },
    {
      key: "season",
      label: `${seasonName(card.season)} Season`,
      line: card.stats.season,
      empty: "No games yet",
    },
    { key: "l7", label: "L7", line: card.stats.l7, empty: "No games yet" },
    { key: "l15", label: "L15", line: card.stats.l15, empty: "No games yet" },
    { key: "l30", label: "L30", line: card.stats.l30, empty: "No games yet" },
  ];
  const columns = card.stat_columns;

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs" data-card-stats>
        <caption className="sr-only">His per-game stats on every basis.</caption>
        <thead>
          <tr>
            <th scope="col" className={`${TH} text-left`}>
              <span className="sr-only">Basis</span>
            </th>
            {columns.map((column) => (
              <th key={column} scope="col" className={`${TH} text-right`}>
                {column}
              </th>
            ))}
            <th scope="col" className={`${TH} text-right`}>
              GP
            </th>
            <th scope="col" className={`${TH} text-right`}>
              FPTS/G
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, label, line, empty }) => (
            <tr
              key={key}
              data-card-row={key}
              className="border-t border-zinc-100 dark:border-zinc-900"
            >
              <th scope="row" className="py-1 pr-3 text-left font-medium text-zinc-600 dark:text-zinc-400">
                {label}
              </th>
              {line === null ? (
                <td colSpan={columns.length + 2} className="py-1 text-zinc-500 italic">
                  {empty}
                </td>
              ) : (
                <>
                  {columns.map((column) => (
                    <td key={column} className={NUM}>
                      {decimal(line.per_game[column], 1)}
                    </td>
                  ))}
                  <td className={NUM}>{whole(line.games)}</td>
                  <td className={`${NUM} font-semibold`}>{decimal(line.fantasy_ppg, 1)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Schedule({ card }: { card: TeamPlayerCardResponse }) {
  const bySp = new Map(card.schedule.map((day) => [day.sp, day]));
  const grouped = new Set(card.by_matchup.flatMap((group) => group.days));
  const outside = card.schedule.filter((day) => !grouped.has(day.sp));

  return (
    <div className="flex flex-col gap-3">
      {card.by_matchup.map((group) => (
        <div key={group.matchup_id} data-card-matchup={group.matchup_id}>
          <h4 className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            {group.label} · {matchupRange(group.start, group.end)}
            <span className="ml-2 font-normal text-zinc-500">
              {group.games} {group.games === 1 ? "game" : "games"}
            </span>
          </h4>
          <ul className="mt-1 flex flex-col">
            {group.days.map((sp) => {
              const day = bySp.get(sp);
              if (!day) return null;
              return (
                <li
                  key={sp}
                  data-card-day={sp}
                  className="flex gap-3 border-t border-zinc-100 py-1 text-xs dark:border-zinc-900"
                >
                  <span className="w-8 font-medium text-zinc-600 dark:text-zinc-400">
                    {day.weekday}
                  </span>
                  <span className="w-12 tabular-nums text-zinc-500">{monthDay(day.date)}</span>
                  <span className="text-zinc-900 dark:text-zinc-100" data-card-game>
                    {day.game ? <GameCell game={day.game} /> : MISSING}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      {outside.length > 0 ? (
        <div>
          <h4 className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            After the fantasy season
          </h4>
          <ul className="mt-1 flex flex-col">
            {outside.map((day) => (
              <li key={day.sp} className="flex gap-3 py-1 text-xs">
                <span className="w-8">{day.weekday}</span>
                <span className="w-12 tabular-nums">{monthDay(day.date)}</span>
                <span>{day.game ? <GameCell game={day.game} /> : MISSING}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function TeamPlayerCard({
  playerId,
  name,
  fromSp,
  onClose,
  onWatchChange,
}: {
  playerId: number;
  name: string;
  fromSp: number | null;
  onClose: () => void;
  /** The watchlist changed from this card: the page has the waiver list read again. */
  onWatchChange?: () => void;
}) {
  const [settled, setSettled] = useState<Settled | null>(null);
  const key = `${playerId}:${fromSp ?? ""}`;
  // The star as clicked, until the card is read back; and why the last click failed.
  const [star, setStar] = useState<{ key: string; on: boolean } | null>(null);
  const [starError, setStarError] = useState<string | null>(null);
  const [starBusy, setStarBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .teamPlayer(playerId, fromSp ?? undefined)
      .then((card) => {
        if (!cancelled) setSettled({ key, status: "ready", card });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setSettled({ key, status: "error", error: asApiError(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [playerId, fromSp, key]);

  const fresh = settled?.key === key ? settled : null;
  const card = fresh?.status === "ready" ? fresh.card : null;
  const watched = star?.key === key ? star.on : (card?.watched ?? false);

  async function toggleWatch() {
    if (card === null) return;
    const next = !watched;
    setStar({ key, on: next });
    setStarError(null);
    setStarBusy(true);
    try {
      if (next) await api.watchPlayer(playerId);
      else await api.unwatchPlayer(playerId);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setStar(null);
      setStarError(`Couldn’t ${next ? "watch" : "unwatch"} him: ${error.detail ?? error.message}`);
      setStarBusy(false);
      return;
    }
    onWatchChange?.();
    try {
      const again = await api.teamPlayer(playerId, fromSp ?? undefined);
      setSettled({ key, status: "ready", card: again });
      setStar(null);
    } catch {
      // The write landed; the optimistic star stands until the next read.
    } finally {
      setStarBusy(false);
    }
  }
  const error = fresh?.status === "error" ? fresh.error : null;
  const player = card?.player ?? null;
  const injury = injuryBadge(player?.injury_status);

  const subtitle =
    player === null
      ? "Reading his card…"
      : [
          player.nba_team ?? MISSING,
          positionList(player.positions),
          injury ? `Injury: ${injury}` : null,
          ownerText(player.owner, card?.waiver ?? null),
        ]
          .filter(Boolean)
          .join(" · ");

  return (
    <PlayerDialog
      headingId={`team-player-${playerId}`}
      title={player?.name ?? name}
      titleAction={
        card !== null ? (
          <WatchStar
            watched={watched}
            name={player?.name ?? name}
            busy={starBusy}
            onToggle={toggleWatch}
          />
        ) : null
      }
      subtitle={<span data-card-owner>{subtitle}</span>}
      closeLabel={`Close ${name}`}
      onClose={onClose}
    >
      {starError !== null ? (
        <p
          role="alert"
          data-watch-error
          className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
        >
          {starError}
        </p>
      ) : null}
      {error !== null ? (
        <p
          role="status"
          data-detail="error"
          className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
        >
          Couldn’t read his card{error.detail ? ` — ${error.detail}` : ""}. Close this and try
          again.
        </p>
      ) : card === null ? (
        <p role="status" aria-live="polite" data-detail="loading" className="text-sm text-zinc-500">
          Reading his card…
        </p>
      ) : (
        <>
          <section className={DIALOG_SECTION}>
            <h3 className={DIALOG_HEADING}>Per game</h3>
            <StatsTable card={card} />
          </section>
          <section className={DIALOG_SECTION}>
            <h3 className={DIALOG_HEADING}>Next 7 days</h3>
            <Schedule card={card} />
          </section>
        </>
      )}
    </PlayerDialog>
  );
}
