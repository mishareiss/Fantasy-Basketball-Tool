"use client";

import { QUIET_BUTTON, FIELD } from "@/components/draft/DraftStates";
import type { TeamCalendarResponse } from "@/lib/api";
import { dayLabel, matchupDays, matchupOf, monthDay, weekday } from "@/lib/team";

/**
 * Which day the ROSTER shows: one day back or forward, today, the previous or next matchup,
 * any day of the season by date, or a day of this matchup off the strip.
 *
 * Everything comes off the calendar the page read once (`GET /team/calendar`), so moving
 * between days costs one roster read and nothing else. Before tip-off there is no "today", so
 * the page opens on the first day and says when the season starts.
 */

const ARROW =
  "rounded-md border border-zinc-300 px-2 py-1 text-sm text-zinc-700 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900";

export function DayPicker({
  calendar,
  sp,
  onChange,
}: {
  calendar: TeamCalendarResponse;
  sp: number;
  onChange: (sp: number) => void;
}) {
  const day = calendar.days.find((d) => d.sp === sp) ?? null;
  const matchup = matchupOf(calendar.matchups, sp);
  const index = matchup ? calendar.matchups.indexOf(matchup) : -1;
  const previousMatchup = index > 0 ? calendar.matchups[index - 1] : null;
  const nextMatchup = index >= 0 ? (calendar.matchups[index + 1] ?? null) : null;
  const strip = matchup ? matchupDays(calendar.days, matchup.id) : [];
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
            return (
              <button
                key={d.sp}
                type="button"
                aria-pressed={selected}
                data-strip-day={d.sp}
                onClick={() => onChange(d.sp)}
                className={`flex min-w-[3.5rem] flex-col items-center rounded-md border px-2 py-1 text-xs transition-colors ${
                  selected
                    ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                    : "border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-900"
                }`}
              >
                <span className="font-medium">{weekday(d.date)}</span>
                <span className="tabular-nums">{monthDay(d.date)}</span>
                <span
                  className={`tabular-nums ${selected ? "" : "text-zinc-500"}`}
                  title={`${d.my_games} of my players play`}
                >
                  {d.my_games} G
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
