"use client";

import { useEffect, useId, useState } from "react";

import {
  ApiError,
  api,
  type CurveResponse,
  type Horizon,
  type TiersResponse,
} from "@/lib/api";
import { HORIZON_LABEL } from "@/lib/board";
import { decimal } from "@/lib/format";

/**
 * The two dials the board's ranking actually turns on, made readable without opening JSON.
 *
 * Read-only on purpose this task: /valuation/curve and /valuation/tiers are read-only
 * endpoints, and tuning happens through the DYNASTY_* / TIER_* env vars — which is why every
 * parameter is shown next to the variable that moves it. Live sliders are a later task.
 *
 * Fetched lazily on first open. A closed inspector should not cost two requests on a board
 * that is already 404ing because nothing is synced.
 */

const BAND_STYLE: Record<string, string> = {
  youth: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  prime: "bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  decline: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  floor: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400",
};

function Param({ name, value, env }: { name: string; value: string; env?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] tracking-wide text-zinc-500 uppercase">{name}</span>
      <span className="font-mono text-sm text-zinc-900 tabular-nums dark:text-zinc-100">
        {value}
      </span>
      {env ? <span className="font-mono text-[10px] text-zinc-400">{env}</span> : null}
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  // Named landmarks: the two halves both contain a table of numbers, and "which table is
  // this" should be answerable from the accessibility tree rather than from position.
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div>
        <h3 id={id} className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          {title}
        </h3>
        <p className="text-xs text-zinc-500">{hint}</p>
      </div>
      {children}
    </section>
  );
}

