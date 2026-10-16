"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  type DayView,
  type LineupAssignment,
  type PlanMutationResponse,
  type PlanResponse,
  type TeamBasis,
  type TeamCalendarResponse,
  type TeamDayResponse,
} from "@/lib/api";
import { composerPrefill, moveNumbers, type ComposerDraft, type ComposerEntry } from "@/lib/plan";
import {
  autoFillSummary,
  clampDay,
  isDayView,
  isTeamBasis,
  matchupOf,
  syncRange,
  teamHref,
} from "@/lib/team";
import { BasisToggle } from "./BasisToggle";
import { DayPicker } from "./DayPicker";
import { MoveComposer } from "./MoveComposer";
import { DayPlanWarnings, DroppedTodayStrip } from "./PlanRosterExtras";
import { PlanTab, type PlanNotice } from "./PlanTab";
import { RightPanelTabs, rememberTab, rememberedTab, type RightTab } from "./RightPanelTabs";
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
 * ACTUAL | PLAN (task 31). The roster's view is in the URL too (`?view=plan`). The Plan view
 * reads `GET /team/day?view=plan` (the roster as if the plan had run) and saves slot moves to
 * the plan's lineup, never the Actual one. The plan itself (`GET /team/plan`) is read only
 * while something shows it: the Plan view, the Plan tab, or the move composer. After any plan
 * write the page reads the plan, the day and the waiver list again.
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
  const rawView = searchParams.get("view");

  return (
    <TeamView
      initialDay={Number.isFinite(rawDay) && rawDay > 0 ? rawDay : null}
      initialBasis={isTeamBasis(rawBasis) ? rawBasis : "projected"}
      initialView={isDayView(rawView) ? rawView : "actual"}
      onUrlChange={(href) => router.replace(href, { scroll: false })}
    />
  );
}

