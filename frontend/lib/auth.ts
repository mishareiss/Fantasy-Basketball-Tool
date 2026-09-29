/**
 * The browser's half of the shared-password gate (backend: app/auth.py).
 *
 * One token for the whole app, kept in `localStorage` so a phone on the couch on draft day
 * doesn't ask for it again every time the tab is reopened. It is a shared password for two
 * people, not a user account: there is nothing to enrol, nothing to reset, and no claim that
 * the token identifies who is holding it.
 *
 * Two things live here rather than in `lib/api.ts`, because they are state and `api.ts` is a
 * transport:
 *
 * * where the token is (`getAccessToken` / `setAccessToken` / `clearAccessToken`), and
 * * the one event the UI reacts to (`reportUnauthorized` / `subscribeUnauthorized`) — the
 *   backend refused us, so whatever is on screen is no longer a view anyone is entitled to.
 *
 * Every `localStorage` touch is wrapped: in a private window, or with site data blocked, the
 * accessor itself throws, and an app that cannot remember a password should still ask for one
 * rather than fail to render.
 */

const STORAGE_KEY = "fbb.access-token";

/** Read straight through on every call — no module-level cache.
 *
 * A cache would buy one synchronous `getItem` per fetch and cost correctness in the three
 * places that matter: another tab signing in, a test writing the key directly, and a stale
 * copy surviving a `clear` that threw. */
export function getAccessToken(): string | null {
  try {
    if (typeof window === "undefined") return null;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored ? stored : null;
  } catch {
    return null;
  }
}

/** Remember the password the user just typed. Empty is the same as having none. */
export function setAccessToken(token: string): void {
  try {
    if (typeof window === "undefined") return;
    if (token) window.localStorage.setItem(STORAGE_KEY, token);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable. The token is lost on reload, which is the right failure: the user
    // types it again rather than being locked out of a page that renders nothing.
  }
}

export function clearAccessToken(): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing was stored in the first place.
  }
}

type Listener = () => void;

const listeners = new Set<Listener>();

/**
 * The backend answered 401: forget the token and tell whoever is listening.
 *
 * Called from the single `request()` in `lib/api.ts`, so every call in the app reports this
 * the same way. Clearing FIRST matters — a listener that re-renders immediately must not find
 * the refused token still sitting in storage.
 */
export function reportUnauthorized(): void {
  clearAccessToken();
  for (const listener of [...listeners]) listener();
}

/** Subscribe to that event. Returns the unsubscribe, so it drops straight into `useEffect`. */
export function subscribeUnauthorized(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
