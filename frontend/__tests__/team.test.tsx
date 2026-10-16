import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { TeamView } from "@/components/team/TeamPage";
import {
  FREE_AGENTS,
  calendar,
  freeAgentsFor,
  playerCard,
  teamDay,
} from "./teamFixtures";

/**
 * Component tests for the Team tab, with the api client mocked wholesale and `ApiError` kept
 * real (the 409 and 422 paths are chosen by its status).
 *
 * `TeamView` is rendered directly, as the board suite renders `BoardView`: the URL is a prop
 * in and a callback out, so "?day updates" is an assertion on `onUrlChange`.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      teamCalendar: vi.fn(),
      teamDay: vi.fn(),
      saveLineup: vi.fn(),
      autoFillLineup: vi.fn(),
      autoFillPlanLineup: vi.fn(),
      syncLineup: vi.fn(),
      freeAgents: vi.fn(),
      teamPlayer: vi.fn(),
      watchlist: vi.fn(),
      watchPlayer: vi.fn(),
      unwatchPlayer: vi.fn(),
    },
  };
});

const getCalendar = vi.mocked(api.teamCalendar);
const getDay = vi.mocked(api.teamDay);
const saveLineup = vi.mocked(api.saveLineup);
const syncLineup = vi.mocked(api.syncLineup);
const autoFill = vi.mocked(api.autoFillLineup);
const autoFillPlan = vi.mocked(api.autoFillPlanLineup);
const freeAgents = vi.mocked(api.freeAgents);
const teamPlayer = vi.mocked(api.teamPlayer);
const getWatchlist = vi.mocked(api.watchlist);
const watchPlayer = vi.mocked(api.watchPlayer);
const unwatchPlayer = vi.mocked(api.unwatchPlayer);

/** An in-memory `localStorage`: this runner has no real one (see auth.test.tsx). */
function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("localStorage", memoryStorage());
  getCalendar.mockResolvedValue(calendar());
  getDay.mockImplementation(async ({ sp = 1, basis = "projected" } = {}) =>
    teamDay(sp, { basis }),
  );
  freeAgents.mockImplementation(async (params = {}) => freeAgentsFor(params));
  teamPlayer.mockImplementation(async (id) => playerCard(id));
  getWatchlist.mockResolvedValue({ season: 2027, entries: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(props: Partial<React.ComponentProps<typeof TeamView>> = {}) {
  const onUrlChange = vi.fn();
  render(
    <TeamView initialDay={null} initialBasis="projected" onUrlChange={onUrlChange} {...props} />,
  );
  return { onUrlChange };
}

const roster = () => screen.getByRole("region", { name: "Roster" });
const waivers = () => screen.getByRole("region", { name: "Waiver list" });

async function ready() {
  await screen.findByRole("button", { name: "Luka Doncic" });
  await within(waivers()).findByRole("button", { name: "Free Agent Guy" });
}

function slotRow(slot: string, nth = 0): HTMLElement {
  return roster().querySelectorAll<HTMLElement>(`tr[data-slot="${slot}"]`)[nth];
}

describe("the roster", () => {
  it("draws every slot, empty ones included, and dims a player with no game", async () => {
    renderPage();
    await ready();

    const rows = roster().querySelectorAll("tbody tr");
    expect(rows).toHaveLength(10);
    expect(within(slotRow("SG")).getByText("Empty")).toBeTruthy();
    expect(within(slotRow("IR")).getByText("Empty")).toBeTruthy();
    expect(slotRow("C").dataset.noGame).toBe("true");
    expect(slotRow("PG").dataset.noGame).toBeUndefined();
    // The game is two stacked lines, the server's halves, never a split string.
    const gameCell = slotRow("PG").querySelector<HTMLElement>("td[data-game] [data-game-matchup]")!;
    expect([...gameCell.children].map((line) => line.textContent)).toEqual(["vs GSW", "7:00 PM"]);
    expect(gameCell.title).toBe("vs GSW 7:00 PM");
    // A missing stat line is an em dash, not a zero.
    expect(within(slotRow("C")).getAllByText("—").length).toBeGreaterThan(0);
    // The injury badge for a non-healthy player only.
    expect(within(slotRow("SF")).getByText("DTD")).toBeTruthy();
    expect(within(slotRow("PG")).queryByText("DTD")).toBeNull();
    // The header strip.
    expect(within(roster()).getByText("Emzep")).toBeTruthy();
    expect(within(roster()).getByText("ESPN lineup")).toBeTruthy();
    expect(within(roster()).getByText("Acquisitions 2 / 7 this matchup")).toBeTruthy();
  });

  it("opens on the effective period before tip-off and says when the season starts", async () => {
    renderPage();
    await ready();
    expect(getDay).toHaveBeenCalledWith({ sp: 1, basis: "projected" });
    expect(screen.getByText("Season starts Tue 10/20")).toBeTruthy();
    expect(screen.getByText("Tue 10/20 · Matchup 1")).toBeTruthy();
  });

  it("steps days with prev/next/today, writing ?day and re-reading the roster", async () => {
    const user = userEvent.setup();
    const { onUrlChange } = renderPage();
    await ready();

    await user.click(screen.getByRole("button", { name: "Next day" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=2");
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 2, basis: "projected" }));
    await screen.findByText("Wed 10/21 · Matchup 1");

    await user.click(screen.getByRole("button", { name: "Next day" }));
    await user.click(screen.getByRole("button", { name: "Previous day" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=2");

    await user.click(screen.getByRole("button", { name: "Today" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=1");
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 1, basis: "projected" }));

    await user.click(screen.getByRole("button", { name: /Next matchup/ }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=7");
  });

  it("opens on ?day when the URL has one", async () => {
    renderPage({ initialDay: 4, initialBasis: "projected" });
    await ready();
    expect(getDay).toHaveBeenCalledWith({ sp: 4, basis: "projected" });
  });

  it("shows the no-team state on a 409", async () => {
    getDay.mockRejectedValue(new ApiError("/team/day responded 409", 409, "No team is ours"));
    renderPage();
    expect(await screen.findByText("No team synced yet")).toBeTruthy();
  });
});

describe("the basis toggle", () => {
  it("disables Actual until games have been played", async () => {
    renderPage();
    await ready();
    const actual = within(roster()).getByRole("button", { name: "Actual" }) as HTMLButtonElement;
    expect(actual.disabled).toBe(true);
    expect(within(roster()).getByText("Available once games have been played")).toBeTruthy();
  });

  it("drives `basis` from Actual and the split select", async () => {
    getDay.mockImplementation(async ({ sp = 1, basis = "projected" } = {}) =>
      teamDay(sp, { basis, actuals_available: true }),
    );
    const user = userEvent.setup();
    const { onUrlChange } = renderPage();
    await ready();

    await user.click(within(roster()).getByRole("button", { name: "Actual" }));
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 1, basis: "season" }));
    await user.selectOptions(within(roster()).getByRole("combobox", { name: "Stats split" }), "l7");
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 1, basis: "l7" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=1&basis=l7");
  });
});

describe("moving a player", () => {
  it("highlights legal targets, saves the full lineup, and re-reads the day", async () => {
    saveLineup.mockImplementation(async (sp) => ({ ...teamDay(sp), days_written: 1 }));
    const user = userEvent.setup();
    renderPage();
    await ready();

    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    const targets = [...roster().querySelectorAll<HTMLElement>('tr[data-target="true"]')];
    expect(targets.map((row) => row.dataset.slot)).toEqual(["SG", "UT", "UT", "BE", "IR"]);
    expect(slotRow("SF").dataset.target).toBeUndefined();

    const reads = getDay.mock.calls.length;
    await user.click(within(slotRow("SG")).getByRole("button", { name: "Here" }));
    expect(saveLineup).toHaveBeenCalledWith(1, {
      assignments: [
        { player_id: 1, slot_id: 1 },
        { player_id: 2, slot_id: 2 },
        { player_id: 3, slot_id: 4 },
        { player_id: 4, slot_id: 11 },
        { player_id: 5, slot_id: 12 },
      ],
      through_sp: null,
    });
    await waitFor(() => expect(getDay.mock.calls.length).toBe(reads + 1));
  });

  it("sends through_sp = the matchup's last day when the box is checked", async () => {
    saveLineup.mockImplementation(async (sp) => ({ ...teamDay(sp), days_written: 6 }));
    const user = userEvent.setup();
    renderPage();
    await ready();

    await user.click(screen.getByRole("checkbox", { name: "Apply through end of matchup" }));
    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    await user.click(within(slotRow("UT", 0)).getByRole("button", { name: "Here" }));
    expect(saveLineup).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        through_sp: 6,
        assignments: expect.arrayContaining([
          { player_id: 1, slot_id: 11 },
          { player_id: 4, slot_id: 0 },
        ]),
      }),
    );
  });

  it("shows a 422's reasons and leaves the grid as it was", async () => {
    saveLineup.mockRejectedValue(
      new ApiError("/team/lineup/1 responded 422", 422, undefined, [
        "Luka Doncic (1) can't play SG",
      ]),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    const reads = getDay.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    await user.click(within(slotRow("SG")).getByRole("button", { name: "Here" }));
    expect(await screen.findByText("Luka Doncic (1) can't play SG")).toBeTruthy();
    expect(getDay.mock.calls.length).toBe(reads);
    expect(within(slotRow("PG")).getByText("Luka Doncic")).toBeTruthy();
    expect(within(slotRow("SG")).getByText("Empty")).toBeTruthy();
  });

  it("shows the server's warnings as a note", async () => {
    saveLineup.mockImplementation(async (sp) => ({
      ...teamDay(sp),
      days_written: 1,
      warnings: ["Luka Doncic (1) is on IR but not listed as injured"],
    }));
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    await user.click(within(slotRow("IR")).getByRole("button", { name: "Here" }));
    expect(
      await screen.findByText("Luka Doncic (1) is on IR but not listed as injured"),
    ).toBeTruthy();
  });

  it("cancels on Escape and on a second click of Move", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();

    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    expect(within(roster()).getAllByRole("button", { name: "Here" }).length).toBeGreaterThan(0);
    await user.keyboard("{Escape}");
    expect(within(roster()).queryAllByRole("button", { name: "Here" })).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    expect(within(roster()).queryAllByRole("button", { name: "Here" })).toHaveLength(0);
    expect(saveLineup).not.toHaveBeenCalled();
  });
});

describe("Auto-fill lineup", () => {
  const filled = (sp: number, over: Record<string, unknown> = {}) => ({
    ...teamDay(sp, { source: "manual" }),
    days_written: 1,
    changed: true,
    days: [
      { sp, starters_playing: 7, slots: 7, benched_playing: ["Ty Jerome", "Rudy Gobert"] },
    ],
    ...over,
  });

  it("fills the Actual lineup, says who sits, and re-reads the day", async () => {
    autoFill.mockImplementation(async ({ sp }) => filled(sp));
    const user = userEvent.setup();
    renderPage();
    await ready();
    const reads = getDay.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Auto-fill lineup" }));
    expect(autoFill).toHaveBeenCalledWith({ sp: 1, through_sp: null });
    expect(autoFillPlan).not.toHaveBeenCalled();
    expect(saveLineup).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        "7 of 7 starters playing. Benched with games: Ty Jerome, Rudy Gobert.",
      ),
    ).toBeTruthy();
    await waitFor(() => expect(getDay.mock.calls.length).toBe(reads + 1));
  });

  it("has its own through-end-of-matchup box", async () => {
    autoFill.mockImplementation(async ({ sp }) => filled(sp, { days_written: 6 }));
    const user = userEvent.setup();
    renderPage();
    await ready();

    await user.click(screen.getByRole("checkbox", { name: "through end of matchup" }));
    expect(
      (screen.getByRole("checkbox", { name: "Apply through end of matchup" }) as HTMLInputElement)
        .checked,
    ).toBe(false);
    await user.click(screen.getByRole("button", { name: "Auto-fill lineup" }));
    expect(autoFill).toHaveBeenCalledWith({ sp: 1, through_sp: 6 });
    expect(await screen.findByText(/Set 6 days\./)).toBeTruthy();
  });

  it("says when the lineup was already optimal", async () => {
    autoFill.mockImplementation(async ({ sp }) => filled(sp, { changed: false, days_written: 0 }));
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(screen.getByRole("button", { name: "Auto-fill lineup" }));
    expect(await screen.findByText("Lineup already optimal")).toBeTruthy();
  });

  it("shows a refusal's reasons", async () => {
    autoFill.mockRejectedValue(
      new ApiError("/team/lineup/auto responded 422", 422, undefined, ["through_sp 0 is before sp 1"]),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(screen.getByRole("button", { name: "Auto-fill lineup" }));
    expect(await screen.findByText("through_sp 0 is before sp 1")).toBeTruthy();
  });
});