export function TeamView({
  initialDay,
  initialBasis,
  initialView = "actual",
  onUrlChange,
}: {
  initialDay: number | null;
  initialBasis: TeamBasis;
  initialView?: DayView;
  onUrlChange: (href: string) => void;
}) {
  const [calendar, setCalendar] = useState<TeamCalendarResponse | null>(null);
  const [calendarError, setCalendarError] = useState<ApiError | null>(null);
  const [chosenDay, setChosenDay] = useState<number | null>(initialDay);
  const [basis, setBasis] = useState<TeamBasis>(initialBasis);
  const [view, setView] = useState<DayView>(initialView);
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<DaySettled | null>(null);

  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [errors, setErrors] = useState<string[] | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [through, setThrough] = useState(false);
  const [autoFilling, setAutoFilling] = useState(false);
  const [autoThrough, setAutoThrough] = useState(false);
  const [autoResult, setAutoResult] = useState<string | null>(null);
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
  // The waiver panel's chosen days, as it last reported them: the composer's default day.
  const [waiverDays, setWaiverDays] = useState<number[] | null>(null);

  const [rightTab, setRightTab] = useState<RightTab>(rememberedTab);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [planError, setPlanError] = useState<ApiError | null>(null);
  const [planVersion, setPlanVersion] = useState(0);
  const [notice, setNotice] = useState<PlanNotice | null>(null);
  // The open composer, keyed so a second open starts from its own prefill.
  const [composer, setComposer] = useState<{ key: number; draft: ComposerDraft } | null>(null);

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

  const dayKey = sp === null ? null : `${sp}|${basis}|${view}|${version}`;
  const pending = useRef(0);

  useEffect(() => {
    if (dayKey === null || sp === null) return;
    const ticket = (pending.current += 1);
    // The Actual read is exactly task 29a's call; only the Plan view adds `view`.
    (view === "plan" ? api.teamDay({ sp, basis, view }) : api.teamDay({ sp, basis }))
      .then((day) => {
        if (ticket === pending.current) setSettled({ key: dayKey, status: "ready", day });
      })
      .catch((caught: unknown) => {
        if (ticket === pending.current)
          setSettled({ key: dayKey, status: "error", error: asApiError(caught) });
      });
  }, [dayKey, sp, basis, view]);

  // The plan, while something on screen shows it, and again after every plan write (even
  // when the write closed the only thing showing it: the composer).
  const needPlan = rightTab === "plan" || view === "plan" || composer !== null || planVersion > 0;
  useEffect(() => {
    if (!needPlan) return;
    let cancelled = false;
    api
      .plan()
      .then((answer) => {
        if (cancelled) return;
        setPlan(answer);
        setPlanError(null);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setPlanError(asApiError(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [needPlan, planVersion]);

  // The last good day stays on screen (dimmed) while the next one loads.
  const [shown, setShown] = useState<TeamDayResponse | null>(null);
  if (settled?.status === "ready" && settled.day !== shown) setShown(settled.day);
  const dayError = settled?.status === "error" && settled.key === dayKey ? settled.error : null;
  const dayLoading = settled?.key !== dayKey;

  function goTo(next: number) {
    setChosenDay(next);
    setErrors(null);
    setNotes([]);
    setAutoResult(null);
    onUrlChange(teamHref(next, basis, view));
  }

  function changeBasis(next: TeamBasis) {
    setBasis(next);
    onUrlChange(teamHref(sp, next, view));
  }

  function changeView(next: DayView) {
    setView(next);
    setErrors(null);
    setNotes([]);
    setAutoResult(null);
    onUrlChange(teamHref(sp, basis, next));
  }

  function changeTab(next: RightTab) {
    setRightTab(next);
    rememberTab(next);
  }

  /** After any plan write: the plan, the day (in the current view) and the waiver list. */
  const planChanged = useCallback(() => {
    setPlanVersion((v) => v + 1);
    setVersion((v) => v + 1);
    setWatchVersion((v) => v + 1);
  }, []);

  function openComposer(entry: ComposerEntry) {
    const today = plan?.today_sp ?? calendar?.effective_period ?? 1;
    const chips = waiverDays ?? (waiverStart ? [waiverStart.day] : []);
    const draft = composerPrefill(entry, { waiverDay: chips[0] ?? null, today });
    setComposer((current) => ({ key: (current?.key ?? 0) + 1, draft }));
  }

  const closeComposer = useCallback(() => setComposer(null), []);

  const composerSaved = useCallback(
    (answer: PlanMutationResponse) => {
      setComposer(null);
      const number = answer.move_id === null ? undefined : moveNumbers(answer.moves).get(answer.move_id);
      setNotice({
        moveId: answer.move_id,
        text: number ? `Saved move ${number}.` : "Saved.",
        warnings: answer.warnings.filter((w) => w.move_id !== null && w.move_id === answer.move_id),
      });
      planChanged();
    },
    [planChanged],
  );

  async function save(assignments: LineupAssignment[]) {
    if (sp === null) return;
    setSaving(true);
    setErrors(null);
    setNotes([]);
    setAutoResult(null);
    try {
      const body = { assignments, through_sp: through && matchup ? matchup.last_sp : null };
      // The Plan view's moves go to the plan's lineup and never touch the Actual one.
      const answer =
        view === "plan" ? await api.savePlanLineup(sp, body) : await api.saveLineup(sp, body);
      setNotes(answer.warnings);
      setVersion((v) => v + 1);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setErrors(error.details ?? [error.detail ?? error.message]);
    } finally {
      setSaving(false);
    }
  }

  async function autoFill() {
    if (sp === null) return;
    setAutoFilling(true);
    setErrors(null);
    setNotes([]);
    setAutoResult(null);
    try {
      const body = { sp, through_sp: autoThrough && matchup ? matchup.last_sp : null };
      // Each view fills its own lineup: the Plan view's never touches the Actual one.
      const answer =
        view === "plan" ? await api.autoFillPlanLineup(body) : await api.autoFillLineup(body);
      setAutoResult(autoFillSummary(answer));
      setNotes(answer.warnings);
      setVersion((v) => v + 1);
    } catch (caught: unknown) {
      const error = asApiError(caught);
      setErrors(error.details ?? [error.detail ?? error.message]);
    } finally {
      setAutoFilling(false);
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
              <RosterHeader
                day={day}
                syncing={syncing}
                onSync={syncFromEspn}
                view={view}
                onViewChange={changeView}
                autoFill={{
                  running: autoFilling,
                  through: autoThrough,
                  onThroughChange: setAutoThrough,
                  onRun: autoFill,
                }}
              />
              {autoResult ? (
                <p
                  role="status"
                  data-auto-fill-result
                  className="rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-200"
                >
                  {autoResult}
                </p>
              ) : null}
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

              {view === "plan" ? <DayPlanWarnings warnings={day.plan_warnings ?? []} /> : null}

              <div
                className={dayLoading || saving || autoFilling ? "opacity-60" : ""}
                aria-busy={dayLoading || saving || autoFilling}
              >
                <RosterTable
                  day={day}
                  busy={saving || syncing || autoFilling || dayLoading}
                  onOpenPlayer={(player) => setCard({ ...player, fromSp: day.sp })}
                  onMove={save}
                  view={view}
                  moves={plan?.moves ?? []}
                  onPlanDrop={(player) => openComposer({ kind: "roster-drop", player, sp: day.sp })}
                />
              </div>

              {view === "plan" ? <DroppedTodayStrip drops={day.planned_drops ?? []} /> : null}
            </>
          )}
        </section>

        {waiverStart ? (
          <RightPanelTabs
            tab={rightTab}
            onChange={changeTab}
            planCount={plan ? plan.moves.length : null}
            waivers={
              <WaiverPanel
                calendar={calendar}
                initialMatchupId={waiverStart.matchupId}
                initialDay={waiverStart.day}
                refreshToken={watchVersion}
                onOpenPlayer={(player, fromSp) => setCard({ ...player, fromSp })}
                onPlan={(row) => openComposer({ kind: "waiver", player: row })}
                onDaysChange={setWaiverDays}
              />
            }
            plan={
              <PlanTab
                plan={plan}
                error={planError}
                calendar={calendar}
                notice={notice}
                onDismissNotice={() => setNotice(null)}
                onAdd={() => openComposer({ kind: "add" })}
                onEdit={(move) => openComposer({ kind: "edit", move })}
                onChanged={planChanged}
              />
            }
          />
        ) : null}
      </div>

      {composer ? (
        <MoveComposer
          key={composer.key}
          calendar={calendar}
          initial={composer.draft}
          moves={plan?.moves ?? []}
          onClose={closeComposer}
          onSaved={composerSaved}
        />
      ) : null}

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
