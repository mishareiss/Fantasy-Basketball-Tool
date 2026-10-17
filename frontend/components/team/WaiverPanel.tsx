"use client";

import { useEffect, useRef, useState } from "react";

import { Segment, Segmented } from "@/components/board/BoardControls";
import { FIELD, QUIET_BUTTON } from "@/components/draft/DraftStates";
import {
  ApiError,
  POSITIONS,
  api,
  type DayFilterMode,
  type FreeAgentRow,
  type FreeAgentSort,
  type FreeAgentsResponse,
  type Position,
  type TeamBasis,
  type TeamCalendarResponse,
} from "@/lib/api";
import { decimal } from "@/lib/format";
import { inPlanLabel } from "@/lib/plan";
import {
  dayLabel,
  groupDaysByMatchup,
  isTeamBasis,
  matchupDays,
  matchupRange,
  monthDay,
  toggleDay,
  waiverBadge,
  weekday,
} from "@/lib/team";
import { BasisToggle } from "./BasisToggle";
import { GameCell } from "./GameCell";
import { PlayerCell } from "./PlayerCell";
import { ProjectionTag } from "./ProjectionTag";
import { Badge } from "./TeamStates";
import { WatchStar } from "./WatchStar";

/**
 * Who I could pick up, filtered by who plays on the days I care about.
 *
 * INDEPENDENT OF THE ROSTER. It opens on the roster's matchup and day, and after that it is
 * its own: its own days, its own mode, its own basis. Changing the roster's day does not
 * re-read this list, because "who plays Thursday and Saturday" is a question about the
 * waiver wire, not about the day my lineup is on.
 *
 * THE DAYS are chips, one per day of a matchup (switch matchups to pick days in another).
 * The mode is the filter: Off keeps everyone and still draws a game cell per chosen day, so
 * you can scan who plays when; Any needs a game on one of them; All on every one. One chip
 * with Any or All is simply "plays that day".
 *
 * POSITIONS are toggle chips: any combination, sent as one comma list the server ORs (SF + PF
 * = eligible at either). None selected is every position.
 *
 * ALL | WATCHLIST replaces the old free-agent / waivers split: everyone here is available, and
 * a waiver player already carries his `W · clears …` badge. The watchlist is shared by both
 * co-managers (it lives in the database). A star toggles a row optimistically, then the list is
 * read again (the house rule: refetch after a write); a failed write puts the star back and
 * says why. The card's star does the same and tells the page, which bumps `refreshToken`.
 *
 * THE PLAN (task 31): a row that is a candidate in a planned move carries `In plan · Wed`
 * (`· backup` when he isn't the primary), and every row has `+ Plan`, which opens the move
 * composer with him as the primary pickup. The page owns the composer; this panel only says
 * who, and tells the page which days are chosen (the composer's default day is the first).
 *
 * The mode, basis and positions are remembered per browser (localStorage, guarded: a private
 * window or a blocked store just means the defaults). Nothing else is.
 */

const PAGE = 50;
const STORAGE_KEY = "team.waivers";
const DEBOUNCE_MS = 300;

const MODES: { mode: DayFilterMode; label: string; hint: string }[] = [
  { mode: "none", label: "Off", hint: "Everyone, with a game cell per chosen day" },
  { mode: "any", label: "Any", hint: "Plays on at least one chosen day" },
  { mode: "all", label: "All", hint: "Plays on every chosen day" },
];

type View = "all" | "watchlist";

const VIEWS: { view: View; label: string }[] = [
  { view: "all", label: "All" },
  { view: "watchlist", label: "Watchlist" },
];

const SORTS: { sort: FreeAgentSort; label: string }[] = [
  { sort: "fpts", label: "FPTS/G" },
  { sort: "games", label: "Games" },
  { sort: "name", label: "Name" },
];

type Remembered = { mode: DayFilterMode; basis: TeamBasis; positions: Position[] };

/** Toggle one position chip, keeping POSITIONS order so the param is stable. */
function togglePosition(selected: readonly Position[], position: Position): Position[] {
  const next = selected.includes(position)
    ? selected.filter((p) => p !== position)
    : [...selected, position];
  return POSITIONS.filter((p) => next.includes(p));
}

