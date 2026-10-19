import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TeamView } from "@/components/team/TeamPage";
import { ProjectionTag } from "@/components/team/ProjectionTag";
import { api } from "@/lib/api";
import { imputedNote, projectionTag, shortSeason } from "@/lib/team";
import { FREE_AGENTS, calendar, freeAgentsFor, playerCard, startsFor, teamDay } from "./teamFixtures";

/**
 * Where a `projected` number came from: the "25-26 actual" / "stale proj" tags beside FPTS/G,
 * and the card's "est." markers for stats ESPN doesn't project.
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
    },
  };
});

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

describe("the projection tag text", () => {
  it("names last season's actual, a stale projection, and nothing for ESPN's own", () => {
    expect(shortSeason(2026)).toBe("25-26");
    expect(projectionTag("last_season_actual", 2026)?.label).toBe("25-26 actual");
    expect(projectionTag("last_season_actual", 2026)?.title).toMatch(/hasn't projected him/);
    expect(projectionTag("stale_projection", 2026)?.label).toBe("stale proj");
    expect(projectionTag("stale_projection", 2026)?.title).toMatch(/25-26 projection/);
    expect(projectionTag("espn", 2027)).toBeNull();
    expect(projectionTag(null, null)).toBeNull();
    expect(projectionTag(undefined, undefined)).toBeNull();
  });

  it("writes the imputation note in a fixed order, or nothing", () => {
    expect(imputedNote(["PF", "TD", "DREB", "OREB", "DD"])).toBe(
      "DD/TD/OREB/DREB/PF estimated: ESPN doesn't project them",
    );
    expect(imputedNote([])).toBeNull();
    expect(imputedNote(undefined)).toBeNull();
  });

  it("renders with its tooltip, and renders nothing for ESPN", () => {
    const { container, rerender } = render(
      <ProjectionTag source="last_season_actual" season={2026} />,
    );
    const tag = container.querySelector<HTMLElement>("[data-projection-tag]")!;
    expect(tag.textContent).toBe("25-26 actual");
    expect(tag.title).toMatch(/actually scored per game in 25-26/);
    rerender(<ProjectionTag source="espn" season={2027} />);
    expect(container.querySelector("[data-projection-tag]")).toBeNull();
  });
});

describe("on the Team tab", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("localStorage", memoryStorage());
    vi.mocked(api.teamCalendar).mockResolvedValue(calendar());
    vi.mocked(api.teamStarts).mockImplementation(async ({ matchupId, view } = {}) =>
      startsFor(matchupId, view),
    );
    vi.mocked(api.teamDay).mockImplementation(async ({ sp = 1, basis = "projected" } = {}) => {
      const day = teamDay(sp, { basis });
      return {
        ...day,
        slots: day.slots.map((row) =>
          row.player?.name === "Luka Doncic"
            ? {
                ...row,
                player: {
                  ...row.player,
                  stats: row.player.stats && {
                    ...row.player.stats,
                    projection_source: "last_season_actual" as const,
                    projection_season: 2026,
                  },
                },
              }
            : row,
        ),
      };
    });
    const pool = FREE_AGENTS.map((row, index) =>
      index === 0
        ? { ...row, stats: { fantasy_ppg: 20, games: 60, projection_source: "stale_projection" as const, projection_season: 2026 } }
        : { ...row, stats: { fantasy_ppg: 20, games: 60, projection_source: "espn" as const, projection_season: 2027 } },
    );
    vi.mocked(api.freeAgents).mockImplementation(async (params = {}) => freeAgentsFor(params, pool));
    vi.mocked(api.teamPlayer).mockImplementation(async (id) => {
      const card = playerCard(id);
      return {
        ...card,
        stat_columns: ["PTS", "REB", "DD"],
        stats: {
          ...card.stats,
          projected: {
            ...card.stats.projected!,
            per_game: { PTS: 28.1, REB: 8.4, DD: 0.55 },
            projection_source: "espn",
            projection_season: 2027,
            imputed: ["DD", "DREB", "OREB", "PF", "TD"],
          },
        },
      };
    });
    vi.mocked(api.watchlist).mockResolvedValue({ season: 2027, entries: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function renderPage() {
    render(<TeamView initialDay={null} initialBasis="projected" onUrlChange={vi.fn()} />);
  }

  it("tags a roster player's FPTS/G and a free agent's when it isn't ESPN's projection", async () => {
    renderPage();
    const roster = await screen.findByRole("region", { name: "Roster" });
    const luka = await within(roster).findByRole("button", { name: "Luka Doncic" });
    const row = luka.closest("tr")!;
    const fpts = row.querySelector<HTMLElement>("td[data-fpts]")!;
    expect(within(fpts).getByText("25-26 actual")).toBeTruthy();
    // Nobody else on the roster is tagged.
    expect(roster.querySelectorAll("[data-projection-tag]")).toHaveLength(1);

    const waivers = screen.getByRole("region", { name: "Waiver list" });
    const guy = await within(waivers).findByRole("button", { name: "Free Agent Guy" });
    const cell = guy.closest("tr")!.querySelector<HTMLElement>("td[data-fa-fpts]")!;
    expect(within(cell).getByText("stale proj").title).toMatch(/25-26 projection/);
    expect(waivers.querySelectorAll("[data-projection-tag]")).toHaveLength(1);
  });

  it("marks estimated stats on the card's Projected row and says why", async () => {
    const user = userEvent.setup();
    renderPage();
    const roster = await screen.findByRole("region", { name: "Roster" });
    await user.click(await within(roster).findByRole("button", { name: "Luka Doncic" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Per game");

    const projected = dialog.querySelector<HTMLElement>('tr[data-card-row="projected"]')!;
    const estimated = projected.querySelectorAll<HTMLElement>("td[data-estimated]");
    expect(estimated).toHaveLength(1);
    expect(estimated[0].textContent).toBe("0.6est.");
    // ESPN's own PTS isn't marked, and an ESPN projection carries no source tag.
    expect(projected.querySelector("[data-projection-tag]")).toBeNull();
    expect(dialog.querySelector("[data-imputed-note]")?.textContent).toBe(
      "DD/TD/OREB/DREB/PF estimated: ESPN doesn't project them",
    );
  });
});
