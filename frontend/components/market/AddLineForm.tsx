"use client";

import type { ImportRowOutcome, MarketStat } from "@/lib/api";
import { type AddForm, validateAdd } from "@/lib/market";

/**
 * Add one line: a player, a stat, the number, and the price on each side.
 *
 * The player field is a plain text box on purpose. The name is resolved by the IMPORTER's
 * dry run (`POST /import/market_line`), which is the same matcher, the same candidate list
 * and the same alias fix a pasted file gets — so a name this page can place is a name that
 * file could, and a name it can't is fixed once, for both, forever. A search endpoint here
 * would be a second opinion about who "J. Williams" is.
 *
 * The stat list is the LEAGUE's scored counting stats, from `GET /market/lines`. A prop on
 * something we don't score derives nothing, and a rate (FG%) has no coefficient that can be
 * multiplied by a per-game line — so neither is offered rather than being refused later.
 */

const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

const BUTTON =
  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={htmlFor}
        className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
      >
        {label}
      </label>
      {children}
      {hint ? <span className="max-w-[16rem] text-[11px] text-zinc-500">{hint}</span> : null}
    </div>
  );
}

/**
 * The name didn't resolve. Show what it might have been, exactly as the importer's review
 * rows do — picking one records the alias AND writes the line, so the question is asked once
 * per player ever rather than once per line.
 */
function Unresolved({
  row,
  onPick,
  picking,
}: {
  row: ImportRowOutcome;
  onPick: (playerId: number) => void;
  picking: boolean;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/30"
    >
      <p className="text-amber-800 dark:text-amber-300">
        {row.candidates.length
          ? `Nothing certain for “${row.source_name}”. Who is it?`
          : `Nobody in our pool looks like “${row.source_name}”. A line has to hang on one of
             our players, so there is nothing to write yet.`}
      </p>
      <div className="flex flex-wrap gap-2">
        {row.candidates.map((candidate) => (
          <button
            key={candidate.player_id}
            type="button"
            disabled={picking}
            onClick={() => onPick(candidate.player_id)}
            className={`${BUTTON} border border-amber-400 bg-white text-amber-900 hover:bg-amber-100 dark:border-amber-700 dark:bg-zinc-950 dark:text-amber-300 dark:hover:bg-amber-950/60`}
          >
            {candidate.full_name}
            {candidate.nba_team ? ` · ${candidate.nba_team}` : ""} ·{" "}
            {Math.round(candidate.score * 100)}%
          </button>
        ))}
      </div>
      <p className="text-[11px] text-amber-700 dark:text-amber-400">
        Picking one records it as this book&rsquo;s name for that player, so the next line —
        and the next paste — resolve it instantly.
      </p>
    </div>
  );
}

export function AddLineForm({
  stats,
  form,
  onChange,
  onSubmit,
  pending,
  unresolved,
  onPick,
  picking,
}: {
  stats: MarketStat[];
  form: AddForm;
  onChange: (next: Partial<AddForm>) => void;
  onSubmit: () => void;
  pending: boolean;
  /** The importer's row when the name didn't resolve certainly; null when it did. */
  unresolved: ImportRowOutcome | null;
  onPick: (playerId: number) => void;
  picking: boolean;
}) {
  const problem = validateAdd(form);

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Add a line</h2>

      <form
        className="flex flex-wrap items-start gap-x-4 gap-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!problem && !pending) onSubmit();
        }}
      >
        <Field label="Player" htmlFor="market-name" hint="Any spelling — the matcher resolves it.">
          <input
            id="market-name"
            value={form.name}
            autoComplete="off"
            onChange={(event) => onChange({ name: event.target.value })}
            placeholder="LeBron James"
            className={`${FIELD} w-56`}
          />
        </Field>

        <Field label="Stat" htmlFor="market-stat" hint="What our league pays for, per unit.">
          <select
            id="market-stat"
            value={form.stat}
            onChange={(event) => onChange({ stat: event.target.value })}
            className={`${FIELD} w-44`}
          >
            <option value="">Pick a stat…</option>
            {stats.map((stat) => (
              <option key={stat.stat_id} value={stat.name}>
                {stat.name} · {stat.label} ({stat.points > 0 ? "+" : ""}
                {stat.points})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Line" htmlFor="market-line" hint="Per game, as the book quotes it.">
          <input
            id="market-line"
            value={form.line}
            inputMode="decimal"
            onChange={(event) => onChange({ line: event.target.value })}
            placeholder="24.5"
            className={`${FIELD} w-24`}
          />
        </Field>

        <Field label="Over odds" htmlFor="market-over" hint="American; empty = unpriced.">
          <input
            id="market-over"
            value={form.over}
            inputMode="numeric"
            onChange={(event) => onChange({ over: event.target.value })}
            placeholder="-115"
            className={`${FIELD} w-24`}
          />
        </Field>

        <Field label="Under odds" htmlFor="market-under" hint="Both sides, or the price is unusable.">
          <input
            id="market-under"
            value={form.under}
            inputMode="numeric"
            onChange={(event) => onChange({ under: event.target.value })}
            placeholder="-105"
            className={`${FIELD} w-24`}
          />
        </Field>

        <div className="flex items-center gap-3 self-end pb-1">
          <button
            type="submit"
            disabled={problem !== null || pending}
            className={`${BUTTON} bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300`}
          >
            {pending ? "Saving…" : "Add line"}
          </button>
          {problem ? <span className="text-xs text-amber-600">{problem}</span> : null}
        </div>
      </form>

      {unresolved ? <Unresolved row={unresolved} onPick={onPick} picking={picking} /> : null}
    </section>
  );
}
