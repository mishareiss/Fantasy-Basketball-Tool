"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  ApiError,
  api,
  type ImportRowOutcome,
  type MarketLineRow,
  type MarketLinesResponse,
  type MarketPlayer,
} from "@/lib/api";
import { KIND_MARKET_LINE } from "@/lib/importing";
import {
  EMPTY_ADD,
  MARKET_SOURCE,
  RESOLVE_DELIMITER,
  type AddForm,
  parseOdds,
  resolveText,
} from "@/lib/market";
import { AddLineForm } from "./AddLineForm";
import { MarketEmpty, MarketFailed, MarketLoading, NoScoringRules } from "./MarketStates";
import { PlayerLines, type LineEdit } from "./PlayerLines";

/**
 * The market page: see the lines a book has posted, add one, move one, take one away.
 *
 * A page of its own rather than a mode on /import, and that is the one design decision here
 * worth arguing about. Every other source is a FILE: imported, replaced by next week's file,
 * never edited. A player's props are a standing SET — five numbers kept by hand, one of which
 * moves on a Tuesday because a book repriced assists and nothing else. An importer has no
 * verb for "change this one number", and none at all for "this prop came off the board":
 * pasting a shorter file leaves what it doesn't mention exactly where it was. So the bulk
 * paste stays at /import (market_line is a first-class kind there), and this is where the set
 * is kept right afterwards.
 *
 * Three things it deliberately does NOT own:
 *
 * * **The name matcher.** Adding a line resolves the player through the importer's own dry
 *   run, candidates and all, and a miss is fixed with the same alias call. One matcher.
 * * **The pricing.** Every value on this page is the backend's derived `Projection` read
 *   back after the write — never a number computed here from odds. The de-vig lives in one
 *   place (`app.ranking.market`) and the browser is not it.
 * * **The consequences of a delete.** Removing a player's last line removes his market
 *   projection server-side, which is what makes him leave the consensus board. The page just
 *   says so.
 */

type Settled =
  | { status: "ready"; response: MarketLinesResponse }
  | { status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

const BUTTON =
  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

/** Which book, and which season. Applied on submit, so typing a source doesn't refetch. */
function MarketControls({
  source,
  season,
  onApply,
  busy,
}: {
  source: string;
  season: string;
  onApply: (next: { source: string; season: string }) => void;
  busy: boolean;
}) {
  const [draftSource, setDraftSource] = useState(source);
  const [draftSeason, setDraftSeason] = useState(season);

  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        onApply({ source: draftSource.trim() || MARKET_SOURCE, season: draftSeason.trim() });
      }}
    >
      <div className="flex flex-col gap-1">
        <label
          htmlFor="market-source"
          className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
        >
          Book
        </label>
        <input
          id="market-source"
          value={draftSource}
          onChange={(event) => setDraftSource(event.target.value)}
          placeholder={MARKET_SOURCE}
          className={`${FIELD} w-40`}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label
          htmlFor="market-season"
          className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
        >
          Season
        </label>
        <input
          id="market-season"
          value={draftSeason}
          inputMode="numeric"
          onChange={(event) => setDraftSeason(event.target.value)}
          placeholder="ESPN_SEASON"
          className={`${FIELD} w-32`}
        />
      </div>
      <button
        type="submit"
        disabled={busy}
        className={`${BUTTON} border border-zinc-300 text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900`}
      >
        Show lines
      </button>
      <p className="max-w-md text-[11px] text-zinc-500">
        One book per source: keep a single hand-aggregated set under{" "}
        <span className="font-mono">market</span>, or enter two books as two sources and read
        them side by side on the{" "}
        <Link href="/" className="underline">
          consensus board
        </Link>
        .
      </p>
    </form>
  );
}

