"use client";

import { QUIET_BUTTON, FIELD } from "@/components/draft/DraftStates";
import type { StartsResponse, TeamCalendarResponse } from "@/lib/api";
import { dayLabel, matchupDays, matchupOf, monthDay, startsTooltip, weekday } from "@/lib/team";

/**
 * Which day the ROSTER shows: one day back or forward, today, the previous or next matchup,
 * any day of the season by date, or a day of this matchup off the strip.
 *
 * Everything comes off the calendar the page read once (`GET /team/calendar`), so moving
 * between days costs one roster read and nothing else. Before tip-off there is no "today", so
 * the page opens on the first day and says when the season starts.
 *
 * STARTS, NOT GAMES (task 35). Each strip day shows `filled/7` from `GET /team/starts` in the
 * roster's view (Actual or Plan): an amber "3 open" where nobody on the roster can fill a
 * start (a streamer's day) and a red "2 benched" where the lineup as set sits players who
 * could start. How many of mine play stays in the tooltip. Until the starts arrive (or for a
 * day they don't cover) the day shows its game count, as before.
 */

const ARROW =
  "rounded-md border border-zinc-300 px-2 py-1 text-sm text-zinc-700 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900";

export function DayPicker({
  calendar,
  sp,
  onChange,
  starts = null,
}: {
  calendar: TeamCalendarResponse;
  sp: number;
  onChange: (sp: number) => void;
  /** The strip's matchup's starts, in the roster's view. */
  starts?: StartsResponse | null;
}) {
  const day = calendar.days.find((d) => d.sp === sp) ?? null;
  const matchup = matchupOf(calendar.matchups, sp);
  const index = matchup ? calendar.matchups.indexOf(matchup) : -1;
  const previousMatchup = index > 0 ? calendar.matchups[index - 1] : null;
  const nextMatchup = index >= 0 ? (calendar.matchups[index + 1] ?? null) : null;
  const strip = matchup ? matchupDays(calendar.days, matchup.id) : [];
  const startsBySp = new Map(
    starts && starts.matchup.id === matchup?.id ? starts.days.map((d) => [d.sp, d]) : [],
  );
  const today = calendar.today_period ?? calendar.effective_period;
  const first = calendar.days[0];
  const last = calendar.days.at(-1);
  const preSeason = calendar.today_period === null && calendar.effective_period === 1;

  const label = day ? `${dayLabel(day.date)}${matchup ? ` · ${matchup.label}` : ""}` : `Day ${sp}`;

  return (
    <section
      aria-label="Roster day"
      data-day-picker
      className="flex flex-col gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={ARROW}
          aria-label="Previous day"
          disabled={sp <= 1}
          onClick={() => onChange(sp - 1)}
        >
          ‹
        </button>
        <h2
          data-day-label
          className="min-w-[11rem] text-center text-sm font-semibold text-zinc-900 tabular-nums dark:text-zinc-100"
        >
          {label}
        </h2>
        <button
          type="button"
          className={ARROW}
          aria-label="Next day"
          disabled={sp >= calendar.final_period}
          onClick={() => onChange(sp + 1)}
        >
          ›
        </button>
        <button
          type="button"
          className={QUIET_BUTTON}
          disabled={sp === today}
          onClick={() => onChange(today)}
        >
          Today
        </button>
        <span className="mx-1 h-5 w-px bg-zinc-200 dark:bg-zinc-800" aria-hidden />
        <button
          type="button"
          className={QUIET_BUTTON}
          disabled={previousMatchup === null}
          onClick={() => previousMatchup && onChange(previousMatchup.first_sp)}
        >
          ‹ Prev matchup
        </button>
        <button
          type="button"
          className={QUIET_BUTTON}
          disabled={nextMatchup === null}
          onClick={() => nextMatchup && onChange(nextMatchup.first_sp)}
        >
          Next matchup ›
        </button>
        <label className="ml-auto flex items-center gap-2 text-xs text-zinc-500">
          Go to
          <input
            type="date"
            aria-label="Go to date"
            className={`${FIELD} py-1 text-xs`}
            value={day?.date ?? ""}
            min={first?.date}
            max={last?.date}
            onChange={(event) => {
              const target = calendar.days.find((d) => d.date === event.target.value);
              if (target) onChange(target.sp);
            }}
          />
        </label>
      </div>

      {preSeason && first ? (
        <p data-preseason className="text-xs text-zinc-500">
          Season starts {dayLabel(first.date)}
        </p>
      ) : null}

      {strip.length > 0 ? (
        <div role="group" aria-label="Days of this matchup" className="flex flex-wrap gap-1.5">
          {strip.map((d) => {
            const selected = d.sp === sp;
            const day = startsBySp.get(d.sp);
            return (
              <button
                key={d.sp}
                type="button"
                aria-pressed={selected}
                data-strip-day={d.sp}
                title={day ? startsTooltip(day, d.my_games) : undefined}
                onClick={() => onChange(d.sp)}
                className={`flex min-w-[3.5rem] flex-col items-center rounded-md border px-2 py-1 text-xs transition-colors ${
                  selected
                    ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                    : "border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-900"
                }`}
              >
                <span className="font-medium">{weekday(d.date)}</span>
                <span className="tabular-nums">{monthDay(d.date)}</span>
                {day ? (
                  <>
                    <span
                      data-strip-starts
                      className={`font-semibold tabular-nums ${selected ? "" : "text-zinc-900 dark:text-zinc-100"}`}
                    >
                      {day.filled}/{day.slots}
                    </span>
                    {day.empty > 0 ? (
                      <span
                        data-strip-open={day.empty}
                        className="mt-0.5 rounded bg-amber-100 px-1 text-[10px] leading-4 font-medium whitespace-nowrap text-amber-900 dark:bg-amber-500/20 dark:text-amber-200"
                      >
                        {day.empty} open
                      </span>
                    ) : null}
                    {day.left_on_bench > 0 ? (
                      <span
                        data-strip-benched={day.left_on_bench}
                        className="mt-0.5 rounded bg-rose-100 px-1 text-[10px] leading-4 font-medium whitespace-nowrap text-rose-900 dark:bg-rose-500/20 dark:text-rose-200"
                      >
                        {day.left_on_bench} benched
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span
                    className={`tabular-nums ${selected ? "" : "text-zinc-500"}`}
                    title={`${d.my_games} of my players play`}
                  >
                    {d.my_games} G
                  </span>
                )}
              </button>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
