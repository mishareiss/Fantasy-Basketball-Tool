"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL, api } from "@/lib/api";

type Probe = {
  label: string;
  state: "loading" | "ok" | "error";
  detail: string;
};

const INITIAL: Probe[] = [
  { label: "API", state: "loading", detail: "checking…" },
  { label: "Database", state: "loading", detail: "checking…" },
];

export default function Home() {
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

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-8 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          Fantasy Basketball Dynasty Tool
        </h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Scaffold only — the draft board lands here. Below is a live check that the
          frontend can reach the backend and the backend can reach Postgres.
        </p>
      </header>

      <ul className="flex flex-col gap-px overflow-hidden rounded-lg border border-zinc-200 bg-zinc-200 dark:border-zinc-800 dark:bg-zinc-800">
        {probes.map((probe) => (
          <li
            key={probe.label}
            className="flex items-center justify-between gap-4 bg-white px-4 py-3 dark:bg-black"
          >
            <span className="font-medium">{probe.label}</span>
            <span className="flex items-center gap-2 text-sm">
              <span
                aria-hidden
                className={`inline-block h-2 w-2 rounded-full ${
                  probe.state === "ok"
                    ? "bg-emerald-500"
                    : probe.state === "error"
                      ? "bg-red-500"
                      : "bg-zinc-400"
                }`}
              />
              <span className="text-zinc-600 dark:text-zinc-400">{probe.detail}</span>
            </span>
          </li>
        ))}
      </ul>

      <p className="font-mono text-xs text-zinc-500">{API_BASE_URL}</p>
    </main>
  );
}