export function MarketPage() {
  const [query, setQuery] = useState({ source: MARKET_SOURCE, season: "" });
  // Keyed by the request it answers, the same guard the board uses: a result for the book
  // you were looking at a moment ago must read as "loading", never as this book's lines.
  const [settled, setSettled] = useState<(Settled & { key: string }) | null>(null);
  const [form, setForm] = useState<AddForm>(EMPTY_ADD);
  const [busy, setBusy] = useState<string | null>(null);
  const [unresolved, setUnresolved] = useState<ImportRowOutcome | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const season = query.season.trim() && Number.isFinite(Number(query.season))
    ? Number(query.season)
    : undefined;

  const key = `${query.source}|${season ?? ""}`;

  /**
   * One fetch, in one place: the effect below. A write doesn't re-read the list itself, it
   * bumps `reload` and lets the same effect do it — so there is exactly one code path that
   * turns (book, season) into what is on screen, and the derived values shown after an edit
   * are the server's, never a guess patched into local state.
   */
  useEffect(() => {
    let cancelled = false;
    api
      .marketLines({ source: query.source, season })
      .then((response) => {
        if (!cancelled) setSettled({ key, status: "ready", response });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setSettled({ key, status: "error", error: asApiError(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [key, query.source, season, reload]);

  const refresh = () => setReload((count) => count + 1);

  /** Write one line and re-read the list, so the value shown is always the derived one. */
  const write = useCallback(
    async (playerId: number, values: { stat: string; line: string; over: string; under: string }) => {
      const response = await api.putMarketLine({
        source: query.source,
        season,
        player_id: playerId,
        stat: values.stat,
        line: Number(values.line),
        over_odds: parseOdds(values.over) ?? null,
        under_odds: parseOdds(values.under) ?? null,
      });
      const value = response.player.fantasy_points_per_game;
      setNote(
        `${response.player.name} ${response.line.stat} ${response.created ? "added" : "updated"}` +
          (value === null ? "" : ` — now ${value.toFixed(1)} fpts/g`),
      );
      refresh();
      return response;
    },
    [query.source, season],
  );

  /**
   * Add: resolve the name through the importer's dry run, then write.
   *
   * The dry run is what gives this page a matcher, a confidence and a candidate list without
   * owning any of them — and, because `market_line` accepts only certain matches, a fuzzy hit
   * comes back for confirmation here exactly as it would in a paste.
   */
  async function add() {
    setBusy("add");
    setFailure(null);
    setUnresolved(null);
    setNote(null);
    try {
      const preview = await api.importPreview(KIND_MARKET_LINE, {
        source: query.source,
        season,
        text: resolveText(form),
        delimiter: RESOLVE_DELIMITER,
      });
      const row = preview.rows[0];
      if (row?.status === "matched" && row.player_id !== null) {
        await write(row.player_id, form);
        setForm({ ...EMPTY_ADD, stat: form.stat });
      } else if (row) {
        setUnresolved(row);
      } else {
        setFailure(new ApiError("Nothing parsed out of that row — check the player's name."));
      }
    } catch (caught: unknown) {
      setFailure(asApiError(caught));
    } finally {
      setBusy(null);
    }
  }

  /** "That one": record the alias so it never has to be asked again, then write the line. */
  async function pick(playerId: number) {
    if (!unresolved) return;
    setBusy("pick");
    setFailure(null);
    try {
      await api.addPlayerAlias(playerId, {
        source: query.source,
        source_name: unresolved.source_name,
      });
      await write(playerId, form);
      setUnresolved(null);
      setForm({ ...EMPTY_ADD, stat: form.stat });
    } catch (caught: unknown) {
      setFailure(asApiError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function save(line: MarketLineRow, edit: LineEdit) {
    setBusy(`save-${line.id}`);
    setFailure(null);
    try {
      await write(line.player_id, { stat: line.stat, ...edit });
    } catch (caught: unknown) {
      setFailure(asApiError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function remove(player: MarketPlayer, line: MarketLineRow) {
    const last = player.lines.length === 1;
    const confirmed = window.confirm(
      last
        ? `Delete ${player.name}'s ${line.stat} line? It is his last one, so he leaves the ` +
            `${query.source} source and the consensus board with it.`
        : `Delete ${player.name}'s ${line.stat} line?`,
    );
    if (!confirmed) return;

    setBusy(`delete-${line.id}`);
    setFailure(null);
    try {
      const response = await api.deleteMarketLine(line.id);
      setNote(
        response.player_removed
          ? `${player.name} has no lines left — removed from ${query.source}.`
          : `${player.name} ${line.stat} deleted.`,
      );
      refresh();
    } catch (caught: unknown) {
      setFailure(asApiError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function clear(player: MarketPlayer) {
    const confirmed = window.confirm(
      `Clear all ${player.lines.length} of ${player.name}'s lines? He leaves the ` +
        `${query.source} source.`,
    );
    if (!confirmed) return;

    setBusy(`clear-${player.espn_player_id}`);
    setFailure(null);
    try {
      await api.clearMarketPlayer({
        source: query.source,
        season,
        player_id: player.espn_player_id,
      });
      setNote(`${player.name} removed from ${query.source}.`);
      refresh();
    } catch (caught: unknown) {
      setFailure(asApiError(caught));
    } finally {
      setBusy(null);
    }
  }

  const fresh = settled?.key === key ? settled : null;
  const response = fresh?.status === "ready" ? fresh.response : null;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">Market lines</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Season-long props, kept by hand. Each line is de-vigged into a fair per-game number
          and scored under our own rules, so a player&rsquo;s lines become one market
          projection — the <span className="font-mono">projection:{query.source}</span> column
          on the consensus board. Built from the stats a book posted and nothing else, so read
          the value next to how many stats it is priced on.
        </p>
      </header>

      <MarketControls
        source={query.source}
        season={query.season}
        onApply={setQuery}
        busy={busy !== null}
      />

      {response && response.stats.length === 0 ? <NoScoringRules /> : null}

      {response && response.stats.length > 0 ? (
        <AddLineForm
          stats={response.stats}
          form={form}
          onChange={(next) => setForm((current) => ({ ...current, ...next }))}
          onSubmit={() => void add()}
          pending={busy === "add"}
          unresolved={unresolved}
          onPick={(playerId) => void pick(playerId)}
          picking={busy === "pick"}
        />
      ) : null}

      {failure ? <MarketFailed error={failure} /> : null}

      {fresh === null ? <MarketLoading label="Reading the stored lines…" /> : null}
      {fresh?.status === "error" ? <MarketFailed error={fresh.error} /> : null}

      {response ? (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Stored lines
            </h2>
            {/* What the last write did, wherever it came from — an add, an edit, a delete.
                It outlives the form being cleared on purpose: "now 88.8 fpts/g" is the
                answer to what you just did, and the form is already asking the next question. */}
            {note ? (
              <p role="status" className="text-xs text-zinc-500">
                {note}
              </p>
            ) : null}
            <span className="font-mono text-xs text-zinc-500">
              {response.total_lines} line{response.total_lines === 1 ? "" : "s"} ·{" "}
              {response.total_players} player{response.total_players === 1 ? "" : "s"} ·{" "}
              {response.source}, season {response.season}
            </span>
          </div>

          {response.players.length === 0 ? (
            <MarketEmpty source={response.source} season={response.season} />
          ) : (
            <div className="flex flex-col gap-3">
              {response.players.map((player) => (
                <PlayerLines
                  key={player.espn_player_id}
                  player={player}
                  onSave={(line, edit) => void save(line, edit)}
                  onDelete={(line) => void remove(player, line)}
                  onClear={(each) => void clear(each)}
                  busy={busy !== null}
                />
              ))}
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