describe("Sync lineup from ESPN", () => {
  const pulled = { team_id: 8, first_sp: 1, last_sp: 13, days: 13, assignments: 130 };

  it("pulls straight away when the day isn't edited here, then re-reads the day", async () => {
    syncLineup.mockResolvedValue(pulled);
    const user = userEvent.setup();
    renderPage();
    await ready();
    const reads = getDay.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Sync lineup from ESPN" }));
    expect(syncLineup).toHaveBeenCalledWith({});
    await waitFor(() => expect(getDay.mock.calls.length).toBe(reads + 1));
  });

  it("asks inline first when the day was edited here", async () => {
    getDay.mockImplementation(async ({ sp = 1 } = {}) => teamDay(sp, { source: "manual" }));
    syncLineup.mockResolvedValue(pulled);
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(within(roster()).getByText("Edited here")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Sync lineup from ESPN" }));
    expect(syncLineup).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Keep my edits" }));
    expect(syncLineup).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Sync lineup from ESPN" }));
    await user.click(screen.getByRole("button", { name: "Replace with ESPN" }));
    expect(syncLineup).toHaveBeenCalledTimes(1);
  });
});

describe("the waiver panel", () => {
  it("turns chips, mode and filters into freeAgents params", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(freeAgents).toHaveBeenLastCalledWith(
      expect.objectContaining({ days: [1], mode: "none", basis: "projected", offset: 0 }),
    );

    const panel = waivers();
    await user.click(within(panel).getByRole("button", { name: /^Thu\s*10\/22$/ }));
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ days: [1, 3] })),
    );
    const plays = within(panel).getByRole("group", { name: "Plays" });
    await user.click(within(plays).getByRole("button", { name: "All" }));
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Sort waiver list" }), "games");
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(
        expect.objectContaining({ days: [1, 3], mode: "all", sort: "games" }),
      ),
    );
    // The status filter is gone: the panel never sends it.
    expect(freeAgents.mock.calls.every(([p]) => p?.status === undefined)).toBe(true);

    await user.type(within(panel).getByRole("searchbox", { name: "Search waiver list" }), "guy");
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ q: "guy" })),
    );
    // Debounced: the keystrokes before it settled didn't each fire a read.
    expect(freeAgents.mock.calls.some(([p]) => p?.q === "g")).toBe(false);
  });

  it("draws a game cell per chosen day, and both waiver badges", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: /^Wed\s*10\/21$/ }));
    await user.click(within(waivers()).getByRole("button", { name: /^Thu\s*10\/22$/ }));

    await waitFor(() =>
      expect(waivers().querySelectorAll('th[data-waiver-column]')).toHaveLength(3),
    );
    const row = waivers().querySelector<HTMLElement>('tr[data-waiver-player="101"]')!;
    const cells = row.querySelectorAll<HTMLElement>("td[data-game-cell]");
    const lines = (cell: HTMLElement) =>
      [...(cell.querySelector("[data-game-matchup]")?.children ?? [])].map((l) => l.textContent);
    expect([...cells].map(lines)).toEqual([["@ NYK", "7:00 PM"], [], ["@ NYK", "7:00 PM"]]);
    expect(row.querySelector("[data-games-in-days]")!.textContent).toBe("2");

    expect(within(waivers()).getByText("W · clears Thu 10/22")).toBeTruthy();
    expect(within(waivers()).getByText("W · clear time unknown")).toBeTruthy();
  });

  it("appends the next page on Show more", async () => {
    freeAgents.mockImplementation(async (params = {}) =>
      freeAgentsFor({ ...params, limit: 2 }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(within(waivers()).getByText("3 players")).toBeTruthy();
    expect(waivers().querySelectorAll("tr[data-waiver-player]")).toHaveLength(2);

    await user.click(within(waivers()).getByRole("button", { name: /Show more/ }));
    expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 2 }));
    await waitFor(() =>
      expect(waivers().querySelectorAll("tr[data-waiver-player]")).toHaveLength(3),
    );
    expect(within(waivers()).queryByRole("button", { name: /Show more/ })).toBeNull();
  });

  it("doesn't re-read the list when the roster's day changes", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    const reads = freeAgents.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Next day" }));
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 2, basis: "projected" }));
    await screen.findByText("Wed 10/21 · Matchup 1");
    expect(freeAgents.mock.calls.length).toBe(reads);
  });

  it("remembers its mode per browser", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: "Any" }));
    expect(JSON.parse(window.localStorage.getItem("team.waivers")!)).toEqual({
      mode: "any",
      basis: "projected",
      positions: [],
    });
  });
});

