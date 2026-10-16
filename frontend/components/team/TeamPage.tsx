"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  type LineupAssignment,
  type TeamBasis,
  type TeamCalendarResponse,
  type TeamDayResponse,
} from "@/lib/api";
import { clampDay, isTeamBasis, matchupOf, syncRange, teamHref } from "@/lib/team";
import { BasisToggle } from "./BasisToggle";
import { DayPicker } from "./DayPicker";
import { RosterHeader, RosterTable } from "./RosterTable";
import { NoTeamSynced, TeamFailed, TeamLoading } from "./TeamStates";
import { TeamPlayerCard } from "./TeamPlayerCard";
import { WaiverPanel } from "./WaiverPanel";

/**
 * The in-season Team tab: my roster on one day on the left, the waiver list on the right.
 *
 * TWO READS AND TWO OWNERS. The calendar is read once, on mount: every day of the season, its
 * matchup and how many of mine play, which is everything the day picker needs. The day is read
 * whenever the roster's day or basis changes, and after every write. The page owns the
 * roster's day and basis (in the URL, so a refresh or a shared link keeps them); the waiver
 * panel owns its own filters and never hears about the roster's day after it opens.
 *
 * AFTER ANY WRITE, READ THE DAY BACK (docs/HANDOFF.md). A lineup save answers with the new day,
 * and the page ignores that answer except for its warnings: it re-reads `GET /team/day`, so
 * what is on screen is always the read path's view of what was stored, not the write's.
 *
 * A 409 IS A STATE. The Team API answers 409 until a league sync has stored a team marked as
 * ours, and the page says "run the sync" rather than showing a failure.
 */

type DaySettled =
  | { key: string; status: "ready"; day: TeamDayResponse }
  | { key: string; status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

export function TeamPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawDay = Number(searchParams.get("day"));
  const rawBasis = searchParams.get("basis");

  return (
    <TeamView
      initialDay={Number.isFinite(rawDay) && rawDay > 0 ? rawDay : null}
      initialBasis={isTeamBasis(rawBasis) ? rawBasis : "projected"}
      onUrlChange={(href) => router.replace(href, { scroll: false })}
    />
  );
}

