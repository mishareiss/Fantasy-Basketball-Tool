import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TeamView } from "@/components/team/TeamPage";
import { api, type FreeAgentsParams } from "@/lib/api";
import {
  FREE_AGENTS,
  calendar,
  freeAgentsFor,
  planResponse,
  startsFor,
  teamDay,
} from "./teamFixtures";

/**
 * The starts tracker on the Team tab (task 35): the strip's `filled/7` with its open and
 * benched markers, the matchup summary bar (and the Plan view's delta over Actual), and the
 * "Wk G" column on the roster and the waiver list. The api client is mocked wholesale.
 *
 * Matchup 1's fixture numbers (teamFixtures.startsFor), Actual: day 1 5/7 with 2 open, day 3
 * 3/7 with 3 open and 1 benched, day 5 4/7 with 1 open and 2 benched, the rest 7/7. Totals
 * 33/42, 6 open, 3 benched, Proj 2,140 (as set 1,995). The plan fills one more on day 3.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      teamCalendar: vi.fn(),
      teamDay: vi.fn(),
      teamStarts: vi.fn(),
      freeAgents: vi.fn(),
      teamPlayer: vi.fn(),
      watchlist: vi.fn(),
      plan: vi.fn(),
      saveLineup: vi.fn(),
    },
  };
});

const getStarts = vi.mocked(api.teamStarts);

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
  vi.mocked(api.teamCalendar).mockResolvedValue(calendar());
  vi.mocked(api.teamDay).mockImplementation(async ({ sp = 1, basis = "projected", view } = {}) => {
    const day = teamDay(sp, { basis, ...(view === "plan" ? { view: "plan" as const } : {}) });
    // In the Plan view Luka is dropped after tonight's games: one game left on the plan roster.
    if (view !== "plan") return day;
    return {
      ...day,
      slots: day.slots.map((row) =>
        row.player?.id === 1 ? { ...row, player: { ...row.player, matchup_games_left: 1 } } : row,
      ),
    };
  });
  getStarts.mockImplementation(async ({ matchupId, view } = {}) => startsFor(matchupId, view));
  vi.mocked(api.freeAgents).mockImplementation(async (params: FreeAgentsParams = {}) =>
    freeAgentsFor(params, [
      { ...FREE_AGENTS[0]!, matchup_games_left: 4 },
      ...FREE_AGENTS.slice(1),
    ]),
  );
  vi.mocked(api.watchlist).mockResolvedValue({ season: 2027, entries: [] });
  vi.mocked(api.plan).mockResolvedValue(planResponse());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(props: Partial<React.ComponentProps<typeof TeamView>> = {}) {
  render(<TeamView initialDay={null} initialBasis="projected" onUrlChange={vi.fn()} {...props} />);
}

const stripDay = (sp: number) => document.querySelector<HTMLElement>(`[data-strip-day="${sp}"]`)!;
const summary = () => document.querySelector<HTMLElement>("[data-starts-summary]");

async function ready() {
  await screen.findByRole("button", { name: "Luka Doncic" });
  await waitFor(() => expect(summary()).not.toBeNull());
}

describe("the day strip", () => {
  it("shows filled/7 per day with amber open and red benched markers", async () => {
    renderPage();
    await ready();

    expect(getStarts).toHaveBeenCalledWith({ matchupId: 1, view: "actual" });
    const shown = [1, 2, 3, 4, 5, 6].map(
      (sp) => stripDay(sp).querySelector("[data-strip-starts]")?.textContent,
    );
    expect(shown).toEqual(["5/7", "7/7", "3/7", "7/7", "4/7", "7/7"]);

    expect(stripDay(1).querySelector("[data-strip-open]")?.textContent).toBe("2 open");
    expect(stripDay(1).querySelector("[data-strip-benched]")).toBeNull();
    const day3 = stripDay(3);
    expect(day3.querySelector("[data-strip-open]")?.textContent).toBe("3 open");
    expect(day3.querySelector("[data-strip-open]")?.className).toContain("amber");
    expect(day3.querySelector("[data-strip-benched]")?.textContent).toBe("1 benched");
    expect(day3.querySelector("[data-strip-benched]")?.className).toContain("rose");
    expect(stripDay(2).querySelector("[data-strip-open], [data-strip-benched]")).toBeNull();
    // The games count isn't drawn once the starts are in.
    expect(stripDay(2).textContent).not.toMatch(/\d G$/);
  });

  it("keeps the games count and the benched-with-games names in the tooltip", async () => {
    renderPage();
    await ready();
    const title = stripDay(2).title;
    expect(title).toContain("7 of 7 starts filled (7 possible)");
    expect(title).toContain(`${calendar().days[1]!.my_games} of my players play`);
    expect(title).toContain("No slot for: Bench Wing");
    expect(stripDay(3).title).toContain("3 open: nobody on the roster can fill them");
  });

  it("falls back to the game counts when the starts can't be read", async () => {
    getStarts.mockRejectedValue(new Error("down"));
    renderPage();
    await screen.findByRole("button", { name: "Luka Doncic" });
    expect(stripDay(1).textContent).toContain(`${calendar().days[0]!.my_games} G`);
    expect(summary()).toBeNull();
  });

  it("follows the roster's view: the Plan view reads and shows the plan's starts", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(screen.getByRole("button", { name: "Plan view" }));

    await waitFor(() => expect(getStarts).toHaveBeenLastCalledWith({ matchupId: 1, view: "plan" }));
    await waitFor(() =>
      expect(stripDay(3).querySelector("[data-strip-starts]")?.textContent).toBe("4/7"),
    );
    expect(stripDay(3).querySelector("[data-strip-open]")?.textContent).toBe("2 open");
    expect(stripDay(3).querySelector("[data-strip-benched]")?.textContent).toBe("1 benched");
    expect(stripDay(5).querySelector("[data-strip-benched]")?.textContent).toBe("2 benched");
  });

  it("reads once per matchup, not once per day, and again for another matchup", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(stripDay(3));
    await screen.findByRole("heading", { name: /10\/22/ });
    expect(getStarts).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Next matchup ›" }));
    await waitFor(() => expect(getStarts).toHaveBeenLastCalledWith({ matchupId: 2, view: "actual" }));
    await waitFor(() =>
      expect(stripDay(7).querySelector("[data-strip-starts]")?.textContent).toBe("7/7"),
    );
  });
});

describe("the summary bar", () => {
  it("sums the matchup in the Actual view, with no delta", async () => {
    renderPage();
    await ready();
    expect(summary()!.textContent).toBe(
      "Starts 33/42 · 6 open · 3 benched · Proj 2,140 pts (as set 1,995)",
    );
    expect(document.querySelector("[data-starts-delta]")).toBeNull();
    // Above the roster table.
    const table = document.querySelector("[data-roster-table]")!;
    expect(summary()!.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("adds the plan's gain over Actual in the Plan view", async () => {
    renderPage({ initialView: "plan" });
    await ready();
    await waitFor(() => expect(document.querySelector("[data-starts-delta]")).not.toBeNull());
    expect(summary()!.textContent).toContain("Starts 34/42 · 5 open · 3 benched · Proj 2,220 pts");
    const delta = document.querySelector<HTMLElement>("[data-starts-delta]")!;
    expect(delta.textContent).toBe("+1 start, +80 pts vs Actual");
    expect(delta.className).toContain("emerald");
  });
});

describe("Wk G", () => {
  const roster = () => screen.getByRole("region", { name: "Roster" });
  const waivers = () => screen.getByRole("region", { name: "Waiver list" });

  it("is a roster column between Game and the stats", async () => {
    renderPage();
    await ready();
    const headers = within(roster())
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    expect(headers.slice(3, 6)).toEqual(["Game", "Wk G", "PTS"]);
    const luka = roster().querySelector('[data-slot-player="1"] [data-wk-games]');
    expect(luka?.textContent).toBe("3");
    // A player the server sent no number for shows a dash, and an empty slot spans the column.
    expect(roster().querySelector('[data-slot-player="2"] [data-wk-games]')?.textContent).toBe("–");
    const empty = roster().querySelector('tr[data-slot="SG"] td[colspan]') as HTMLTableCellElement;
    expect(empty.colSpan).toBe(headers.length - 2);
  });

  it("shows the plan roster's number in the Plan view", async () => {
    renderPage({ initialView: "plan" });
    await ready();
    const luka = roster().querySelector('[data-slot-player="1"] [data-wk-games]');
    expect(luka?.textContent).toBe("1");
    const header = within(roster()).getByRole("columnheader", { name: "Wk G" });
    expect(header.title).toMatch(/plan roster/);
  });

  it("is on the waiver list beside G", async () => {
    renderPage();
    await ready();
    await within(waivers()).findByRole("button", { name: "Free Agent Guy" });
    const headers = within(waivers())
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    expect(headers.slice(-3)).toEqual(["G", "Wk G", "FPTS/G"]);
    const rows = waivers().querySelectorAll("[data-waiver-player] [data-wk-games]");
    expect(Array.from(rows).map((cell) => cell.textContent)).toEqual(["4", "–", "–"]);
  });

  it("stays narrow: no fixed-width table, one short nowrap header", async () => {
    renderPage();
    await ready();
    const table = document.querySelector<HTMLElement>("[data-roster-table]")!;
    expect(table.className).toContain("w-full");
    expect(table.className).not.toMatch(/min-w-/);
    const header = within(roster()).getByRole("columnheader", { name: "Wk G" });
    expect(header.className).toContain("w-8");
    expect(header.className).toContain("whitespace-nowrap");
  });
});