function remembered(): Remembered {
  const fallback: Remembered = { mode: "none", basis: "projected", positions: [] };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<Remembered>;
    const positions = Array.isArray(parsed.positions) ? parsed.positions : [];
    return {
      mode: MODES.some((m) => m.mode === parsed.mode) ? (parsed.mode as DayFilterMode) : "none",
      basis: isTeamBasis(parsed.basis) ? parsed.basis : "projected",
      positions: POSITIONS.filter((p) => positions.includes(p)),
    };
  } catch {
    return fallback;
  }
}

function remember(value: Remembered) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // A blocked store just means the defaults next time.
  }
}

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

export function WaiverPanel({
  calendar,
  initialMatchupId,
  initialDay,
  refreshToken = 0,
  onOpenPlayer,
  onPlan,
  onDaysChange,
}: {
  calendar: TeamCalendarResponse;
  initialMatchupId: number;
  initialDay: number;
  /** Bumped by the page when the watchlist changed elsewhere (the card's star): read again. */
  refreshToken?: number;
  onOpenPlayer: (player: { id: number; name: string }, fromSp: number | null) => void;
  /** `+ Plan` on a row: open the move composer with him as the primary pickup. */
  onPlan?: (row: FreeAgentRow) => void;
  /** The chosen days, whenever they change (the composer's default day is the first). */
  onDaysChange?: (days: number[]) => void;
}) {
  const [matchupId, setMatchupId] = useState(initialMatchupId);
  const [days, setDaysState] = useState<number[]>([initialDay]);

  function setDays(next: number[] | ((current: number[]) => number[])) {
    const value = typeof next === "function" ? next(days) : next;
    setDaysState(value);
    onDaysChange?.(value);
  }
  const [{ mode, basis, positions }, setRemembered] = useState<Remembered>(remembered);
  const [view, setView] = useState<View>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<FreeAgentSort>("fpts");
  const q = useDebounced(search.trim(), DEBOUNCE_MS);

  // The list on screen, tagged with the filters it answers: a slow answer about old filters
  // can't land on new ones, and "loading" is derived rather than written before each fetch.
  const [page, setPage] = useState<{ key: string; list: FreeAgentsResponse } | null>(null);
  const [failure, setFailure] = useState<{ key: string; error: ApiError } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const pending = useRef(0);

  // Star toggles not yet confirmed by a re-read, by player id, and the last one that failed.
  const [stars, setStars] = useState<Record<number, boolean>>({});
  const [starError, setStarError] = useState<string | null>(null);
  // Bumped after a star write: re-read the same filters, without the "loading" dimming.
  const [reload, setReload] = useState(0);
  // How many rows were on screen, so a re-read after a star keeps what Show more had added.
  const shown = useRef({ key: "", rows: 0 });
  // The size of the whole watchlist, for the empty-watchlist message. Null until read.
  const [watchCount, setWatchCount] = useState<number | null>(null);

  const daysKey = days.join(",");
  const positionsKey = positions.join(",");
  const watchlist = view === "watchlist";
  const requestKey = [daysKey, mode, basis, positionsKey, view, q, sort].join("|");
  const list = page?.list ?? null;
  const error = failure?.key === requestKey ? failure.error : null;
  const loading = loadingMore || (page?.key !== requestKey && error === null);

  function update(next: Partial<Remembered>) {
    setRemembered((current) => {
      const value = { ...current, ...next };
      remember(value);
      return value;
    });
  }

  function params(offset: number) {
    return {
      days: daysKey ? daysKey.split(",").map(Number) : undefined,
      mode,
      basis,
      positions,
      watchlist: watchlist || undefined,
      q: q || undefined,
      sort,
      limit: PAGE,
      offset,
    };
  }

  useEffect(() => {
    shown.current.rows = page?.list.players.length ?? 0;
  }, [page]);

  // The first page, whenever a filter changes, and the same filters again after a star write
  // (`reload`, `refreshToken`). Only the filters are dependencies: the roster's day is not one
  // of them, by design (see the header).
  useEffect(() => {
    const ticket = (pending.current += 1);
    const again = shown.current.key === requestKey;
    shown.current.key = requestKey;
    api
      .freeAgents({
        days: daysKey ? daysKey.split(",").map(Number) : undefined,
        mode,
        basis,
        positions: positionsKey ? (positionsKey.split(",") as Position[]) : [],
        watchlist: watchlist || undefined,
        q: q || undefined,
        sort,
        limit: again ? Math.max(PAGE, shown.current.rows) : PAGE,
        offset: 0,
      })
      .then((answer) => {
        if (ticket !== pending.current) return;
        setPage({ key: requestKey, list: answer });
        // A star the read now agrees with is no longer pending.
        const server = new Map(answer.players.map((row) => [row.id, row.watched]));
        setStars((current) =>
          Object.fromEntries(
            Object.entries(current).filter(([id, on]) => server.get(Number(id)) !== on),
          ),
        );
      })
      .catch((caught: unknown) => {
        if (ticket === pending.current) setFailure({ key: requestKey, error: asApiError(caught) });
      });
  }, [requestKey, daysKey, mode, basis, positionsKey, watchlist, q, sort, reload, refreshToken]);

  // The watchlist's size, only while it is the view: it tells "nobody watched yet" apart from
  // "nobody watched matches these filters" (or is on a roster now).
  useEffect(() => {
    if (!watchlist) return;
    let cancelled = false;
    api
      .watchlist()
      .then((answer) => {
        if (!cancelled) setWatchCount(answer.entries.length);
      })
      .catch(() => {
        if (!cancelled) setWatchCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, [watchlist, reload, refreshToken]);

  async function toggleWatch(row: FreeAgentRow) {
    const next = !(stars[row.id] ?? row.watched);
    setStars((current) => ({ ...current, [row.id]: next }));
    setStarError(null);
    try {
      if (next) await api.watchPlayer(row.id);
      else await api.unwatchPlayer(row.id);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setStars((current) => {
        const rest = { ...current };
        delete rest[row.id];
        return rest;
      });
      setStarError(
        `Couldn’t ${next ? "watch" : "unwatch"} ${row.name}: ${error.detail ?? error.message}`,
      );
      return;
    }
    setReload((n) => n + 1);
  }

  function showMore() {
    if (!page) return;
    const ticket = (pending.current += 1);
    const shown = page;
    setLoadingMore(true);
    api
      .freeAgents(params(shown.list.players.length))
      .then((answer) => {
        if (ticket !== pending.current) return;
        setPage({
          key: shown.key,
          list: { ...answer, players: [...shown.list.players, ...answer.players] },
        });
      })
      .catch((caught: unknown) => {
        if (ticket === pending.current) setFailure({ key: requestKey, error: asApiError(caught) });
      })
      .finally(() => setLoadingMore(false));
  }

  const matchupIndex = calendar.matchups.findIndex((m) => m.id === matchupId);
  const matchup = calendar.matchups[matchupIndex] ?? null;
  const chips = matchup ? matchupDays(calendar.days, matchup.id) : [];
  const columns = list
    ? groupDaysByMatchup(
        list.days.map((d) => d.sp),
        calendar.days,
        calendar.matchups,
      )
    : [];
  const columnDays = columns.flatMap((group) => group.days);
  const fromSp = days[0] ?? list?.days[0]?.sp ?? null;
  const oneDay =
    days.length === 1 && mode !== "none"
      ? calendar.days.find((d) => d.sp === days[0])
      : undefined;

  return (
    <section
      aria-label="Waiver list"
      data-waiver-panel
      className="flex flex-col gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">Waiver list</h2>
        <span className="text-xs text-zinc-500 tabular-nums" data-waiver-total>
          {list ? `${list.total} players` : ""}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-md border border-zinc-300 px-2 py-0.5 text-sm disabled:opacity-40 dark:border-zinc-700"
            aria-label="Previous waiver matchup"
            disabled={matchupIndex <= 0}
            onClick={() => setMatchupId(calendar.matchups[matchupIndex - 1].id)}
          >
            ‹
          </button>
          <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300" data-waiver-matchup>
            {matchup ? `${matchup.label} · ${matchupRange(matchup.start, matchup.end)}` : ""}
          </span>
          <button
            type="button"
            className="rounded-md border border-zinc-300 px-2 py-0.5 text-sm disabled:opacity-40 dark:border-zinc-700"
            aria-label="Next waiver matchup"
            disabled={matchupIndex < 0 || matchupIndex >= calendar.matchups.length - 1}
            onClick={() => setMatchupId(calendar.matchups[matchupIndex + 1].id)}
          >
            ›
          </button>
          {days.length > 0 ? (
            <button
              type="button"
              className="ml-auto text-[11px] text-zinc-500 hover:underline"
              onClick={() => setDays([])}
            >
              Clear days
            </button>
          ) : null}
        </div>
        <div role="group" aria-label="Waiver days" className="flex flex-wrap gap-1.5">
          {chips.map((d) => {
            const on = days.includes(d.sp);
            return (
              <button
                key={d.sp}
                type="button"
                aria-pressed={on}
                data-waiver-day={d.sp}
                onClick={() => setDays((current) => toggleDay(current, d.sp))}
                className={`flex min-w-[3rem] flex-col items-center rounded-md border px-1.5 py-0.5 text-[11px] ${
                  on
                    ? "border-sky-600 bg-sky-600 text-white dark:border-sky-400 dark:bg-sky-500 dark:text-zinc-950"
                    : "border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-900"
                }`}
              >
                <span className="font-medium">{weekday(d.date)}</span>
                <span className="tabular-nums">{monthDay(d.date)}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Segmented label="Plays">
          {MODES.map((m) => (
            <Segment
              key={m.mode}
              active={mode === m.mode}
              title={m.hint}
              onClick={() => update({ mode: m.mode })}
            >
              {m.label}
            </Segment>
          ))}
        </Segmented>
        {oneDay ? (
          <span className="text-[11px] text-zinc-500">Plays {dayLabel(oneDay.date)}</span>
        ) : null}
        <BasisToggle
          label="Stats"
          basis={basis}
          actualsAvailable={list?.actuals_available ?? false}
          onChange={(next) => update({ basis: next })}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">Pos</span>
          <div
            role="group"
            aria-label="Positions"
            title="Any combination; none selected is every position"
            className="flex gap-1"
          >
            {POSITIONS.map((p) => {
              const on = positions.includes(p);
              return (
                <button
                  key={p}
                  type="button"
                  aria-pressed={on}
                  data-position-chip={p}
                  onClick={() => update({ positions: togglePosition(positions, p) })}
                  className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                    on
                      ? "border-sky-600 bg-sky-600 text-white dark:border-sky-400 dark:bg-sky-500 dark:text-zinc-950"
                      : "border-zinc-300 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
                  }`}
                >
                  {p}
                </button>
              );
            })}
          </div>
        </div>
        <Segmented label="Show">
          {VIEWS.map((v) => (
            <Segment key={v.view} active={view === v.view} onClick={() => setView(v.view)}>
              {v.label}
            </Segment>
          ))}
        </Segmented>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          aria-label="Search waiver list"
          placeholder="Search by name"
          className={`${FIELD} min-w-0 flex-1 py-1 text-xs`}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <label className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
          Sort
          <select
            aria-label="Sort waiver list"
            className={`${FIELD} py-1 text-xs normal-case`}
            value={sort}
            onChange={(event) => setSort(event.target.value as FreeAgentSort)}
          >
            {SORTS.map((s) => (
              <option key={s.sort} value={s.sort}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
        >
          Couldn’t read the waiver list: {error.detail ?? error.message}
        </p>
      ) : null}
      {starError ? (
        <p
          role="alert"
          data-watch-error
          className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
        >
          {starError}
        </p>
      ) : null}

      {list === null ? (
        <p role="status" className="text-sm text-zinc-500">
          Reading the waiver list…
        </p>
      ) : (
        <div className={`overflow-x-auto ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
          <table className="w-full border-collapse text-sm" data-waiver-table>
            <caption className="sr-only">Players on no roster, with their games on the chosen days.</caption>
            <thead>
              {columns.length > 1 ? (
                <tr>
                  <th />
                  {columns.map((group) => (
                    <th
                      key={group.matchup?.id ?? "none"}
                      colSpan={group.days.length}
                      className="px-1 pt-1 text-center text-[10px] font-medium text-zinc-400"
                    >
                      {group.matchup?.label ?? ""}
                    </th>
                  ))}
                  <th colSpan={2} />
                </tr>
              ) : null}
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                <th scope="col" className="px-2 py-1.5 text-left text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">
                  Player
                </th>
                {columnDays.map((d) => (
                  <th
                    key={d.sp}
                    scope="col"
                    data-waiver-column={d.sp}
                    className="px-1 py-1.5 text-center text-[10px] font-semibold text-zinc-500"
                  >
                    {weekday(d.date)}
                    <br />
                    <span className="font-normal tabular-nums">{monthDay(d.date)}</span>
                  </th>
                ))}
                <th scope="col" className="px-2 py-1.5 text-right text-[10px] font-semibold tracking-wide text-zinc-500 uppercase" title="Games on the chosen days">
                  G
                </th>
                <th scope="col" className="px-2 py-1.5 text-right text-[10px] font-semibold tracking-wide text-zinc-500 uppercase">
                  FPTS/G
                </th>
              </tr>
            </thead>
            <tbody>
              {list.players.length === 0 ? (
                <tr>
                  <td
                    colSpan={columnDays.length + 3}
                    data-waiver-empty={watchlist && watchCount === 0 ? "watchlist" : "filters"}
                    className="px-2 py-4 text-center text-xs text-zinc-500"
                  >
                    {!watchlist
                      ? "Nobody matches these filters."
                      : watchCount === 0
                        ? "No one on your watchlist yet — star players to add them."
                        : "Nobody on your watchlist is available and matches these filters."}
                  </td>
                </tr>
              ) : (
                list.players.map((row) => (
                  <WaiverRow
                    key={row.id}
                    row={row}
                    watched={stars[row.id] ?? row.watched}
                    columnDays={columnDays.map((d) => d.sp)}
                    inPlan={inPlanLabel(row, calendar)}
                    onOpen={() => onOpenPlayer({ id: row.id, name: row.name }, fromSp)}
                    onToggleWatch={() => toggleWatch(row)}
                    onPlan={onPlan ? () => onPlan(row) : undefined}
                  />
                ))
              )}
            </tbody>
          </table>
          {list.players.length < list.total ? (
            <div className="mt-2 flex justify-center">
              <button type="button" className={QUIET_BUTTON} disabled={loading} onClick={showMore}>
                Show more ({list.players.length} of {list.total})
              </button>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function WaiverRow({
  row,
  watched,
  columnDays,
  inPlan,
  onOpen,
  onToggleWatch,
  onPlan,
}: {
  row: FreeAgentRow;
  watched: boolean;
  columnDays: number[];
  inPlan: string | null;
  onOpen: () => void;
  onToggleWatch: () => void;
  onPlan?: () => void;
}) {
  const games = new Map(row.games.map((g) => [g.sp, g.game]));
  return (
    <tr data-waiver-player={row.id} className="border-b border-zinc-100 dark:border-zinc-900">
      <td className="px-2 py-1.5">
        <div className="flex items-start gap-1.5">
          <WatchStar watched={watched} name={row.name} onToggle={onToggleWatch} />
          <PlayerCell player={row} onOpen={onOpen}>
            {row.status === "WAIVERS" ? (
              <Badge tone="amber" data-waiver-badge="true">
                {waiverBadge(row.waiver ?? { clears_at_display: null, clears_at_known: false })}
              </Badge>
            ) : null}
            {inPlan ? (
              <Badge tone="sky" data-in-plan="true">
                {inPlan}
              </Badge>
            ) : null}
          </PlayerCell>
          {onPlan ? (
            <button
              type="button"
              aria-label={`Plan a pickup of ${row.name}`}
              title="Plan a move with him as the pickup"
              onClick={onPlan}
              className="ml-auto shrink-0 rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] font-semibold whitespace-nowrap text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              + Plan
            </button>
          ) : null}
        </div>
      </td>
      {columnDays.map((sp) => {
        const game = games.get(sp) ?? null;
        return (
          <td
            key={sp}
            data-game-cell={sp}
            className="px-1 py-1.5 text-center text-[11px] text-zinc-700 dark:text-zinc-300"
          >
            <GameCell game={game} align="center" />
          </td>
        );
      })}
      <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums" data-games-in-days>
        {row.games_in_days}
      </td>
      <td
        className="px-2 py-1.5 text-right font-mono text-xs font-semibold tabular-nums text-zinc-900 dark:text-zinc-100"
        data-fa-fpts
      >
        <ProjectionTag
          source={row.stats?.projection_source}
          season={row.stats?.projection_season}
        />
        {decimal(row.stats?.fantasy_ppg, 1)}
      </td>
    </tr>
  );
}