describe("the position chips", () => {
  const chips = () => within(waivers()).getByRole("group", { name: "Positions" });

  it("toggle any combination, none selected meaning every position", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ positions: [] }));

    // Clicked out of order, sent in PG..C order.
    await user.click(within(chips()).getByRole("button", { name: "PF" }));
    await user.click(within(chips()).getByRole("button", { name: "SF" }));
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(
        expect.objectContaining({ positions: ["SF", "PF"] }),
      ),
    );
    expect(within(chips()).getByRole("button", { name: "SF" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(within(chips()).getByRole("button", { name: "PG" }).getAttribute("aria-pressed")).toBe(
      "false",
    );

    await user.click(within(chips()).getByRole("button", { name: "SF" }));
    await user.click(within(chips()).getByRole("button", { name: "PF" }));
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ positions: [] })),
    );
  });

  it("are remembered with the panel's other prefs", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(chips()).getByRole("button", { name: "C" }));
    expect(JSON.parse(window.localStorage.getItem("team.waivers")!).positions).toEqual(["C"]);
  });

  it("come back from storage on the next visit", async () => {
    window.localStorage.setItem(
      "team.waivers",
      JSON.stringify({ mode: "none", basis: "projected", positions: ["PF", "SF", "XX"] }),
    );
    renderPage();
    await ready();
    expect(freeAgents).toHaveBeenLastCalledWith(
      expect.objectContaining({ positions: ["SF", "PF"] }),
    );
  });
});