function CurvePanel({ curve }: { curve: CurveResponse }) {
  const { params, env_vars: env } = curve;

  return (
    <Section
      title="Age curve"
      hint="What dynasty value multiplies current-year value by, age by age."
    >
      <div className="flex flex-wrap gap-x-8 gap-y-3">
        <Param name="prime" value={`${params.prime_start}–${params.prime_end}`} env={env.prime_start} />
        <Param
          name="youth bonus / yr"
          value={params.youth_bonus_per_year.toFixed(3)}
          env={env.youth_bonus_per_year}
        />
        <Param
          name="decline / yr"
          value={params.decline_per_year.toFixed(3)}
          env={env.decline_per_year}
        />
        <Param
          name="floor"
          value={params.min_multiplier.toFixed(2)}
          env={env.min_multiplier}
        />
      </div>

      <div className="overflow-x-auto">
        <table className="border-collapse text-[11px]">
          <caption className="sr-only">Age to dynasty multiplier</caption>
          <thead>
            <tr>
              <th scope="row" className="pr-3 text-left font-medium text-zinc-500">
                Age
              </th>
              {curve.sample.map((point) => (
                <th key={point.age} scope="col" className="px-1.5 py-0.5 font-mono font-normal text-zinc-500 tabular-nums">
                  {point.age}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row" className="pr-3 text-left font-medium text-zinc-500">
                ×
              </th>
              {curve.sample.map((point) => (
                <td
                  key={point.age}
                  title={point.band}
                  className={`px-1.5 py-0.5 text-center font-mono tabular-nums ${
                    BAND_STYLE[point.band] ?? BAND_STYLE.floor
                  }`}
                >
                  {point.multiplier.toFixed(2)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap gap-2">
        {Object.keys(BAND_STYLE).map((band) => (
          <span
            key={band}
            className={`rounded px-1.5 py-0.5 text-[10px] tracking-wide uppercase ${BAND_STYLE[band]}`}
          >
            {band}
          </span>
        ))}
      </div>
    </Section>
  );
}

function TiersPanel({ tiers }: { tiers: TiersResponse }) {
  const { params, env_vars: env } = tiers;

  return (
    <Section
      title={`Tier structure — ${HORIZON_LABEL[tiers.horizon].toLowerCase()}`}
      hint={`A break opens where the drop beats ${params.gap_multiple}× the typical drop of ${decimal(tiers.typical_gap, 2)} (threshold ${decimal(tiers.break_threshold, 2)}), across the top ${tiers.pool_size} of ${tiers.total_ranked} ranked.`}
    >
      <div className="flex flex-wrap gap-x-8 gap-y-3">
        <Param name="gap multiple" value={`${params.gap_multiple}×`} env={env.gap_multiple} />
        <Param name="min tier size" value={String(params.min_size)} env={env.min_size} />
        <Param name="max tiers" value={String(params.max_tiers)} env={env.max_tiers} />
        <Param name="pool" value={String(params.pool)} env={env.pool} />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="text-[11px] tracking-wide text-zinc-500 uppercase">
              <th scope="col" className="py-1 pr-3 text-left">Tier</th>
              <th scope="col" className="px-3 py-1 text-right">Starts</th>
              <th scope="col" className="px-3 py-1 text-left">Leader</th>
              <th scope="col" className="px-3 py-1 text-right">Size</th>
              <th scope="col" className="px-3 py-1 text-right">Band</th>
              <th scope="col" className="py-1 pl-3 text-right">Gap</th>
            </tr>
          </thead>
          <tbody>
            {tiers.tiers.map((tier) => (
              <tr key={tier.tier} className="border-t border-zinc-100 dark:border-zinc-900">
                <td className="py-1 pr-3 font-medium">Tier {tier.tier}</td>
                <td className="px-3 py-1 text-right font-mono text-zinc-500 tabular-nums">
                  #{tier.start_rank}
                </td>
                <td className="px-3 py-1 text-zinc-600 dark:text-zinc-400">
                  {tier.leader ?? "—"}
                </td>
                <td className="px-3 py-1 text-right font-mono tabular-nums">{tier.size}</td>
                <td className="px-3 py-1 text-right font-mono text-zinc-500 tabular-nums">
                  {decimal(tier.value_high)} → {decimal(tier.value_low)}
                </td>
                <td className="py-1 pl-3 text-right font-mono text-zinc-500 tabular-nums">
                  {tier.gap_ratio === null ? "—" : `${tier.gap_ratio.toFixed(1)}×`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

type Loaded = {
  /** Which horizon this was fetched for — the tier structure is horizon-specific. */
  horizon: Horizon;
  curve: CurveResponse | null;
  tiers: TiersResponse | null;
  error: ApiError | null;
};

export function CalibrationInspector({ horizon }: { horizon: Horizon }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    Promise.all([api.valuationCurve(), api.valuationTiers(horizon)])
      .then(([curve, tiers]) => {
        if (!cancelled) setLoaded({ horizon, curve, tiers, error: null });
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setLoaded({
          horizon,
          curve: null,
          tiers: null,
          error: caught instanceof ApiError ? caught : new ApiError("Could not load calibration"),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [horizon, open]);

  // Same trick as the board: a result from the previous horizon is not this horizon's, so
  // flipping the toggle shows "loading" again rather than the last horizon's tiers.
  const current = loaded?.horizon === horizon ? loaded : null;
  const error = current?.error ?? null;
  const data = current?.curve && current.tiers ? { curve: current.curve, tiers: current.tiers } : null;

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs font-medium text-zinc-600 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-900"
      >
        <span aria-hidden className="text-[10px]">{open ? "▼" : "▶"}</span>
        Curve &amp; tiers
        <span className="font-normal text-zinc-400">
          the two dials this ranking depends on — read-only
        </span>
      </button>

      {open ? (
        <div className="flex flex-col gap-6 border-t border-zinc-200 px-4 py-4 dark:border-zinc-800">
          {error ? (
            <p className="text-xs text-zinc-500">
              {error.detail ?? error.message} — the curve and tiers need a synced board.
            </p>
          ) : data ? (
            <>
              <CurvePanel curve={data.curve} />
              <TiersPanel tiers={data.tiers} />
            </>
          ) : (
            <p className="text-xs text-zinc-500">Loading calibration…</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
