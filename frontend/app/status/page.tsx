import Link from "next/link";

import { BackendStatusPanel } from "@/components/BackendStatus";

export const metadata = {
  title: "Status — Fantasy Basketball Dynasty Tool",
};

/**
 * The full reachability check, kept out of the board's way.
 *
 * "The board is empty" and "the backend isn't running" look the same from the table; this
 * page is where you find out which it is.
 */
export default function StatusPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Status</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          A live check that the frontend can reach the backend and the backend can reach
          Postgres.
        </p>
      </header>

      <BackendStatusPanel />

      <Link href="/" className="text-sm text-sky-600 hover:underline dark:text-sky-400">
        ← Back to the board
      </Link>
    </main>
  );
}
