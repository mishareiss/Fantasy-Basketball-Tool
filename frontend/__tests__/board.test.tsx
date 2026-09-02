import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { BoardView } from "@/components/board/BoardView";
import { boardResponse, curveResponse, pointGuardResponse, tiersResponse } from "./fixtures";

/**
 * Component tests for the board.
 *
 * The api client is mocked wholesale, so nothing here touches the network — but `ApiError`
 * is kept real, because the empty/unreachable states are chosen by `instanceof` and by the
 * status code on it, and a fake error class would let those branches rot untested.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      board: vi.fn(),
      valuationCurve: vi.fn(),
      valuationTiers: vi.fn(),
    },
  };
});

const board = vi.mocked(api.board);
const valuationCurve = vi.mocked(api.valuationCurve);
const valuationTiers = vi.mocked(api.valuationTiers);

beforeEach(() => {
  board.mockResolvedValue(boardResponse());
  valuationCurve.mockResolvedValue(curveResponse());
  valuationTiers.mockResolvedValue(tiersResponse());
});

afterEach(() => {
  vi.clearAllMocks();
});

/** The rendered `<tr>` for a player, so a test can assert on that row's cells. */
function playerRow(name: string) {
  return screen.getByRole("cell", { name }).closest("tr") as HTMLTableRowElement;
}

/**
 * "Dynasty" is both a control and a column header, so every query for one has to say which.
 */
function control(group: string, name: string) {
  return within(screen.getByRole("group", { name: group })).getByRole("button", { name });
}

function columnHeader(name: RegExp) {
  return within(screen.getByRole("table")).getByRole("button", { name });
}

describe("BoardView", () => {
  it("renders a row per player from the board response", async () => {
    render(<BoardView />);

    expect(await screen.findByRole("cell", { name: "Victor Wembanyama" })).toBeTruthy();
    for (const name of ["Anthony Edwards", "Cade Cunningham", "Tyrese Haliburton"]) {
      expect(screen.getByRole("cell", { name })).toBeTruthy();
    }

    // The selected horizon's value is on the row, and so is the secondary horizon's.
    const row = playerRow("Victor Wembanyama");
    expect(within(row).getByText("62.4")).toBeTruthy();
    expect(within(row).getByText("C")).toBeTruthy();
    expect(within(row).getByText("23")).toBeTruthy();

    // Column headers, including the one named after the selected horizon.
    expect(columnHeader(/^Dynasty/)).toBeTruthy();
    expect(columnHeader(/^Win now/)).toBeTruthy();
    expect(columnHeader(/^ADP/)).toBeTruthy();
    expect(columnHeader(/^×Age/)).toBeTruthy();
  });

  it("shows what the board was built from", async () => {
    render(<BoardView />);

    // Once for the projection source, once for the ADP source — deliberately independent.
    expect(await screen.findAllByText("espn 2027")).toHaveLength(2);
    expect(screen.getByText("Oct 21, 2027")).toBeTruthy();
    expect(screen.getByText("5 of 5")).toBeTruthy();
    expect(screen.getByText("2 tiers · top 4")).toBeTruthy();
  });

  it("refetches with horizon=dynasty when the horizon toggle is used", async () => {
    const user = userEvent.setup();
    render(<BoardView initialControls={{ horizon: "current_year", position: null, limit: 100, tiers: "auto" }} />);

    await screen.findByRole("cell", { name: "Victor Wembanyama" });
    expect(board).toHaveBeenCalledWith(expect.objectContaining({ horizon: "current_year" }));

    await user.click(control("Horizon", "Dynasty"));

    expect(board).toHaveBeenLastCalledWith(expect.objectContaining({ horizon: "dynasty" }));
    expect(board).toHaveBeenCalledTimes(2);
  });

  it("narrows the board when a position filter is chosen", async () => {
    const user = userEvent.setup();
    render(<BoardView />);

    expect(await screen.findByRole("cell", { name: "Victor Wembanyama" })).toBeTruthy();

    board.mockResolvedValue(pointGuardResponse());
    await user.click(control("Position", "PG"));

    expect(await screen.findByRole("cell", { name: "Cade Cunningham" })).toBeTruthy();
    expect(board).toHaveBeenLastCalledWith(expect.objectContaining({ position: "PG" }));
    expect(screen.queryByRole("cell", { name: "Victor Wembanyama" })).toBeNull();
    expect(screen.queryByRole("cell", { name: "Anthony Edwards" })).toBeNull();
  });

  it("reports the controls back to the router shell", async () => {
    const user = userEvent.setup();
    const onControlsChange = vi.fn();
    render(<BoardView onControlsChange={onControlsChange} />);

    await screen.findByRole("cell", { name: "Victor Wembanyama" });
    await user.click(control("Position", "SG"));

    expect(onControlsChange).toHaveBeenCalledWith(
      expect.objectContaining({ position: "SG", horizon: "dynasty" }),
    );
  });
});

