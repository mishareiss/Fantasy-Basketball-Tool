"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL, api } from "@/lib/api";

/**
 * The reachability probe that used to be the whole home page.
 *
 * It is still the fastest answer to "is the board empty because nothing is synced, or
 * because nothing is running", so it lives on as a one-line footer strip under every page
 * (`strip`) and as the full check on /status (`panel`).
 */

type ProbeState = "loading" | "ok" | "error";

type Probe = {
  label: string;
  state: ProbeState;
  detail: string;
};

const INITIAL: Probe[] = [
  { label: "API", state: "loading", detail: "checking…" },
  { label: "Database", state: "loading", detail: "checking…" },
];

const DOT: Record<ProbeState, string> = {
  ok: "bg-emerald-500",
  error: "bg-red-500",
  loading: "bg-zinc-400",
};

function useProbes(): Probe[] {
  const [probes, setProbes] = useState<Probe[]>(INITIAL);

  useEffect(() => {
    let cancelled = false;

    const update = (index: number, next: Omit<Probe, "label">) => {
      if (cancelled) return;
      setProbes((current) =>
        current.map((probe, i) => (i === index ? { ...probe, ...next } : probe)),
      );
    };

    api
      .health()
      .then((data) => update(0, { state: "ok", detail: data.status }))
      .catch((error: Error) => update(0, { state: "error", detail: error.message }));

    api
      .dbHealth()
      .then((data) =>
        update(1, {
          state: data.status === "ok" ? "ok" : "error",
          detail: data.database ?? data.status,
        }),
      )
      .catch((error: Error) => update(1, { state: "error", detail: error.message }));

    return () => {
      cancelled = true;
    };
  }, []);

  return probes;
}

function Dot({ state }: { state: ProbeState }) {
  return <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${DOT[state]}`} />;
}

export function BackendStatusStrip() {
  const probes = useProbes();

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500 dark:text-zinc-500">
      {probes.map((probe) => (
        <span key={probe.label} className="flex items-center gap-1.5">
          <Dot state={probe.state} />
          <span className="text-zinc-600 dark:text-zinc-400">{probe.label}</span>
          <span className="font-mono">{probe.state === "ok" ? probe.detail : probe.state}</span>
        </span>
      ))}
      <span className="font-mono">{API_BASE_URL}</span>
    </div>
  );
}

export function BackendStatusPanel() {
  const probes = useProbes();

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-px overflow-hidden rounded-lg border border-zinc-200 bg-zinc-200 dark:border-zinc-800 dark:bg-zinc-800">
        {probes.map((probe) => (
          <li
            key={probe.label}
            className="flex items-center justify-between gap-4 bg-white px-4 py-3 dark:bg-black"
          >
            <span className="font-medium">{probe.label}</span>
            <span className="flex items-center gap-2 text-sm">
              <Dot state={probe.state} />
              <span className="text-zinc-600 dark:text-zinc-400">{probe.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="font-mono text-xs text-zinc-500">{API_BASE_URL}</p>
    </div>
  );
}