export function TeamView({
  initialDay,
  initialBasis,
  onUrlChange,
}: {
  initialDay: number | null;
  initialBasis: TeamBasis;
  onUrlChange: (href: string) => void;
}) {
  const [calendar, setCalendar] = useState<TeamCalendarResponse | null>(null);
  const [calendarError, setCalendarError] = useState<ApiError | null>(null);
  const [chosenDay, setChosenDay] = useState<number | null>(initialDay);
  const [basis, setBasis] = useState<TeamBasis>(initialBasis);
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<DaySettled | null>(null);

  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [errors, setErrors] = useState<string[] | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [through, setThrough] = useState(false);
  const [card, setCard] = useState<{ id: number; name: string; fromSp: number | null } | null>(
    null,
  );
  // Bumped when the card's star changes the watchlist, so the waiver list reads it again.
  const [watchVersion, setWatchVersion] = useState(0);
  // The waiver panel opens on the roster's matchup and day, then goes its own way: captured
  // once, the first time the page knows which day the roster is on.
  const [waiverStart, setWaiverStart] = useState<{ matchupId: number; day: number } | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    api
      .teamCalendar()
      .then((answer) => {
        if (!cancelled) setCalendar(answer);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setCalendarError(asApiError(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sp = calendar
    ? clampDay(chosenDay ?? calendar.effective_period, calendar.final_period)
    : null;
  const matchup = calendar && sp !== null ? matchupOf(calendar.matchups, sp) : null;

  if (calendar && sp !== null && waiverStart === null && matchup) {
    // Set during render, once: React's documented way to derive state from a first value.
    setWaiverStart({ matchupId: matchup.id, day: sp });
  }

  const dayKey = sp === null ? null : `${sp}|${basis}|${version}`;
  const pending = useRef(0);

  useEffect(() => {
    if (dayKey === null || sp === null) return;
    const ticket = (pending.current += 1);
    api
      .teamDay({ sp, basis })
      .then((day) => {
        if (ticket === pending.current) setSettled({ key: dayKey, status: "ready", day });
      })
      .catch((caught: unknown) => {
        if (ticket === pending.current)
          setSettled({ key: dayKey, status: "error", error: asApiError(caught) });
      });
  }, [dayKey, sp, basis]);

  // The last good day stays on screen (dimmed) while the next one loads.
  const [shown, setShown] = useState<TeamDayResponse | null>(null);
  if (settled?.status === "ready" && settled.day !== shown) setShown(settled.day);
  const dayError = settled?.status === "error" && settled.key === dayKey ? settled.error : null;
  const dayLoading = settled?.key !== dayKey;

  function goTo(next: number) {
    setChosenDay(next);
    setErrors(null);
    setNotes([]);
    onUrlChange(teamHref(next, basis));
  }

  function changeBasis(next: TeamBasis) {
    setBasis(next);
    onUrlChange(teamHref(sp, next));
  }

  async function save(assignments: LineupAssignment[]) {
    if (sp === null) return;
    setSaving(true);
    setErrors(null);
    setNotes([]);
    try {
      const answer = await api.saveLineup(sp, {
        assignments,
        through_sp: through && matchup ? matchup.last_sp : null,
      });
      setNotes(answer.warnings);
      setVersion((v) => v + 1);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setErrors(error.details ?? [error.detail ?? error.message]);
    } finally {
      setSaving(false);
    }
  }

  async function syncFromEspn() {
    if (!calendar || sp === null) return;
    setSyncing(true);
    setErrors(null);
    setNotes([]);
    try {
      const pulled = await api.syncLineup(syncRange(calendar, sp));
      setNotes([
        `Pulled ESPN’s lineup for ${pulled.days} ${pulled.days === 1 ? "day" : "days"}.`,
      ]);
      setVersion((v) => v + 1);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setErrors(error.details ?? [error.detail ?? error.message]);
    } finally {
      setSyncing(false);
    }
  }

  const closeCard = useCallback(() => setCard(null), []);
  const watchChanged = useCallback(() => setWatchVersion((v) => v + 1), []);

  const failure = calendarError ?? (shown === null ? dayError : null);
  if (failure) {
    return failure.status === 409 ? <NoTeamSynced error={failure} /> : <TeamFailed error={failure} />;
  }
  if (calendar === null || sp === null) return <TeamLoading label="Reading the calendar…" />;

  const day = shown;

  return (
    <div className="flex flex-col gap-4">
      <DayPicker calendar={calendar} sp={sp} onChange={goTo} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
        <section
          aria-label="Roster"
          className="flex min-w-0 flex-col gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950"
        >
          {day === null ? (
            <TeamLoading label="Reading the roster…" />
          ) : (
            <>
              <RosterHeader day={day} syncing={syncing} onSync={syncFromEspn} />
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <BasisToggle
                  label="Stats"
                  basis={basis}
                  actualsAvailable={day.actuals_available}
                  onChange={changeBasis}
                />
                <label className="flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400">
                  <input
                    type="checkbox"
                    checked={through}
                    onChange={(event) => setThrough(event.target.checked)}
                  />
                  Apply through end of matchup
                </label>
              </div>

              {dayError ? (
                <p role="alert" className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200">
                  Couldn’t read that day: {dayError.detail ?? dayError.message}
                </p>
              ) : null}
              {errors ? (
                <div
                  role="alert"
                  data-lineup-errors
                  className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:bg-rose-500/10 dark:text-rose-200"
                >
                  <p className="font-medium">That lineup wasn’t saved:</p>
                  <ul className="mt-1 list-disc pl-4">
                    {errors.map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {[...notes, ...day.warnings].length > 0 ? (
                <ul
                  data-lineup-notes
                  className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"
                >
                  {[...notes, ...day.warnings].map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              ) : null}

              <div className={dayLoading || saving ? "opacity-60" : ""} aria-busy={dayLoading || saving}>
                <RosterTable
                  day={day}
                  busy={saving || syncing || dayLoading}
                  onOpenPlayer={(player) => setCard({ ...player, fromSp: day.sp })}
                  onMove={save}
                />
              </div>
            </>
          )}
        </section>

        {waiverStart ? (
          <WaiverPanel
            calendar={calendar}
            initialMatchupId={waiverStart.matchupId}
            initialDay={waiverStart.day}
            refreshToken={watchVersion}
            onOpenPlayer={(player, fromSp) => setCard({ ...player, fromSp })}
          />
        ) : null}
      </div>

      {card ? (
        <TeamPlayerCard
          playerId={card.id}
          name={card.name}
          fromSp={card.fromSp}
          onClose={closeCard}
          onWatchChange={watchChanged}
        />
      ) : null}
    </div>
  );
}
