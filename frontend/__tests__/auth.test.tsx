import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/components/AuthGate";
import { API_BASE_URL, ApiError, api } from "@/lib/api";
import {
  clearAccessToken,
  getAccessToken,
  reportUnauthorized,
  setAccessToken,
  subscribeUnauthorized,
} from "@/lib/auth";

/**
 * The shared-password gate, from three angles:
 *
 * * the store (`lib/auth`) — what it remembers, and what it does when storage is unavailable;
 * * the chokepoint (`lib/api`: `request`) — the header it attaches, and the 401 it acts on.
 *   `fetch` is stubbed here rather than the module, because the header going out IS the point;
 * * the gate (`components/AuthGate`) — when the login screen appears and what unlocks it.
 *
 * `localStorage` is stubbed in-memory, the same way `fetch` is, and for a duller reason than
 * isolation: this runner does not provide a real one. `window.localStorage` here is a bare
 * object with no `getItem` on it, so the store's own try/catch would swallow every write and
 * every test below would assert against a token that was never kept. Stubbing it makes these
 * tests about the store rather than about jsdom, and the throwing variant in the last case of
 * the first block is then the honest test of the private-window path.
 */

const fetchMock = vi.fn<typeof fetch>();

/** A Storage that actually stores, for the environment that doesn't supply one. */
function memoryStorage(): Storage {
  const entries = new Map<string, string>();
  return {
    get length() {
      return entries.size;
    },
    key: (index: number) => [...entries.keys()][index] ?? null,
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => void entries.set(key, String(value)),
    removeItem: (key: string) => void entries.delete(key),
    clear: () => entries.clear(),
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The `Authorization` of the nth outgoing request, however the caller spelled its headers. */
function sentAuthorization(call = 0): string | null {
  return new Headers(fetchMock.mock.calls[call][1]?.headers).get("Authorization");
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async () => jsonResponse({}));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("the token store", () => {
  it("remembers a token and hands the same one back", () => {
    setAccessToken("hoops-2027");
    expect(getAccessToken()).toBe("hoops-2027");
  });

  it("reports nothing when none is stored, and after it is cleared", () => {
    expect(getAccessToken()).toBeNull();
    setAccessToken("hoops-2027");
    clearAccessToken();
    expect(getAccessToken()).toBeNull();
  });

  it("treats an empty password as having none rather than storing a blank", () => {
    setAccessToken("");
    expect(getAccessToken()).toBeNull();
  });

  it("survives a private window, where every localStorage call throws", () => {
    // Not a hypothetical: Safari's private mode and a browser with site data blocked both
    // throw from the accessor itself, and the app has to render a login box rather than crash.
    const throwing = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
      removeItem: () => {
        throw new Error("SecurityError");
      },
    };
    vi.stubGlobal("localStorage", throwing);

    expect(getAccessToken()).toBeNull();
    expect(() => setAccessToken("hoops-2027")).not.toThrow();
    expect(() => clearAccessToken()).not.toThrow();
    expect(() => reportUnauthorized()).not.toThrow();
  });

  it("notifies subscribers of a 401 and drops the token before they run", () => {
    setAccessToken("stale");
    const seen: (string | null)[] = [];
    const unsubscribe = subscribeUnauthorized(() => seen.push(getAccessToken()));

    reportUnauthorized();
    // Cleared BEFORE the listener: a component that re-renders synchronously must not find
    // the refused token still in storage.
    expect(seen).toEqual([null]);

    unsubscribe();
    reportUnauthorized();
    expect(seen).toEqual([null]);
  });
});

describe("request() and the token", () => {
  it("sends no Authorization header when nothing is stored", async () => {
    await api.board();
    expect(sentAuthorization()).toBeNull();
  });

  it("attaches the stored token as a Bearer header, on every call", async () => {
    setAccessToken("hoops-2027");

    await api.board();
    await api.masterBoard("dynasty");

    expect(sentAuthorization(0)).toBe("Bearer hoops-2027");
    expect(sentAuthorization(1)).toBe("Bearer hoops-2027");
  });

  it("attaches it to writes too, without disturbing the body's Content-Type", async () => {
    setAccessToken("hoops-2027");

    await api.putMasterOrder([7, 4, 9], "dynasty");

    const init = fetchMock.mock.calls[0][1];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer hoops-2027");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ ordered_player_ids: [7, 4, 9] });
  });

  it("clears the stored token on a 401 and still throws, so no page hangs", async () => {
    setAccessToken("wrong-password");
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Missing or invalid access token." }, 401));

    const error = await api.board().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(getAccessToken()).toBeNull();
  });

  it("keeps the token on any other failure — a 404 is not a password problem", async () => {
    setAccessToken("hoops-2027");
    fetchMock.mockResolvedValue(jsonResponse({ detail: "No 'espn' projections stored yet." }, 404));

    await api.board().catch(() => undefined);

    expect(getAccessToken()).toBe("hoops-2027");
  });

  it("keeps it through an unreachable backend, which says nothing about the password", async () => {
    setAccessToken("hoops-2027");
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await api.board().catch(() => undefined);

    expect(getAccessToken()).toBe("hoops-2027");
  });

  it("probes a GATED endpoint to check a token, never an open one", async () => {
    // `/health` answers without a password by design (backend app/auth.py: OPEN_PATHS), so a
    // check against it would accept every wrong password there is.
    await api.checkAccess();
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${API_BASE_URL}/import/kinds`);
  });
});

describe("AuthGate", () => {
  function gated() {
    return render(
      <AuthGate>
        <p>the board</p>
      </AuthGate>,
    );
  }

  it("shows the page and no password box until something is refused", () => {
    gated();
    expect(screen.getByText("the board")).toBeDefined();
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("replaces the page with a login screen when a call comes back 401", async () => {
    gated();

    reportUnauthorized();

    expect(await screen.findByLabelText("Password")).toBeDefined();
    // Unmounted, not covered: that is what makes the children refetch on the way back in.
    expect(screen.queryByText("the board")).toBeNull();
  });

  it("stores the password, verifies it, and hands the page back", async () => {
    const user = userEvent.setup();
    gated();
    reportUnauthorized();
    await screen.findByLabelText("Password");

    await user.type(screen.getByLabelText("Password"), "hoops-2027");
    await user.click(screen.getByRole("button", { name: "Open the board" }));

    expect(await screen.findByText("the board")).toBeDefined();
    expect(getAccessToken()).toBe("hoops-2027");
    // The check went out WITH the password just typed — the point of storing it first.
    expect(sentAuthorization()).toBe("Bearer hoops-2027");
  });

  it("says so and stays locked when the backend refuses the password", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Missing or invalid access token." }, 401));
    gated();
    reportUnauthorized();
    await screen.findByLabelText("Password");

    await user.type(screen.getByLabelText("Password"), "not-it");
    await user.click(screen.getByRole("button", { name: "Open the board" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/didn't work/);
    expect(screen.queryByText("the board")).toBeNull();
    // Refused, so not kept: the next attempt starts from nothing rather than from a wrong one.
    expect(getAccessToken()).toBeNull();
  });

  it("blames the backend, not the password, when the backend is unreachable", async () => {
    const user = userEvent.setup();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    gated();
    reportUnauthorized();
    await screen.findByLabelText("Password");

    await user.type(screen.getByLabelText("Password"), "hoops-2027");
    await user.click(screen.getByRole("button", { name: "Open the board" }));

    expect((await screen.findByRole("alert")).textContent).toContain(API_BASE_URL);
    expect(screen.queryByText("the board")).toBeNull();
  });

  it("opens on any password when the backend has no token configured", async () => {
    // A backend with APP_ACCESS_TOKEN unset answers 200 to everything, so the check passes and
    // the gate opens. It is a shared password, and only the backend can judge one.
    const user = userEvent.setup();
    gated();
    reportUnauthorized();
    await screen.findByLabelText("Password");

    await user.type(screen.getByLabelText("Password"), "literally anything");
    await user.click(screen.getByRole("button", { name: "Open the board" }));

    expect(await screen.findByText("the board")).toBeDefined();
  });

  it("refuses to submit an empty box, so a stray Enter costs nothing", async () => {
    const user = userEvent.setup();
    gated();
    reportUnauthorized();
    await screen.findByLabelText("Password");

    await user.click(screen.getByLabelText("Password"));
    await user.keyboard("{Enter}");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText("the board")).toBeNull();
  });
});