describe("the watchlist", () => {
  const show = () => within(waivers()).getByRole("group", { name: "Show" });
  const row = (id: number) =>
    waivers().querySelector<HTMLElement>(`tr[data-waiver-player="${id}"]`)!;
  const star = (id: number) => row(id).querySelector<HTMLButtonElement>("[data-watch-star]")!;

  it("replaces the status filter with All | Watchlist, which sends watchlist=true", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(within(waivers()).queryByRole("button", { name: "Free agents" })).toBeNull();
    expect(within(waivers()).queryByRole("button", { name: "Waivers" })).toBeNull();
    expect(within(show()).getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe(
      "true",
    );

    await user.click(within(show()).getByRole("button", { name: "Watchlist" }));
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ watchlist: true })),
    );
  });

  it("says how to start one when the watchlist is empty", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(show()).getByRole("button", { name: "Watchlist" }));
    expect(
      await within(waivers()).findByText(
        "No one on your watchlist yet — star players to add them.",
      ),
    ).toBeTruthy();
  });

  it("stars a row at once, writes, and reads the list again", async () => {
    let watched = false;
    freeAgents.mockImplementation(async (params = {}) =>
      freeAgentsFor(params, FREE_AGENTS.map((r) => (r.id === 101 ? { ...r, watched } : r))),
    );
    let finish: () => void = () => {};
    watchPlayer.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = () => {
            watched = true;
            resolve({} as never);
          };
        }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(star(101).dataset.watchStar).toBe("off");
    const reads = freeAgents.mock.calls.length;

    await user.click(star(101));
    // Optimistic: filled before the server has answered.
    expect(star(101).dataset.watchStar).toBe("on");
    expect(watchPlayer).toHaveBeenCalledWith(101);
    expect(freeAgents.mock.calls.length).toBe(reads);

    finish();
    await waitFor(() => expect(freeAgents.mock.calls.length).toBe(reads + 1));
    expect(star(101).dataset.watchStar).toBe("on");
  });

  it("unstars through DELETE", async () => {
    freeAgents.mockImplementation(async (params = {}) =>
      freeAgentsFor(params, FREE_AGENTS.map((r) => ({ ...r, watched: r.id === 102 }))),
    );
    unwatchPlayer.mockResolvedValue({ player_id: 102, removed: true });
    const user = userEvent.setup();
    renderPage();
    await ready();
    expect(star(102).dataset.watchStar).toBe("on");
    await user.click(star(102));
    expect(unwatchPlayer).toHaveBeenCalledWith(102);
  });

  it("puts the star back and says why when the write fails", async () => {
    watchPlayer.mockRejectedValue(new ApiError("nope", 404, "No player 101"));
    const user = userEvent.setup();
    renderPage();
    await ready();
    const reads = freeAgents.mock.calls.length;

    await user.click(star(101));
    expect(
      await within(waivers()).findByText("Couldn’t watch Free Agent Guy: No player 101"),
    ).toBeTruthy();
    expect(star(101).dataset.watchStar).toBe("off");
    expect(freeAgents.mock.calls.length).toBe(reads);
  });
});

