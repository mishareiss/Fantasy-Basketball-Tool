"use client";

/**
 * The bordered card the board's empty/error states are made of, and now the importer's too.
 *
 * Lifted out of `board/BoardStates` unchanged when the importer needed the same shape: two
 * pages explaining two different nothings should at least look like the same application.
 */

export function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950">
      <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2>
      <div className="mt-2 flex flex-col gap-3 text-sm text-zinc-600 dark:text-zinc-400">
        {children}
      </div>
    </div>
  );
}

/** A shell command, or any other thing meant to be typed exactly. */
export function Command({ children }: { children: string }) {
  return (
    <code className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
      {children}
    </code>
  );
}
