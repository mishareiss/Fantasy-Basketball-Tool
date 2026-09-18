import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { BoardView } from "@/components/board/BoardView";
import {
  ADP_SOURCE,
  DYNASTY_RANKING_SOURCE,
  PROJECTION_SOURCE,
  REDRAFT_RANKING_SOURCE,
  boardResponse,
  consensusResponse,
  curveResponse,
  percentileFor,
  sourcesResponse,
  tiersResponse,
} from "./fixtures";

/**
 * Component tests for the consensus board.
 *
 * Mocked the same way board.test.tsx mocks the value board — the api client wholesale, but
 * `ApiError` kept real, because the empty/unreachable states are chosen by the status code on
 * it. `api.sources` is what decides which chips exist, so the tests drive eligibility through
 * it rather than through a horizon constant.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      board: vi.fn(),
      sources: vi.fn(),
      boardConsensus: vi.fn(),
      valuationCurve: vi.fn(),
      valuationTiers: vi.fn(),
    },
  };
});

const board = vi.mocked(api.board);
const sources = vi.mocked(api.sources);
const boardConsensus = vi.mocked(api.boardConsensus);

beforeEach(() => {
  board.mockResolvedValue(boardResponse());
  vi.mocked(api.valuationCurve).mockResolvedValue(curveResponse());
  vi.mocked(api.valuationTiers).mockResolvedValue(tiersResponse());
  sources.mockResolvedValue(sourcesResponse("dynasty"));
  boardConsensus.mockImplementation(async (params = {}) =>
    consensusResponse(
      { method: params.method ?? "rank", horizon: params.horizon ?? "dynasty" },
      params.sources ?? [],
    ),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

const ALL_DYNASTY = [PROJECTION_SOURCE.id, ADP_SOURCE.id, DYNASTY_RANKING_SOURCE.id];

/** Render the board already switched to the consensus view. */
function renderConsensus() {
  return render(
    <BoardView
      initialControls={{
        mode: "consensus",
        horizon: "dynasty",
        position: null,
        limit: 100,
        tiers: "auto",
        method: "rank",
      }}
    />,
  );
}

function sourceChip(name: string) {
  return screen.getByRole("checkbox", { name: new RegExp(name) });
}

function playerRow(name: string) {
  return screen.getByRole("cell", { name }).closest("tr") as HTMLTableRowElement;
}

function columnHeader(name: RegExp) {
  return within(screen.getByRole("table")).getByRole("button", { name });
}

function control(group: string, name: string) {
  return within(screen.getByRole("group", { name: group })).getByRole("button", { name });
}

