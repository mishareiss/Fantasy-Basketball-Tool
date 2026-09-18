import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { MarketPage } from "@/components/market/MarketPage";
import {
  JOKIC,
  WEMBY,
  aliasResponse,
  importResponse,
  marketDelete,
  marketLines,
  marketWrite,
} from "./fixtures";

/**
 * Component tests for the market page.
 *
 * The api client is mocked wholesale — same arrangement as the board's and the importer's —
 * but `ApiError` is kept real, because the failure panel is chosen by the status code on it.
 *
 * Two things are asserted here that are really claims about the DESIGN rather than about the
 * markup, and they are the reason this file exists:
 *
 * * adding a line resolves the player through `importPreview`, i.e. the importer's own
 *   matcher and candidate list, and never through a second one;
 * * every value on screen comes from a re-read of `GET /market/lines` after the write, so a
 *   changed odds pair shows the backend's re-derived number rather than one computed here.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      marketLines: vi.fn(),
      putMarketLine: vi.fn(),
      deleteMarketLine: vi.fn(),
      clearMarketPlayer: vi.fn(),
      importPreview: vi.fn(),
      addPlayerAlias: vi.fn(),
    },
  };
});

const lines = vi.mocked(api.marketLines);
const putLine = vi.mocked(api.putMarketLine);
const deleteLine = vi.mocked(api.deleteMarketLine);
const clearPlayer = vi.mocked(api.clearMarketPlayer);
const preview = vi.mocked(api.importPreview);
const addAlias = vi.mocked(api.addPlayerAlias);

/** The importer's answer when the name resolved certainly — what the add path expects. */
function matchedPreview(playerId = JOKIC.espn_player_id, name = "Nikola Jokic") {
  return importResponse({
    kind: "market_line",
    rows: [
      {
        line: 2,
        source_name: name,
        status: "matched",
        values: { stat: "PTS", line: 24.5 },
        team: null,
        positions: [],
        player_id: playerId,
        player_name: name,
        confidence: 1,
        method: "exact",
        candidates: [],
        note: null,
      },
    ],
  });
}

/** And when it didn't: a review row carrying who it might have been. */
function reviewPreview(name = "Victor Wembanyma") {
  return importResponse({
    kind: "market_line",
    rows: [
      {
        line: 2,
        source_name: name,
        status: "review",
        values: { stat: "BLK", line: 3.5 },
        team: null,
        positions: [],
        player_id: null,
        player_name: null,
        confidence: 0.94,
        method: "fuzzy",
        candidates: [
          { player_id: 5104157, full_name: "Victor Wembanyama", nba_team: "SAS", score: 0.94 },
          { player_id: 3032977, full_name: "Victor Oladipo", nba_team: null, score: 0.61 },
        ],
        note: null,
      },
    ],
  });
}

