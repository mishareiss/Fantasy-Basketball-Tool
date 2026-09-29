"use client";

import { useEffect, useState, type ReactNode } from "react";

import { ApiError, api } from "@/lib/api";
import { setAccessToken, subscribeUnauthorized } from "@/lib/auth";

/**
 * The login screen, and the one thing that puts it on screen.
 *
 * It is LAZY on purpose: nothing is gated until the backend says so. The gate appears when a
 * call comes back 401 (`lib/auth.ts: reportUnauthorized`), and never before. That is what keeps
 * a local `make backend` — which configures no APP_ACCESS_TOKEN and therefore refuses nothing —
 * exactly as it was, with no password to type before the board renders. Point
 * NEXT_PUBLIC_API_BASE_URL at a deployed backend instead and the first board read 401s, which
 * is the moment the password is actually needed.
 *
 * Locking UNMOUNTS the children rather than covering them, and that is the whole refetch
 * story: every page in this app loads its data in an effect, so mounting again after a
 * successful sign-in re-reads it with the new token. Nothing needs a refresh handler, and no
 * page keeps stale rows from before the 401 behind a dialog.
 *
 * What it is not: identity. An open backend accepts any password here, because the backend is
 * the only thing that can judge one and an open one judges nothing.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [locked, setLocked] = useState(false);

  useEffect(() => subscribeUnauthorized(() => setLocked(true)), []);

  if (locked) return <LoginScreen onUnlocked={() => setLocked(false)} />;
  return <>{children}</>;
}

type Phase = "idle" | "checking";

function LoginScreen({ onUnlocked }: { onUnlocked: () => void }) {
  const [password, setPassword] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const token = password.trim();
    if (!token || phase === "checking") return;

    setPhase("checking");
    setError(null);
    // Store first, because the check goes out through the same `request()` as everything else
    // and reads the token from storage. A wrong one is cleared again by the 401 itself.
    setAccessToken(token);
    try {
      await api.checkAccess();
      setPassword("");
      onUnlocked();
    } catch (caught) {
      const status = caught instanceof ApiError ? caught.status : undefined;
      setError(
        status === 401
          ? "That password didn't work."
          : // Anything else is the backend being unreachable, not the password being wrong,
            // and saying "wrong password" about a stopped server sends someone hunting for
            // the wrong problem.
            (caught as Error).message,
      );
      setPhase("idle");
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-black"
      >
        <div className="flex flex-col gap-1">
          <h1 className="text-base font-semibold">Fantasy Basketball Dynasty Tool</h1>
          <p className="text-sm text-zinc-500">
            This board is shared. Enter the password to open it.
          </p>
        </div>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Password</span>
          <input
            type="password"
            name="access-token"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-950"
          />
        </label>

        {error ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={!password.trim() || phase === "checking"}
          className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {phase === "checking" ? "Checking…" : "Open the board"}
        </button>
      </form>
    </main>
  );
}
