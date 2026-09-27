"use client";

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  type DetailMarketLine,
  type MarketProjectionLine,
  type PlayerDetailResponse,
  type SeasonLine,
} from "@/lib/api";
import { MISSING, decimal, positions as positionList, whole } from "@/lib/format";
import {
  BOX_SCORE,
  americanOdds,
  seasonSummary,
  shootingSplits,
  statValue,
} from "@/lib/playerdetail";

/**
 * One player's evidence, over the board: the season he actually played, what the market
 * thinks, and the props that opinion was built out of.
 *
 * WHY THIS IS A DIALOG AND NOT A PAGE. The question it answers is asked mid-scan — you are
 * three rows into a tier arguing with yourself about a centre, and "twenty-two a game off
 * what?" is a two-second detour, not a navigation. So it opens over the board, the board stays
 * exactly where it was underneath, and Escape puts you back on the row you came from.
 *
 * IT FETCHES ITSELF. The board read carries one number per production column and nothing else,
 * deliberately — a thousand rows do not need thirty stats each. So the dialog asks for the
 * detail when it opens, which makes it the one place on this page with its own loading and its
 * own failure, and both are LOCAL: a detail fetch that fails is a sentence inside the dialog,
 * never the page's error panel, because the board behind it is still perfectly good.
 *
 * NOTHING IS INVENTED. Every value here is present-or-absent (`lib/playerdetail.ts`): a stat
 * the source published no number for renders as an em dash, and the two shooting percentages
 * exist only where both halves of them do. That is the entire reason a stat line is worth
 * putting in front of somebody.
 */

const TILE =
  "flex flex-col items-center rounded-md bg-zinc-100 px-2 py-1.5 dark:bg-zinc-900";
const TILE_LABEL = "text-[10px] font-medium tracking-wide text-zinc-500 uppercase";
const TILE_VALUE = "font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-100";
const SECTION = "flex flex-col gap-2";
const HEADING = "text-[11px] font-semibold tracking-wide text-zinc-500 uppercase";

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className={TILE} data-stat={label}>
      <span className={TILE_LABEL}>{label}</span>
      <span className={TILE_VALUE}>{value}</span>
    </div>
  );
}

/**
 * The counting stats and the two derived rates, as one grid.
 *
 * The rates sit at the end rather than beside their pairs, because FGM and FGA are what we
 * hold and FG% is what we worked out — keeping the arithmetic downstream of its inputs is the
 * same argument the backend makes for not storing a percentage at all.
 */
function BoxScore({ perGame }: { perGame: Record<string, number> }) {
  return (
    <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-6 lg:grid-cols-8">
      {BOX_SCORE.map(({ key, label, digits }) => (
        <Tile
          key={key}
          label={label}
          value={
            digits === 0 ? whole(statValue(perGame, key)) : decimal(statValue(perGame, key), 1)
          }
        />
      ))}
      {shootingSplits(perGame).map(({ label, value }) => (
        <Tile
          key={label}
          label={label}
          // Derived, so it is only ever as precise as one decimal deserves: 52.4%.
          value={value === null ? MISSING : `${value.toFixed(1)}%`}
        />
      ))}
    </div>
  );
}

function LastSeason({ season }: { season: SeasonLine }) {
  return (
    <section className={SECTION} data-detail="last-season">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className={HEADING}>Last season · {seasonSummary(season)}</h3>
        <p className="font-mono text-xs text-zinc-500 tabular-nums">
          <span className="text-zinc-900 dark:text-zinc-100" data-detail="last-season-ppg">
            {decimal(season.fantasy_ppg, 1)}
          </span>{" "}
          FP/g · {decimal(season.fantasy_total, 0)} total
        </p>
      </div>
      <BoxScore perGame={season.per_game} />
      <p className="text-xs text-zinc-500">
        What he actually did, priced under <strong className="font-semibold">our</strong>{" "}
        scoring — so it is directly comparable to the projection the board ranks by, not to
        whatever his old league paid him.
      </p>
    </section>
  );
}