describe("the player card", () => {
  it("opens from the roster on the roster's day, and Escape returns focus to the name", async () => {
    const user = userEvent.setup();
    renderPage({ initialDay: 5 });
    await ready();

    const name = screen.getByRole("button", { name: "Luka Doncic" });
    await user.click(name);
    expect(teamPlayer).toHaveBeenCalledWith(1, 5);

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Per game");
    expect(within(dialog).getByText(/Emzep/)).toBeTruthy();
    const season = dialog.querySelector<HTMLElement>('tr[data-card-row="season"]')!;
    expect(within(season).getByText("2026-27 Season")).toBeTruthy();
    expect(within(season).getByText("No games yet")).toBeTruthy();
    const l7 = dialog.querySelector<HTMLElement>('tr[data-card-row="l7"]')!;
    expect(within(l7).getByText("45.5")).toBeTruthy();
    expect(within(l7).getByText("—")).toBeTruthy(); // REB absent, not 0

    expect(within(dialog).getByText(/Matchup 1 · Oct 20–25/)).toBeTruthy();
    expect(within(dialog).getByText(/Matchup 2 · Oct 26–Nov 1/)).toBeTruthy();
    expect(dialog.querySelectorAll("li[data-card-day]")).toHaveLength(7);
    const cardGame = dialog.querySelector<HTMLElement>('li[data-card-day="5"] [data-game-matchup]')!;
    expect([...cardGame.children].map((line) => line.textContent)).toEqual(["vs GSW", "7:00 PM"]);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(name);
  });

  it("opens from the waiver list on the first chosen day", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: /^Fri\s*10\/23$/ }));
    await user.click(within(waivers()).getByRole("button", { name: /^Tue\s*10\/20$/ }));
    await waitFor(() =>
      expect(freeAgents).toHaveBeenLastCalledWith(expect.objectContaining({ days: [4] })),
    );

    await user.click(within(waivers()).getByRole("button", { name: FREE_AGENTS[1].name }));
    expect(teamPlayer).toHaveBeenCalledWith(102, 4);
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });
});