describe("tier dividers", () => {
  it("labels each tier from tier_summary and groups its rows under it", async () => {
    render(<BoardView />);

    const tierOne = await screen.findByRole("columnheader", { name: /Tier 1/ });
    expect(tierOne.textContent).toContain("2 players");
    // The band the tier covers, in the selected horizon's units.
    expect(tierOne.textContent).toContain("62.4 → 58.1");

    const tierTwo = screen.getByRole("columnheader", { name: /Tier 2/ });
    expect(tierTwo.textContent).toContain("2 players");
    // The drop that opened it, as a multiple of the board's typical drop.
    expect(tierTwo.textContent).toContain("3.2× drop");

    // Players below the tiered pool land under their own divider, not in a worst tier.
    expect(screen.getByRole("columnheader", { name: /Untiered/ })).toBeTruthy();

    // Each divider heads the tbody holding its own players.
    const groups = screen.getAllByRole("rowgroup");
    const tierTwoBody = tierTwo.closest("tbody") as HTMLTableSectionElement;
    expect(groups).toContain(tierTwoBody);
    expect(within(tierTwoBody).getByRole("cell", { name: "Cade Cunningham" })).toBeTruthy();
    expect(
      within(tierTwoBody).queryByRole("cell", { name: "Victor Wembanyama" }),
    ).toBeNull();
  });

  it("drops the dividers when tiers are toggled off", async () => {
    const user = userEvent.setup();
    render(<BoardView />);

    expect(await screen.findByRole("columnheader", { name: /Tier 1/ })).toBeTruthy();

    board.mockResolvedValue(
      boardResponse({
        tiers: "off",
        tier_pool: 0,
        tier_summary: [],
        players: boardResponse().players.map((player) => ({ ...player, tier: null })),
      }),
    );
    await user.click(control("Tiers", "Off"));

    expect(board).toHaveBeenLastCalledWith(expect.objectContaining({ tiers: "off" }));
    expect(await screen.findByText("off")).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: /Tier 1/ })).toBeNull();
    // The rows are still there — only the dividers went away.
    expect(screen.getByRole("cell", { name: "Victor Wembanyama" })).toBeTruthy();
  });
});

describe("board states", () => {
  it("shows a loading state while the request is in flight", () => {
    board.mockReturnValue(new Promise(() => {}));
    render(<BoardView />);

    expect(screen.getByRole("status").textContent).toMatch(/loading board/i);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("explains an unreachable backend rather than rendering an empty table", async () => {
    board.mockRejectedValue(new ApiError("Could not reach the API at http://localhost:8000"));
    render(<BoardView />);

    expect(await screen.findByText(/can.t reach the api/i)).toBeTruthy();
    expect(screen.getByText(/make backend/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("tells the user to sync when the board 404s", async () => {
    board.mockRejectedValue(
      new ApiError("/players/board responded 404", 404, "No 'espn' projections stored yet"),
    );
    render(<BoardView />);

    expect(await screen.findByText(/nothing is synced/i)).toBeTruthy();
    expect(screen.getByText("make sync")).toBeTruthy();
    expect(screen.getByText(/No 'espn' projections stored yet/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says so when a filter matches nobody", async () => {
    board.mockResolvedValue(boardResponse({ position: "C", players: [], total_ranked: 0 }));
    render(<BoardView />);

    expect(await screen.findByText(/no players match this filter/i)).toBeTruthy();
  });
});

describe("curve & tiers inspector", () => {
  it("stays closed — and unfetched — until it is opened", async () => {
    render(<BoardView />);
    await screen.findByRole("cell", { name: "Victor Wembanyama" });

    expect(valuationCurve).not.toHaveBeenCalled();
    expect(valuationTiers).not.toHaveBeenCalled();
  });

  it("shows the age multipliers and the tier structure when expanded", async () => {
    const user = userEvent.setup();
    render(<BoardView />);
    await screen.findByRole("cell", { name: "Victor Wembanyama" });

    await user.click(screen.getByRole("button", { name: /curve & tiers/i }));

    // The age -> multiplier table, plus the env var that moves each parameter.
    const curve = await screen.findByRole("region", { name: "Age curve" });
    expect(within(curve).getByRole("columnheader", { name: "22" })).toBeTruthy();
    expect(within(curve).getByText("1.08")).toBeTruthy();
    expect(within(curve).getByText("DYNASTY_PRIME_START")).toBeTruthy();

    // The tier structure, for the horizon the board is ranked by. Scoped to its own region:
    // the tier leaders are also players on the board behind it.
    const tiers = screen.getByRole("region", { name: /tier structure — dynasty/i });
    expect(within(tiers).getByRole("cell", { name: "Tier 2" })).toBeTruthy();
    expect(within(tiers).getByRole("cell", { name: "Cade Cunningham" })).toBeTruthy();
    expect(within(tiers).getByText("TIER_GAP_MULTIPLE")).toBeTruthy();
    expect(valuationTiers).toHaveBeenCalledWith("dynasty");
  });
});