function Market({
  market,
  lines,
}: {
  market: MarketProjectionLine | null;
  lines: DetailMarketLine[];
}) {
  if (market === null && lines.length === 0) {
    return (
      <section className={SECTION} data-detail="market-empty">
        <h3 className={HEADING}>Market</h3>
        <p className="text-xs text-zinc-500">
          Nobody has posted a season-long prop on him. Enter one on the{" "}
          <span className="font-medium">Market</span> page and he gets a market projection like
          everybody else.
        </p>
      </section>
    );
  }

  return (
    <section className={SECTION} data-detail="market">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className={HEADING}>Market projection</h3>
        <p className="font-mono text-xs text-zinc-500 tabular-nums">
          <span className="text-zinc-900 dark:text-zinc-100" data-detail="market-ppg">
            {decimal(market?.fantasy_ppg, 1)}
          </span>{" "}
          FP/g · {decimal(market?.fantasy_total, 0)} total
          {market?.games === null || market?.games === undefined
            ? ""
            : ` · ${whole(market.games)} GP`}
        </p>
      </div>

      {market === null ? null : <BoxScore perGame={market.per_game} />}

      {lines.length === 0 ? null : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <caption className="sr-only">
              The sportsbook lines this projection was derived from, per game, with the
              American odds on each side.
            </caption>
            <thead>
              <tr className="text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">
                <th scope="col" className="py-1 pr-3 text-left">
                  Stat
                </th>
                <th scope="col" className="py-1 pr-3 text-right">
                  Line
                </th>
                <th scope="col" className="py-1 pr-3 text-right">
                  Over
                </th>
                <th scope="col" className="py-1 text-right">
                  Under
                </th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr
                  key={line.stat}
                  data-market-line={line.stat}
                  className="border-t border-zinc-100 dark:border-zinc-900"
                >
                  <td className="py-1 pr-3 text-zinc-600 dark:text-zinc-400">{line.stat}</td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums text-zinc-900 dark:text-zinc-100">
                    {decimal(line.line, 1)}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums text-zinc-500">
                    {americanOdds(line.over_odds)}
                  </td>
                  <td className="py-1 text-right font-mono tabular-nums text-zinc-500">
                    {americanOdds(line.under_odds)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-zinc-500">
        Built from the stats that happen to be priced, so it is{" "}
        <strong className="font-semibold">partial</strong>: a player with one prop is valued on
        that prop alone and reads low. A reference, never a ranking.
      </p>
    </section>
  );
}

type Settled =
  | { id: number; status: "ready"; detail: PlayerDetailResponse }
  | { id: number; status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

/**
 * The dialog itself.
 *
 * `player` is the row that was clicked — its id and its name, so the heading is right before
 * the fetch lands and the thing you clicked is never a spinner with no label on it. Everything
 * below the heading waits for the response.
 */
export function PlayerDetail({
  playerId,
  name,
  onClose,
}: {
  playerId: number;
  name: string;
  onClose: () => void;
}) {
  /**
   * The answer, tagged with the player it answers about — this page's request-key guard, the
   * same shape `MasterBoardPage` and `ConsensusView` use.
   *
   * Tagged rather than cleared, because clicking a second name while the first one's dialog is
   * open reuses this component: a response that arrives for a player who is no longer the open
   * one must be dropped, and "loading" has to be derivable from the state rather than written
   * into it before the fetch starts.
   */
  const [settled, setSettled] = useState<Settled | null>(null);
  const dialog = useRef<HTMLDivElement | null>(null);
  // Where focus was when this opened, so closing puts it back on the row rather than at the
  // top of a thousand-row table.
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .playerDetail(playerId)
      .then((body) => {
        if (!cancelled) setSettled({ id: playerId, status: "ready", detail: body });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setSettled({ id: playerId, status: "error", error: asApiError(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  const fresh = settled?.id === playerId ? settled : null;
  const detail = fresh?.status === "ready" ? fresh.detail : null;
  const error = fresh?.status === "error" ? fresh.error : null;

  useEffect(() => {
    opener.current = document.activeElement;
    dialog.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Restoring focus is the half of "dismissable" that is easy to forget and the only half
      // a keyboard notices.
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  const heading = `player-detail-${playerId}`;

  return (
    <div
      // The backdrop. A click on it closes; a click inside the panel does not, which is what
      // the `stopPropagation` on the panel below is for.
      onClick={onClose}
      data-detail-backdrop
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-zinc-900/40 p-4 pt-10 backdrop-blur-sm"
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={heading}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="flex w-full max-w-2xl flex-col gap-5 rounded-lg border border-zinc-200 bg-white p-5 shadow-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 dark:border-zinc-800 dark:bg-zinc-950"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-0.5">
            <h2
              id={heading}
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              {detail?.name ?? name}
            </h2>
            <p className="text-xs text-zinc-500">
              {detail === null
                ? "Reading his numbers…"
                : `${positionList(detail.positions)} · ${whole(detail.age)} · ${detail.nba_team ?? MISSING}`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${name}`}
            className="rounded-md px-2 py-1 text-sm text-zinc-500 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 dark:hover:bg-zinc-900"
          >
            Close
          </button>
        </div>

        {error !== null ? (
          // Local, and small: the board behind this is fine, so this is a sentence rather than
          // the page's failure panel.
          <p
            role="status"
            data-detail="error"
            className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
          >
            Couldn’t read his numbers{error.detail ? ` — ${error.detail}` : ""}. The board is
            unaffected; close this and try again.
          </p>
        ) : detail === null ? (
          <p role="status" aria-live="polite" data-detail="loading" className="text-sm text-zinc-500">
            Reading his numbers…
          </p>
        ) : (
          <>
            {detail.last_season === null ? (
              <section className={SECTION} data-detail="no-last-season">
                <h3 className={HEADING}>Last season</h3>
                <p className="text-xs text-zinc-500">
                  He has never completed a season we hold — a rookie, or a player ESPN has no
                  finished split for. Not a zero: there is no line to show, which is a
                  different thing from a bad one.
                </p>
              </section>
            ) : (
              <LastSeason season={detail.last_season} />
            )}

            <Market market={detail.market} lines={detail.market_lines} />
          </>
        )}
      </div>
    </div>
  );
}