describe("the card's star", () => {
  it("watches from the card header, re-reads the card, and has the waiver list re-read", async () => {
    let watched = false;
    teamPlayer.mockImplementation(async (id) => ({ ...playerCard(id), watched }));
    watchPlayer.mockImplementation(async () => {
      watched = true;
      return {} as never;
    });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(screen.getByRole("button", { name: "Luka Doncic" }));
    const dialog = await screen.findByRole("dialog");
    const star = await within(dialog).findByRole("button", { name: "Watch Luka Doncic" });
    const cardReads = teamPlayer.mock.calls.length;
    const listReads = freeAgents.mock.calls.length;

    await user.click(star);
    expect(watchPlayer).toHaveBeenCalledWith(1);
    await waitFor(() => expect(teamPlayer.mock.calls.length).toBe(cardReads + 1));
    await waitFor(() => expect(freeAgents.mock.calls.length).toBe(listReads + 1));
    expect(within(dialog).getByRole("button", { name: "Unwatch Luka Doncic" })).toBeTruthy();
  });

  it("puts the star back and says why when the write fails", async () => {
    teamPlayer.mockImplementation(async (id) => ({ ...playerCard(id), watched: true }));
    unwatchPlayer.mockRejectedValue(new ApiError("down", 500, "database is down"));
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(screen.getByRole("button", { name: "Luka Doncic" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("button", { name: "Unwatch Luka Doncic" }));

    expect(
      await within(dialog).findByText("Couldn’t unwatch him: database is down"),
    ).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Unwatch Luka Doncic" })).toBeTruthy();
  });
});