describe("the source panel", () => {
  it("lists the sources this horizon makes eligible, with their coverage", async () => {
    renderConsensus();

    expect(await screen.findByRole("checkbox", { name: /espn projection/ })).toBeTruthy();
    expect(sourceChip("espn ADP")).toBeTruthy();
    expect(sourceChip("Dizzle Dynasty")).toBeTruthy();
    expect(sources).toHaveBeenCalledWith("dynasty");

    // The coverage is on the chip: a 40-name board and a 101-name market are not the same
    // evidence, and the count is where that starts being visible.
    expect(screen.getByText(/Imported board · 40/)).toBeTruthy();
  });

  it("starts with every eligible source selected", async () => {
    renderConsensus();

    await screen.findByRole("checkbox", { name: /espn projection/ });
    expect(boardConsensus).toHaveBeenCalledWith(
      expect.objectContaining({ sources: ALL_DYNASTY, horizon: "dynasty" }),
    );
  });

  it("refetches the board with just the sources left ticked", async () => {
    const user = userEvent.setup();
    renderConsensus();

    await screen.findByRole("checkbox", { name: /espn ADP/ });
    await user.click(sourceChip("espn ADP"));

    expect(boardConsensus).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sources: [PROJECTION_SOURCE.id, DYNASTY_RANKING_SOURCE.id],
      }),
    );
  });

  it("prompts for a source instead of a blank table when everything is unticked", async () => {
    const user = userEvent.setup();
    renderConsensus();

    await screen.findByRole("checkbox", { name: /espn projection/ });
    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(await screen.findByText(/Pick a source to build a consensus from/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("the consensus table", () => {
  beforeEach(() => {
    boardConsensus.mockImplementation(async (params = {}) =>
      consensusResponse({ method: params.method ?? "rank" }, [
        PROJECTION_SOURCE.id,
        DYNASTY_RANKING_SOURCE.id,
      ]),
    );
  });

  it("gives every selected source a column, plus a consensus column", async () => {
    renderConsensus();
    await screen.findByRole("table");

    expect(columnHeader(/^espn projection/)).toBeTruthy();
    expect(columnHeader(/^Dizzle Dynasty/)).toBeTruthy();
    expect(columnHeader(/^Consensus Rank/)).toBeTruthy();
    expect(columnHeader(/^Spread/)).toBeTruthy();
    expect(columnHeader(/^Player/)).toBeTruthy();
  });

  it("puts each source's rank on the row, and the average of them in the consensus", async () => {
    renderConsensus();

    await screen.findByRole("cell", { name: "Giannis Antetokounmpo" });
    const row = playerRow("Giannis Antetokounmpo");

    expect(within(row).getByText("2")).toBeTruthy(); // the projection has him 2nd
    expect(within(row).getByText("17")).toBeTruthy(); // the dynasty board has him 17th
    expect(within(row).getByText("9.5")).toBeTruthy(); // (2 + 17) / 2
  });

  it("orders the rows by the consensus", async () => {
    renderConsensus();

    await screen.findByRole("cell", { name: "Victor Wembanyama" });
    const names = screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelectorAll("td")[1]?.textContent);

    // 1.0, then 7.0, then 9.5, then the rookie's 12.0.
    expect(names).toEqual([
      "Victor Wembanyama",
      "Cade Cunningham",
      "Giannis Antetokounmpo",
      "Cameron Boozer",
    ]);
  });

  it("shades the row by how far apart the sources are, and names the band", async () => {
    renderConsensus();

    await screen.findByRole("cell", { name: "Giannis Antetokounmpo" });

    // 2nd vs 17th is a 8.5x gap — the row worth staring at.
    const loud = playerRow("Giannis Antetokounmpo").querySelector("[data-disagreement]");
    expect(loud?.getAttribute("data-disagreement")).toBe("severe");
    expect(loud?.getAttribute("title")).toMatch(/Severe disagreement/);

    // 8th vs 6th is nothing, and must not be shaded as though it were.
    const quiet = playerRow("Cade Cunningham").querySelector("[data-disagreement]");
    expect(quiet?.getAttribute("data-disagreement")).toBe("agree");
  });

  it("shows an em dash where a source has no opinion, and says the coverage is thin", async () => {
    renderConsensus();

    await screen.findByRole("cell", { name: "Cameron Boozer" });
    const row = playerRow("Cameron Boozer");

    expect(within(row).getByText("1/2")).toBeTruthy();
    // His consensus is the one rank he has, NOT that averaged with a fabricated last place.
    expect(within(row).getByText("12.0")).toBeTruthy();
    expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("sorts by a source column when its header is clicked", async () => {
    const user = userEvent.setup();
    renderConsensus();

    await screen.findByRole("cell", { name: "Victor Wembanyama" });
    await user.click(columnHeader(/^Dizzle Dynasty/));

    const names = screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelectorAll("td")[1]?.textContent);

    // Dizzle's own order: 1, 6, 12, 17.
    expect(names).toEqual([
      "Victor Wembanyama",
      "Cade Cunningham",
      "Cameron Boozer",
      "Giannis Antetokounmpo",
    ]);
  });

  it("sorts by the spread, which is how you find the disagreements", async () => {
    const user = userEvent.setup();
    renderConsensus();

    await screen.findByRole("cell", { name: "Victor Wembanyama" });
    await user.click(columnHeader(/^Spread/));

    const first = screen.getAllByRole("row")[1].querySelectorAll("td")[1]?.textContent;
    expect(first).toBe("Giannis Antetokounmpo");
  });
});

describe("the rank / percentile toggle", () => {
  it("re-averages the board and restates every source cell", async () => {
    const user = userEvent.setup();
    renderConsensus();

    await screen.findByRole("cell", { name: "Giannis Antetokounmpo" });
    expect(within(playerRow("Giannis Antetokounmpo")).getByText("17")).toBeTruthy();

    await user.click(control("Averaged by", "Percentile"));

    expect(boardConsensus).toHaveBeenLastCalledWith(
      expect.objectContaining({ method: "percentile" }),
    );
    expect(await screen.findByRole("columnheader", { name: /Consensus Percentile/ })).toBeTruthy();

    const row = playerRow("Giannis Antetokounmpo");
    expect(within(row).getByText(percentileFor(17).toFixed(1))).toBeTruthy();
    expect(within(row).getByText(percentileFor(2).toFixed(1))).toBeTruthy();
  });
});

describe("the horizon governs eligibility", () => {
  it("refetches the sources and the board, retiring the list the other horizon tagged", async () => {
    const user = userEvent.setup();
    renderConsensus();

    expect(await screen.findByRole("checkbox", { name: /Dizzle Dynasty/ })).toBeTruthy();

    sources.mockResolvedValue(sourcesResponse("current_year"));
    await user.click(control("Horizon", "Win now"));

    expect(sources).toHaveBeenLastCalledWith("current_year");
    expect(await screen.findByRole("checkbox", { name: /Rest of Season/ })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /Dizzle Dynasty/ })).toBeNull();

    // The value and market sources stay ticked across the switch; the retired list is simply
    // dropped rather than leaving a dead id in the request.
    expect(boardConsensus).toHaveBeenLastCalledWith(
      expect.objectContaining({
        horizon: "current_year",
        sources: [PROJECTION_SOURCE.id, ADP_SOURCE.id],
      }),
    );
    expect(REDRAFT_RANKING_SOURCE.horizon).toBe("redraft");
  });
});

describe("consensus states", () => {
  it("shows a loading state while the first request is in flight", () => {
    sources.mockReturnValue(new Promise(() => {}));
    renderConsensus();

    expect(screen.getByRole("status").textContent).toMatch(/Loading/);
  });

  it("explains an unreachable backend rather than rendering an empty table", async () => {
    sources.mockRejectedValue(new ApiError("Could not reach the API at http://localhost:8000"));
    renderConsensus();

    expect(await screen.findByText(/Can’t reach the API/)).toBeTruthy();
  });

  it("tells the user to sync when there is nothing to rank with", async () => {
    sources.mockResolvedValue({
      horizon: "dynasty",
      ranking_horizon: "dynasty",
      pool_size: 0,
      sources: [],
    });
    renderConsensus();

    expect(await screen.findByText(/Pick a source to build a consensus from/)).toBeTruthy();
    expect(screen.getByText(/make sync/)).toBeTruthy();
  });

  it("surfaces a rejected consensus request without losing the source panel", async () => {
    boardConsensus.mockRejectedValue(new ApiError("/board/consensus responded 400", 400));
    renderConsensus();

    expect(await screen.findByText(/The board request failed/)).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /espn projection/ })).toBeTruthy();
  });
});

describe("the value board is still there", () => {
  it("switches back to the single-source board, which fetches the board endpoint", async () => {
    const user = userEvent.setup();
    renderConsensus();

    await screen.findByRole("checkbox", { name: /espn projection/ });
    expect(board).not.toHaveBeenCalled();

    await user.click(control("Board", "Value"));

    expect(await screen.findByRole("cell", { name: "Victor Wembanyama" })).toBeTruthy();
    expect(board).toHaveBeenCalledWith(expect.objectContaining({ horizon: "dynasty" }));
    // And the consensus view is gone, chips and all.
    expect(screen.queryByRole("checkbox", { name: /espn ADP/ })).toBeNull();
  });
});