beforeEach(() => {
  lines.mockResolvedValue(marketLines());
  putLine.mockResolvedValue(marketWrite());
  deleteLine.mockResolvedValue(marketDelete());
  clearPlayer.mockResolvedValue(marketDelete({ deleted: 3, player_removed: true }));
  preview.mockResolvedValue(matchedPreview());
  addAlias.mockResolvedValue(aliasResponse());
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

/** Fill in the add form. The stat is a select over the league's scored stats. */
async function fillAdd(
  user: ReturnType<typeof userEvent.setup>,
  { name = "LeBron James", stat = "PTS", line = "24.5", over = "-115", under = "-105" } = {},
) {
  await user.type(screen.getByLabelText("Player"), name);
  await user.selectOptions(screen.getByLabelText("Stat"), stat);
  await user.type(screen.getByLabelText("Line"), line);
  if (over) await user.type(screen.getByLabelText("Over odds"), over);
  if (under) await user.type(screen.getByLabelText("Under odds"), under);
}

describe("the stored lines", () => {
  it("lists each player once, with his lines and his derived value", async () => {
    render(<MarketPage />);

    const jokic = await screen.findByRole("region", { name: "Nikola Jokic" });
    expect(within(jokic).getByText("84.3")).toBeTruthy();
    expect(within(jokic).getByText("3 stats priced")).toBeTruthy();
    const stats = within(jokic).getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(stats).toEqual(["PTS", "AST", "REB"]);

    // The partial case, which is the one the note exists for: one prop, one stat priced.
    const wemby = screen.getByRole("region", { name: "Victor Wembanyama" });
    expect(within(wemby).getByText("1 stat priced")).toBeTruthy();
  });

  it("shows the stored odds, and an empty box for a side nobody priced", async () => {
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    expect(
      (screen.getByLabelText("Nikola Jokic AST over odds") as HTMLInputElement).value,
    ).toBe("-150");
    expect(
      (screen.getByLabelText("Nikola Jokic PTS over odds") as HTMLInputElement).value,
    ).toBe("");
  });

  it("asks for the book and the season it was told to", async () => {
    const user = userEvent.setup();
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await user.clear(screen.getByLabelText("Book"));
    await user.type(screen.getByLabelText("Book"), "draftkings");
    await user.type(screen.getByLabelText("Season"), "2028");
    await user.click(screen.getByRole("button", { name: "Show lines" }));

    await waitFor(() =>
      expect(lines).toHaveBeenLastCalledWith({ source: "draftkings", season: 2028 }),
    );
  });

  it("prompts for a first line when the book has none yet", async () => {
    lines.mockResolvedValue(marketLines({ players: [] }));
    render(<MarketPage />);

    expect(await screen.findByText(/No lines stored for market, season 2027/)).toBeTruthy();
  });

  it("says a league sync is needed when nothing can be priced", async () => {
    lines.mockResolvedValue(marketLines({ stats: [], players: [] }));
    render(<MarketPage />);

    expect(await screen.findByText("No scoring rules stored yet")).toBeTruthy();
    expect(screen.queryByLabelText("Stat")).toBeNull();
  });

  it("explains a backend that isn't answering rather than showing an empty board", async () => {
    lines.mockRejectedValue(new ApiError("Could not reach the API at http://localhost:8000"));
    render(<MarketPage />);

    expect(await screen.findByText("Can’t reach the API")).toBeTruthy();
  });
});

describe("adding a line", () => {
  it("resolves the name through the importer, then writes it", async () => {
    const user = userEvent.setup();
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user);
    await user.click(screen.getByRole("button", { name: "Add line" }));

    // The importer's own dry run: one row, tab-separated so a comma in a name is safe.
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    const [kind, body] = preview.mock.calls[0];
    expect(kind).toBe("market_line");
    expect(body.delimiter).toBe("\t");
    expect(body.text).toContain("LeBron James\tPTS\t24.5\t-115\t-105");

    expect(putLine).toHaveBeenCalledWith({
      source: "market",
      season: undefined,
      player_id: JOKIC.espn_player_id,
      stat: "PTS",
      line: 24.5,
      over_odds: -115,
      under_odds: -105,
    });
  });

  it("re-reads the list and reports the re-derived value", async () => {
    const user = userEvent.setup();
    const after = {
      ...JOKIC,
      lines: [...JOKIC.lines, { ...JOKIC.lines[0], id: 9, stat: "BLK", line: 0.9 }],
      fantasy_points_per_game: 88.8,
      stats_priced: 4,
    };
    lines.mockResolvedValueOnce(marketLines()).mockResolvedValue(marketLines({ players: [after] }));
    putLine.mockResolvedValue(
      marketWrite({ player: { ...after, fantasy_points_per_game: 88.8 } }),
    );
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user, { name: "Nikola Jokic", stat: "BLK", line: "0.9", over: "", under: "" });
    await user.click(screen.getByRole("button", { name: "Add line" }));

    expect(await screen.findByText("88.8")).toBeTruthy();
    expect(await screen.findByText(/now 88.8 fpts\/g/)).toBeTruthy();
    expect(lines).toHaveBeenCalledTimes(2);
  });

  it("sends an unpriced side as null rather than as a zero", async () => {
    const user = userEvent.setup();
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user, { over: "", under: "" });
    await user.click(screen.getByRole("button", { name: "Add line" }));

    await waitFor(() =>
      expect(putLine).toHaveBeenCalledWith(
        expect.objectContaining({ over_odds: null, under_odds: null }),
      ),
    );
  });

  it("refuses to send a line that isn't a number, and says why", async () => {
    const user = userEvent.setup();
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user, { line: "o24.5" });

    expect(screen.getByText(/The line itself has to be a number/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Add line" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(preview).not.toHaveBeenCalled();
  });

  it("offers the candidates when the name didn't resolve, and never writes on its own", async () => {
    const user = userEvent.setup();
    preview.mockResolvedValue(reviewPreview());
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user, { name: "Victor Wembanyma", stat: "BLK", line: "3.5" });
    await user.click(screen.getByRole("button", { name: "Add line" }));

    expect(await screen.findByText(/Nothing certain for “Victor Wembanyma”/)).toBeTruthy();
    expect(putLine).not.toHaveBeenCalled();
  });

  it("records the alias and writes the line when a candidate is picked", async () => {
    const user = userEvent.setup();
    preview.mockResolvedValue(reviewPreview());
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user, { name: "Victor Wembanyma", stat: "BLK", line: "3.5" });
    await user.click(screen.getByRole("button", { name: "Add line" }));
    const candidates = await screen.findByRole("alert");
    await user.click(within(candidates).getByRole("button", { name: /Victor Wembanyama/ }));

    expect(addAlias).toHaveBeenCalledWith(WEMBY.espn_player_id, {
      source: "market",
      source_name: "Victor Wembanyma",
    });
    await waitFor(() =>
      expect(putLine).toHaveBeenCalledWith(
        expect.objectContaining({ player_id: WEMBY.espn_player_id, stat: "BLK", line: 3.5 }),
      ),
    );
  });

  it("surfaces the backend's own message when the stat can't be priced", async () => {
    const user = userEvent.setup();
    preview.mockRejectedValue(
      new ApiError("/import/market_line responded 422", 422, "unknown stat 'PRA'"),
    );
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await fillAdd(user);
    await user.click(screen.getByRole("button", { name: "Add line" }));

    expect(await screen.findByText("unknown stat 'PRA'")).toBeTruthy();
  });
});

