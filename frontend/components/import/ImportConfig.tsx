"use client";

import { PROJECTION_BASES, RANKING_HORIZONS } from "@/lib/api";
import type { ProjectionBasis, RankingHorizon } from "@/lib/api";
import {
  BASIS_HINT,
  BASIS_LABEL,
  KIND_PROJECTION,
  KIND_RANKING,
  RANKING_HORIZON_HINT,
  RANKING_HORIZON_LABEL,
  type ImportForm,
} from "@/lib/importing";

/**
 * Step three: what the rows are, beyond the numbers in them.
 *
 * Three fields every kind needs — who published it, which season it is for, how it is
 * delimited — and then the per-kind `options` the backend refuses to guess: a projection's
 * `basis`, a ranking's `name` and `horizon`. The asymmetry is the point and is worth saying
 * on the page: a value source carries production and both horizons are derived from it, so
 * it declares nothing; a rank-only list has no stats to age-adjust, so it must.
 */

const FIELD =
  "rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200";

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
    <div className="flex min-w-40 flex-col gap-1">
      <label
        htmlFor={htmlFor}
        className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase"
      >
        {label}
      </label>
      {children}
      {hint ? <span className="max-w-xs text-[11px] text-zinc-500">{hint}</span> : null}
    </div>
  );
}

export function ImportConfig({
  form,
  onChange,
}: {
  form: ImportForm;
  onChange: (next: Partial<ImportForm>) => void;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        3. Where it came from
      </h2>

      <div className="flex flex-wrap gap-x-6 gap-y-4">
        <Field label="Source" htmlFor="import-source" hint="hashtag, fantasypros, yahoo, us…">
          <input
            id="import-source"
            value={form.source}
            required
            onChange={(event) => onChange({ source: event.target.value })}
            placeholder="hashtag"
            className={FIELD}
          />
        </Field>

        <Field
          label="Season"
          htmlFor="import-season"
          hint="The year the season ends. Blank uses the backend's ESPN_SEASON."
        >
          <input
            id="import-season"
            value={form.season}
            inputMode="numeric"
            onChange={(event) => onChange({ season: event.target.value })}
            placeholder="default"
            className={`${FIELD} w-28 font-mono`}
          />
        </Field>

        <Field
          label="Delimiter"
          htmlFor="import-delimiter"
          hint="Blank sniffs it. Set it when a name with a comma in it confuses the sniffer."
        >
          <select
            id="import-delimiter"
            value={form.delimiter}
            onChange={(event) => onChange({ delimiter: event.target.value })}
            className={`${FIELD} w-32`}
          >
            <option value="">Auto</option>
            <option value=",">Comma</option>
            <option value={"\t"}>Tab</option>
            <option value=";">Semicolon</option>
            <option value="|">Pipe</option>
          </select>
        </Field>

        {form.kind === KIND_PROJECTION ? (
          <Field label="Basis" htmlFor="import-basis" hint={BASIS_HINT}>
            <select
              id="import-basis"
              value={form.basis}
              onChange={(event) => onChange({ basis: event.target.value as ProjectionBasis })}
              className={`${FIELD} w-40`}
            >
              {PROJECTION_BASES.map((basis) => (
                <option key={basis} value={basis}>
                  {BASIS_LABEL[basis]}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        {form.kind === KIND_RANKING ? (
          <>
            <Field
              label="Set name"
              htmlFor="import-name"
              hint="The list's label. With the source, season and horizon it decides which stored set this REPLACES. Blank uses the source name."
            >
              <input
                id="import-name"
                value={form.name}
                onChange={(event) => onChange({ name: event.target.value })}
                placeholder="Dynasty Top 200"
                className={`${FIELD} w-52`}
              />
            </Field>

            <Field label="Horizon" htmlFor="import-horizon" hint={RANKING_HORIZON_HINT}>
              <select
                id="import-horizon"
                value={form.horizon}
                required
                onChange={(event) =>
                  onChange({ horizon: event.target.value as RankingHorizon | "" })
                }
                className={`${FIELD} w-40`}
              >
                <option value="">Choose…</option>
                {RANKING_HORIZONS.map((horizon) => (
                  <option key={horizon} value={horizon}>
                    {RANKING_HORIZON_LABEL[horizon]}
                  </option>
                ))}
              </select>
            </Field>
          </>
        ) : null}
      </div>
    </section>
  );
}
