"use client";

import type { ImportKindInfo } from "@/lib/api";
import { KIND_HINT } from "@/lib/importing";

/**
 * Step one: what is this table?
 *
 * The kinds come from `GET /import/kinds` rather than a hardcoded list, so the page can't
 * offer something the backend doesn't handle — and a planned kind shows as itself, disabled,
 * with the backend's own note on what it is waiting for. That is more use than hiding it:
 * "where do I put sportsbook lines" is a question the page should answer.
 *
 * Under the choice go the columns that kind looks for and the headers it accepts for each.
 * A file whose header row is nothing like them is the one failure the preview can't diagnose,
 * because the parser fails before there is anything to show.
 */

const CHIP_BASE =
  "flex min-w-52 flex-1 flex-col gap-1 rounded-lg border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";
const CHIP_ON = "border-sky-500 bg-sky-50 dark:border-sky-400 dark:bg-sky-950/40";
const CHIP_OFF =
  "border-zinc-200 bg-white hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:bg-zinc-900";
const CHIP_DISABLED =
  "cursor-not-allowed border-dashed border-zinc-200 bg-zinc-50 opacity-70 dark:border-zinc-800 dark:bg-zinc-900/40";

function ColumnHint({ kind }: { kind: ImportKindInfo }) {
  const required = new Set(kind.required);
  const fields = Object.entries(kind.value_columns);
  if (fields.length === 0) return null;

  return (
    <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/40">
      <p className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
        Columns this kind looks for
      </p>
      <dl className="mt-2 flex flex-col gap-1.5">
        {fields.map(([field, aliases]) => (
          <div key={field} className="flex flex-wrap items-baseline gap-x-2 text-xs">
            <dt className="font-mono font-semibold text-zinc-800 dark:text-zinc-200">
              {field}
              {required.has(field) ? (
                <span
                  className="ml-1 font-sans text-[10px] font-normal text-rose-600 dark:text-rose-400"
                  title="A row with no value here comes back invalid and is not written"
                >
                  required
                </span>
              ) : null}
            </dt>
            <dd className="text-zinc-500 dark:text-zinc-400">{aliases.join(", ")}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-[11px] text-zinc-500">
        Headers are matched case- and punctuation-insensitively. Anything else is ignored —
        the name, team and position columns are found the same way.
      </p>
    </div>
  );
}

export function KindPicker({
  kinds,
  value,
  onChange,
}: {
  kinds: ImportKindInfo[];
  value: string;
  onChange: (kind: string) => void;
}) {
  const chosen = kinds.find((kind) => kind.kind === value && kind.implemented);

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">1. What is it?</h2>
      <div role="group" aria-label="Kind" className="flex flex-wrap gap-2">
        {kinds.map((kind) =>
          kind.implemented ? (
            <button
              key={kind.kind}
              type="button"
              aria-pressed={kind.kind === value}
              onClick={() => onChange(kind.kind)}
              className={`${CHIP_BASE} ${kind.kind === value ? CHIP_ON : CHIP_OFF}`}
            >
              <span className="font-mono text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {kind.kind}
              </span>
              <span className="text-xs text-zinc-600 dark:text-zinc-400">
                {KIND_HINT[kind.kind] ?? kind.label}
              </span>
            </button>
          ) : (
            <button
              key={kind.kind}
              type="button"
              disabled
              title={kind.label}
              className={`${CHIP_BASE} ${CHIP_DISABLED}`}
            >
              <span className="flex items-baseline gap-2">
                <span className="font-mono text-sm font-semibold text-zinc-500">{kind.kind}</span>
                <span className="text-[10px] tracking-wide text-zinc-400 uppercase">
                  coming soon
                </span>
              </span>
              {/* The backend's own note on what the kind is waiting for — a model, a
                  migration, some odds arithmetic — rather than a shrug. */}
              <span className="line-clamp-3 text-xs text-zinc-500">{kind.label}</span>
            </button>
          ),
        )}
      </div>
      {chosen ? <ColumnHint kind={chosen} /> : null}
    </section>
  );
}