describe("editing a line", () => {
  it("saves the changed odds and re-reads the list", async () => {
    const user = userEvent.setup();
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    const over = screen.getByLabelText("Nikola Jokic AST over odds");
    await user.clear(over);
    await user.type(over, "-400");
    await user.click(screen.getByRole("button", { name: "Save Nikola Jokic AST" }));

    expect(putLine).toHaveBeenCalledWith({
      source: "market",
      season: undefined,
      player_id: JOKIC.espn_player_id,
      stat: "AST",
      line: 9.5,
      over_odds: -400,
      under_odds: 120,
    });
    await waitFor(() => expect(lines).toHaveBeenCalledTimes(2));
  });

  it("keeps Save out of reach until something actually changed", async () => {
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    const save = screen.getByRole("button", { name: "Save Nikola Jokic PTS" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("deleting a line", () => {
  it("asks first, deletes, and drops the line from the list", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const trimmed = { ...JOKIC, lines: JOKIC.lines.slice(0, 2), stats_priced: 2 };
    lines
      .mockResolvedValueOnce(marketLines())
      .mockResolvedValue(marketLines({ players: [trimmed, WEMBY] }));
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await user.click(screen.getByRole("button", { name: "Delete Nikola Jokic REB" }));

    expect(confirm).toHaveBeenCalled();
    expect(deleteLine).toHaveBeenCalledWith(3);
    await waitFor(() =>
      expect(screen.queryByLabelText("Nikola Jokic REB line")).toBeNull(),
    );
  });

  it("writes nothing when the confirm is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await user.click(screen.getByRole("button", { name: "Delete Nikola Jokic REB" }));

    expect(deleteLine).not.toHaveBeenCalled();
  });

  it("warns that the last line takes the player off the board with it", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    deleteLine.mockResolvedValue(
      marketDelete({ player: { ...WEMBY, lines: [], stats_priced: 0 }, player_removed: true }),
    );
    lines
      .mockResolvedValueOnce(marketLines())
      .mockResolvedValue(marketLines({ players: [JOKIC] }));
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Victor Wembanyama" });

    await user.click(screen.getByRole("button", { name: "Delete Victor Wembanyama BLK" }));

    expect(confirm.mock.calls[0][0]).toContain("leaves the market source");
    // He disappears from the list entirely: no lines, no market projection, no phantom row.
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Victor Wembanyama" })).toBeNull(),
    );
    expect(await screen.findByText(/no lines left — removed from market/)).toBeTruthy();
  });

  it("clears a player's whole set in one call", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<MarketPage />);
    await screen.findByRole("region", { name: "Nikola Jokic" });

    await user.click(screen.getByRole("button", { name: "Clear Nikola Jokic" }));

    expect(clearPlayer).toHaveBeenCalledWith({
      source: "market",
      season: undefined,
      player_id: JOKIC.espn_player_id,
    });
  });
});
