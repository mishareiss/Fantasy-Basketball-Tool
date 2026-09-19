import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { MasterBoardPage } from "@/components/masterboard/MasterBoardPage";
import {
  PAGE,
  addCut,
  moveCut,
  normalizeCuts,
  nudgedCut,
  removeCut,
  shownTier,
  tierBands,
} from "@/lib/masterboard";
import {
  MASTER_ASIDE,
  MASTER_CUTS,
  MASTER_SEEDS,
  deepMasterBoard,
  masterBoard,
} from "./fixtures";

/**
 * Component tests for our own board.
 *
 * The api client is mocked wholesale — the same arrangement the board, importer and market
 * suites use — with `ApiError` kept real so the failure panel is chosen by its status.
 *
 * Four of the assertions here are claims about the DESIGN rather than about markup, and they
 * are why this file exists:
 *
 * * a reorder sends the WHOLE order, never the window on screen, so a move made at the top
 *   of a 1000-deep board cannot drop the 900 players below it;
 * * the board that comes BACK is what ends up rendered — the optimistic move is a guess, and
 *   a server that reflowed differently wins;
 * * flipping the horizon changes the reference column and NOTHING about the order, which is
 *   the entire difference between this page and /board/consensus;
 * * the page never has the whole board in the DOM at once, and search — not scrolling — is
 *   how you reach a player who isn't in the window.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      masterBoard: vi.fn(),
      putMasterOrder: vi.fn(),
      putMasterEntry: vi.fn(),
      putMasterTiers: vi.fn(),
      reseedMasterTiers: vi.fn(),
      resetMasterBoard: vi.fn(),
    },
  };
});

const board = vi.mocked(api.masterBoard);
const putOrder = vi.mocked(api.putMasterOrder);
const putEntry = vi.mocked(api.putMasterEntry);
const resetBoard = vi.mocked(api.resetMasterBoard);
const putTiers = vi.mocked(api.putMasterTiers);
const reseedTiers = vi.mocked(api.reseedMasterTiers);

const [WEMBY, BOOZER, GIANNIS, PAUL] = MASTER_SEEDS;
const AYTON = MASTER_ASIDE;

/**
 * The order actually on screen, read off the rows rather than off the ranks they print.
 *
 * `[data-player]` rather than every `tr`, because the tbody also carries the tier dividers
 * and the gaps between them — rows with no player on them by design.
 */
function onScreen(): number[] {
  return Array.from(document.querySelectorAll("tbody tr[data-player]")).map((row) =>
    Number(row.getAttribute("data-player")),
  );
}

function rowFor(playerId: number): HTMLElement {
  const row = document.querySelector(`tbody tr[data-player="${playerId}"]`);
  if (!row) throw new Error(`no row for player ${playerId}`);
  return row as HTMLElement;
}

/** Every divider drawn, as the rank it starts the band at — the page's own cut_ranks. */
function dividersOnScreen(): number[] {
  return Array.from(document.querySelectorAll("tbody tr[data-tier-divider]")).map((row) =>
    Number(row.getAttribute("data-tier-start")),
  );
}

/** The tier pill a player's row prints, or null when it prints none. */
function tierOf(playerId: number): string | null {
  return rowFor(playerId).querySelector("[data-tier]")?.textContent ?? null;
}

/** Wait for the first read to land. Everything below starts from a board on screen. */
async function openBoard() {
  render(<MasterBoardPage />);
  await screen.findByText(WEMBY.name);
}

beforeEach(() => {
  board.mockResolvedValue(masterBoard());
  putOrder.mockImplementation(async (ids) => masterBoard({}, { order: ids }));
  putEntry.mockResolvedValue(masterBoard());
  resetBoard.mockResolvedValue(masterBoard());
  // A tier write answers with the whole board, like every other mutation here. The cuts it
  // was sent become the cuts it answers with, so "the board reflects the write" is a real
  // claim rather than a restatement of the optimistic update — there isn't one for tiers.
  putTiers.mockImplementation(async (scope, cutRanks) =>
    masterBoard({}, { cuts: { ...MASTER_CUTS, [scope]: cutRanks } }),
  );
  reseedTiers.mockResolvedValue(masterBoard());
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("the board", () => {
  it("puts your rank first and the field's beside it, in your order", async () => {
    await openBoard();

    expect(onScreen()).toEqual([
      WEMBY.espn_player_id,
      BOOZER.espn_player_id,
      GIANNIS.espn_player_id,
      PAUL.espn_player_id,
    ]);
    expect(rowFor(BOOZER.espn_player_id).getAttribute("data-rank")).toBe("2");
    // The reference column: where the dynasty consensus has him, not where we do.
    expect(within(rowFor(BOOZER.espn_player_id)).getByText("12")).toBeTruthy();
  });

  it("colours the gap by direction, and prints it as spots above the field", async () => {
    await openBoard();

    // We have Boozer at 2 and the field at 12 — ten spots out on a limb, and the chip says
    // so with a glyph as well as a colour.
    const ours = rowFor(BOOZER.espn_player_id).querySelector("[data-edge]");
    expect(ours?.getAttribute("data-edge")).toBe("above");
    expect(ours?.textContent).toContain("+10");
    expect(ours?.textContent).toContain("▲");

    // Giannis is the other way up: our 3 against the field's 2.
    const theirs = rowFor(GIANNIS.espn_player_id).querySelector("[data-edge]");
    expect(theirs?.getAttribute("data-edge")).toBe("below");
    expect(theirs?.textContent).toContain("-1");
    expect(theirs?.textContent).toContain("▼");

    // And Wembanyama, where we and the room agree.
    expect(
      rowFor(WEMBY.espn_player_id).querySelector("[data-edge]")?.getAttribute("data-edge"),
    ).toBe("level");
  });

  it("badges the arrival the reconcile placed and the player nobody ranks any more", async () => {
    await openBoard();

    expect(within(rowFor(BOOZER.espn_player_id)).getByText("New")).toBeTruthy();
    expect(within(rowFor(PAUL.espn_player_id)).getByText("Stale")).toBeTruthy();
    // A stale player's reference column is empty rather than zero: nothing backs it.
    expect(rowFor(PAUL.espn_player_id).querySelector("[data-edge]")).toBeNull();
    expect(within(rowFor(PAUL.espn_player_id)).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows each player's tag, and an unset one as an empty control", async () => {
    await openBoard();

    expect(within(rowFor(BOOZER.espn_player_id)).getByText("Target")).toBeTruthy();
    expect(within(rowFor(GIANNIS.espn_player_id)).getByText("Fade")).toBeTruthy();
    expect(within(rowFor(WEMBY.espn_player_id)).getByText("tag")).toBeTruthy();
  });

  it("keeps the set-aside players in a tray, out of the order", async () => {
    await openBoard();

    expect(onScreen()).not.toContain(AYTON.espn_player_id);
    const tray = screen.getByRole("region", { name: "Set aside" });
    expect(within(tray).getByText(AYTON.name)).toBeTruthy();
    // Parked, not forgotten: what we wrote about him is still there.
    expect(within(tray).getByText("Only at a discount")).toBeTruthy();
  });

  it("says it is reading rather than showing an empty board", () => {
    board.mockReturnValue(new Promise(() => {}));
    render(<MasterBoardPage />);

    expect(screen.getByRole("status").textContent).toContain("Reading your board");
  });

  it("explains a backend that isn't answering", async () => {
    board.mockRejectedValue(new ApiError("Could not reach the API at http://localhost:8000"));
    render(<MasterBoardPage />);

    expect((await screen.findByRole("alert")).textContent).toContain("Can’t reach the API");
  });

  it("treats a board with nobody on it as the starting state, not a failure", async () => {
    board.mockResolvedValue(
      masterBoard({ players: [], set_aside: [], total_ranked: 0, added: 0, stale: 0 }),
    );
    render(<MasterBoardPage />);

    expect(await screen.findByText("Nothing to rank yet")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("reordering", () => {
  it("sends the FULL order when a player is nudged up one", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(screen.getByRole("button", { name: `Move ${BOOZER.name} up` }));

    await waitFor(() => expect(putOrder).toHaveBeenCalledTimes(1));
    expect(putOrder.mock.calls[0][0]).toEqual([
      BOOZER.espn_player_id,
      WEMBY.espn_player_id,
      GIANNIS.espn_player_id,
      PAUL.espn_player_id,
    ]);
    // The lens rides along, so the board that comes back is still read against it.
    expect(putOrder.mock.calls[0][1]).toBe("dynasty");
  });

  it("sends the whole order even when the move is made at the top of a deep board", async () => {
    const user = userEvent.setup();
    const deep = deepMasterBoard(400);
    board.mockResolvedValue(deep);
    // The default mock rebuilds its answer from the four named fixtures; this board is 400
    // generated players, so it answers with itself.
    putOrder.mockResolvedValue(deep);
    render(<MasterBoardPage />);
    await screen.findByText("Player 1");

    await user.click(screen.getByRole("button", { name: "Move Player 2 up" }));

    await waitFor(() => expect(putOrder).toHaveBeenCalledTimes(1));
    // 400 sent, 175 on screen: the window is a rendering decision and never a saving one.
    expect(putOrder.mock.calls[0][0]).toHaveLength(400);
    expect(onScreen()).toHaveLength(PAGE);
  });

  it("moves a player across the board by rank, not by dragging him there", async () => {
    const user = userEvent.setup();
    await openBoard();

    const move = within(rowFor(WEMBY.espn_player_id)).getByLabelText(
      `Move ${WEMBY.name} to rank`,
    );
    await user.type(move, "4");
    await user.click(within(rowFor(WEMBY.espn_player_id)).getByRole("button", { name: "Go" }));

    await waitFor(() => expect(putOrder).toHaveBeenCalledTimes(1));
    expect(putOrder.mock.calls[0][0]).toEqual([
      BOOZER.espn_player_id,
      GIANNIS.espn_player_id,
      PAUL.espn_player_id,
      WEMBY.espn_player_id,
    ]);
  });

  it("drops a dragged player onto the row he was released over", async () => {
    await openBoard();

    fireEvent.dragStart(screen.getByRole("button", { name: `Drag ${PAUL.name}` }));
    fireEvent.dragOver(rowFor(WEMBY.espn_player_id));
    fireEvent.drop(rowFor(WEMBY.espn_player_id));

    await waitFor(() => expect(putOrder).toHaveBeenCalledTimes(1));
    expect(putOrder.mock.calls[0][0]).toEqual([
      PAUL.espn_player_id,
      WEMBY.espn_player_id,
      BOOZER.espn_player_id,
      GIANNIS.espn_player_id,
    ]);
  });

  it("renders the board the save answered with, not the one it guessed", async () => {
    const user = userEvent.setup();
    // The server reflowed differently from the optimistic guess — someone else moved a player,
    // or a reconcile ran. Its answer is the board, and the guess is thrown away.
    putOrder.mockResolvedValue(
      masterBoard(
        {},
        {
          order: [
            PAUL.espn_player_id,
            WEMBY.espn_player_id,
            BOOZER.espn_player_id,
            GIANNIS.espn_player_id,
          ],
        },
      ),
    );
    await openBoard();

    await user.click(screen.getByRole("button", { name: `Move ${BOOZER.name} up` }));

    await waitFor(() =>
      expect(onScreen()).toEqual([
        PAUL.espn_player_id,
        WEMBY.espn_player_id,
        BOOZER.espn_player_id,
        GIANNIS.espn_player_id,
      ]),
    );
    expect(rowFor(PAUL.espn_player_id).getAttribute("data-rank")).toBe("1");
  });

  it("throws the guess away and re-reads when the order is refused", async () => {
    const user = userEvent.setup();
    putOrder.mockRejectedValue(
      new ApiError("/master/order responded 422", 422, "missing [5239012]"),
    );
    await openBoard();
    expect(board).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: `Move ${BOOZER.name} up` }));

    expect((await screen.findByRole("alert")).textContent).toContain("That order is out of date");
    // Nothing was written, so what is on screen has to come from a fresh read.
    await waitFor(() => expect(board).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onScreen()[0]).toBe(WEMBY.espn_player_id));
  });

  it("reports the move it just saved", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(screen.getByRole("button", { name: `Move ${BOOZER.name} up` }));

    await waitFor(() =>
      expect(
        screen.getAllByRole("status").some((node) => node.textContent?.includes("Saved")),
      ).toBe(true),
    );
  });
});

describe("tags and notes", () => {
  it("cycles a tag through none, target, fade and back", async () => {
    const user = userEvent.setup();
    await openBoard();

    // Untagged -> the first tag.
    await user.click(screen.getByRole("button", { name: `Tag ${WEMBY.name}` }));
    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(1));
    expect(putEntry.mock.calls[0].slice(0, 2)).toEqual([
      WEMBY.espn_player_id,
      { tag: "target" },
    ]);

    // target -> fade.
    await user.click(screen.getByRole("button", { name: `Tag ${BOOZER.name}` }));
    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(2));
    expect(putEntry.mock.calls[1][1]).toEqual({ tag: "fade" });

    // fade -> none, spelled as an explicit null so the backend clears it.
    await user.click(screen.getByRole("button", { name: `Tag ${GIANNIS.name}` }));
    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(3));
    expect(putEntry.mock.calls[2][1]).toEqual({ tag: null });
  });

  it("saves a note when the box loses focus, and only what changed", async () => {
    const user = userEvent.setup();
    await openBoard();

    const note = within(rowFor(WEMBY.espn_player_id)).getByLabelText(`Note on ${WEMBY.name}`);
    await user.click(note);
    await user.keyboard("Untouchable");
    await user.tab();

    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(1));
    // Only the note: a note edit must not be able to clear a tag.
    expect(putEntry.mock.calls[0].slice(0, 2)).toEqual([
      WEMBY.espn_player_id,
      { note: "Untouchable" },
    ]);
  });

  it("writes nothing when a note is focused and left alone", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(within(rowFor(GIANNIS.espn_player_id)).getByLabelText(`Note on ${GIANNIS.name}`));
    await user.tab();

    expect(putEntry).not.toHaveBeenCalled();
  });

  it("clears a note with an explicit null rather than an empty string", async () => {
    const user = userEvent.setup();
    await openBoard();

    const note = within(rowFor(GIANNIS.espn_player_id)).getByLabelText(
      `Note on ${GIANNIS.name}`,
    );
    await user.clear(note);
    await user.tab();

    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(1));
    expect(putEntry.mock.calls[0][1]).toEqual({ note: null });
  });
});

describe("setting a player aside", () => {
  /** The board as it comes back once a player is out of the order. */
  function withoutGiannis() {
    return masterBoard(
      {},
      {
        order: [WEMBY.espn_player_id, BOOZER.espn_player_id, PAUL.espn_player_id],
        aside: [AYTON, GIANNIS],
      },
    );
  }

  it("takes him out of the order and puts him in the tray", async () => {
    const user = userEvent.setup();
    putEntry.mockResolvedValue(withoutGiannis());
    await openBoard();

    await user.click(screen.getByRole("button", { name: `Set ${GIANNIS.name} aside` }));

    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(1));
    expect(putEntry.mock.calls[0].slice(0, 2)).toEqual([
      GIANNIS.espn_player_id,
      { excluded: true },
    ]);

    await waitFor(() => expect(onScreen()).not.toContain(GIANNIS.espn_player_id));
    const tray = screen.getByRole("region", { name: "Set aside" });
    expect(within(tray).getByText(GIANNIS.name)).toBeTruthy();
    // And everyone below him moved up, which is the server's reflow rather than ours.
    expect(rowFor(PAUL.espn_player_id).getAttribute("data-rank")).toBe("3");
  });

  it("brings him back out of the tray, at the slot the field implies", async () => {
    const user = userEvent.setup();
    board.mockResolvedValue(withoutGiannis());
    putEntry.mockResolvedValue(masterBoard());
    render(<MasterBoardPage />);
    await screen.findByText(WEMBY.name);

    const tray = screen.getByRole("region", { name: "Set aside" });
    await user.click(within(tray).getByRole("button", { name: `Restore ${GIANNIS.name}` }));

    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(1));
    expect(putEntry.mock.calls[0].slice(0, 2)).toEqual([
      GIANNIS.espn_player_id,
      { excluded: false },
    ]);

    await waitFor(() => expect(onScreen()).toContain(GIANNIS.espn_player_id));
    expect(
      within(screen.getByRole("region", { name: "Set aside" })).queryByText(GIANNIS.name),
    ).toBeNull();
  });
});

describe("the horizon", () => {
  it("changes the reference column and leaves the order exactly where it was", async () => {
    const user = userEvent.setup();
    await openBoard();

    const before = onScreen();
    // Dynasty: we have Boozer ten spots above the field.
    expect(
      rowFor(BOOZER.espn_player_id).querySelector("[data-edge]")?.textContent,
    ).toContain("+10");

    board.mockResolvedValue(masterBoard({}, { horizon: "current_year" }));
    await user.click(screen.getByRole("button", { name: "Win now" }));

    // The position rides along on every read now, and "All" is an explicit null.
    await waitFor(() => expect(board).toHaveBeenLastCalledWith("current_year", null));
    await waitFor(() =>
      expect(
        rowFor(BOOZER.espn_player_id).querySelector("[data-edge]")?.textContent,
      ).toContain("+38"),
    );

    // THE assertion: the lens moved, the board did not.
    expect(onScreen()).toEqual(before);
    expect(rowFor(BOOZER.espn_player_id).getAttribute("data-rank")).toBe("2");
    // And the field's own column moved with it: 12 under dynasty, 40 under win-now.
    expect(within(rowFor(BOOZER.espn_player_id)).getByText("40")).toBeTruthy();
  });
});

describe("depth", () => {
  it("draws a window rather than the whole board, and extends it on request", async () => {
    const user = userEvent.setup();
    board.mockResolvedValue(deepMasterBoard(400));
    render(<MasterBoardPage />);
    await screen.findByText("Player 1");

    expect(onScreen()).toHaveLength(PAGE);
    expect(screen.queryByText(`Player ${PAGE + 1}`)).toBeNull();

    await user.click(screen.getByRole("button", { name: /Show \d+ more/ }));

    expect(onScreen()).toHaveLength(PAGE * 2);
    expect(screen.getByText(`Player ${PAGE + 1}`)).toBeTruthy();
  });

  it("finds a player far below the window without drawing everyone above him", async () => {
    const user = userEvent.setup();
    board.mockResolvedValue(deepMasterBoard(400));
    render(<MasterBoardPage />);
    await screen.findByText("Player 1");

    await user.type(screen.getByLabelText("Find a player"), "Player 390");

    expect(screen.getByText("Player 390")).toBeTruthy();
    expect(onScreen()).toEqual([900389]);
    // He is still the 390th man, and a move from here would say so.
    expect(rowFor(900389).getAttribute("data-rank")).toBe("390");
  });

  it("searches by team as well as by name", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.type(screen.getByLabelText("Find a player"), "mil");

    expect(onScreen()).toEqual([GIANNIS.espn_player_id]);
  });

  it("says so when nobody matches, without pretending the board is empty", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.type(screen.getByLabelText("Find a player"), "Nikola Topic");

    expect(screen.getByText(/Nobody on your board matches/)).toBeTruthy();
    expect(screen.queryByText("Nothing to rank yet")).toBeNull();
  });
});

describe("resetting", () => {
  it("asks first, because it throws away every rank, tag and note", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await openBoard();

    await user.click(screen.getByRole("button", { name: "Reset to consensus" }));

    expect(confirm).toHaveBeenCalled();
    expect(confirm.mock.calls[0][0]).toContain("cannot be undone");
    await waitFor(() => expect(resetBoard).toHaveBeenCalledWith("dynasty"));
  });

  it("does nothing at all when the confirm is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await openBoard();

    await user.click(screen.getByRole("button", { name: "Reset to consensus" }));

    expect(resetBoard).not.toHaveBeenCalled();
  });
});

/**
 * Tiers, and the position filter that chooses which set of them you are looking at.
 *
 * The fixture board is cut into two bands (Wembanyama and Boozer, then Giannis and Paul) and
 * the power forwards — Boozer then Giannis — into two of their own, so every assertion below
 * can tell "the board's tiers" apart from "this position's tiers" rather than agreeing with
 * both by accident.
 *
 * The claim the whole feature rests on, and the reason several of these exist: a tier is a
 * BAND OVER THE RANKS, not a property of a player. Moving a player across a line re-tiers him
 * and writes NOTHING to the tier endpoint; moving a line writes cut ranks and cannot move a
 * player. Neither edit can reach the other's endpoint.
 */
describe("tier dividers", () => {
  it("draws one at each cut rank, labelled with the band it opens", async () => {
    await openBoard();

    // The board's stored cuts are [1, 3]: tier 1 is ranks 1-2, tier 2 is 3-4.
    expect(dividersOnScreen()).toEqual([1, 3]);
    const second = document.querySelector('tbody tr[data-tier-start="3"]');
    expect(second?.textContent).toContain("Tier 2");
    expect(second?.textContent).toContain("3–4 on the board");
    expect(second?.textContent).toContain("2 players");
  });

  it("gives every row the tier its rank falls in", async () => {
    await openBoard();

    expect(tierOf(WEMBY.espn_player_id)).toBe("T1");
    expect(tierOf(BOOZER.espn_player_id)).toBe("T1");
    // Giannis is the first man below the line, so he opens tier 2.
    expect(tierOf(GIANNIS.espn_player_id)).toBe("T2");
    expect(tierOf(PAUL.espn_player_id)).toBe("T2");
  });

  it("also prints his tier among his own position, which is the one a roster slot is filled from", async () => {
    await openBoard();

    // Boozer and Giannis are the two power forwards on the board, in that order, and the PF
    // scope is cut between them: he is a tier-1 PF and Giannis is a tier-2 PF, even though
    // both of them are in the same overall tier... which is exactly what the pill is for.
    expect(
      rowFor(BOOZER.espn_player_id).querySelector("[data-position-tier]")?.textContent,
    ).toBe("PF1");
    expect(
      rowFor(GIANNIS.espn_player_id).querySelector("[data-position-tier]")?.textContent,
    ).toBe("PF2");
  });

  it("nudges a divider up a rank and saves the whole corrected list", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(
      screen.getByRole("button", { name: "Move the start of tier 2 up one" }),
    );

    await waitFor(() => expect(putTiers).toHaveBeenCalledTimes(1));
    // [1, 3] with the second cut moved one rank up. Sorted, unique, still led by 1 — which
    // is the only list `PUT /master/tiers` accepts.
    expect(putTiers.mock.calls[0]).toEqual(["overall", [1, 2], "dynasty"]);
    await waitFor(() => expect(dividersOnScreen()).toEqual([1, 2]));
    // And the band the rows are in moved with the line, with nothing written about a player.
    expect(tierOf(BOOZER.espn_player_id)).toBe("T2");
    expect(putOrder).not.toHaveBeenCalled();
  });

  it("nudges one down a rank", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(
      screen.getByRole("button", { name: "Move the start of tier 2 down one" }),
    );

    await waitFor(() => expect(putTiers).toHaveBeenCalledTimes(1));
    expect(putTiers.mock.calls[0][1]).toEqual([1, 4]);
  });

  it("refuses to nudge the top of the board, which is where tier 1 starts by definition", async () => {
    await openBoard();

    const top = screen.getByRole("button", { name: "Move the start of tier 1 up one" });
    expect(top).toHaveProperty("disabled", true);
    // And there is nothing to merge tier 1 into.
    expect(
      screen.queryByRole("button", { name: "Remove the break before tier 1" }),
    ).toBeNull();
  });

  it("drags a divider onto a row and starts the band there — moving no player", async () => {
    await openBoard();

    fireEvent.dragStart(
      screen.getByRole("button", { name: "Drag the start of tier 2" }),
    );
    fireEvent.dragOver(rowFor(PAUL.espn_player_id));
    fireEvent.drop(rowFor(PAUL.espn_player_id));

    await waitFor(() => expect(putTiers).toHaveBeenCalledTimes(1));
    // Paul is the 4th man, so tier 2 now starts at 4.
    expect(putTiers.mock.calls[0][1]).toEqual([1, 4]);
    // THE assertion about the two drag types: a divider released over a player row is a
    // divider move. The order endpoint is never touched.
    expect(putOrder).not.toHaveBeenCalled();
  });

  it("adds a break between two rows", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(
      screen.getByRole("button", { name: "Start a new tier at 2 on the board" }),
    );

    await waitFor(() => expect(putTiers).toHaveBeenCalledTimes(1));
    expect(putTiers.mock.calls[0][1]).toEqual([1, 2, 3]);
    await waitFor(() => expect(dividersOnScreen()).toEqual([1, 2, 3]));
  });

  it("offers no break above the first row, where a tier already starts", async () => {
    await openBoard();

    expect(
      screen.queryByRole("button", { name: "Start a new tier at 1 on the board" }),
    ).toBeNull();
  });

  it("removes a break, merging its tier into the one above", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(
      screen.getByRole("button", { name: "Remove the break before tier 2" }),
    );

    await waitFor(() => expect(putTiers).toHaveBeenCalledTimes(1));
    expect(putTiers.mock.calls[0][1]).toEqual([1]);
    await waitFor(() => expect(dividersOnScreen()).toEqual([1]));
    // One band now, and everybody is in it.
    expect(tierOf(PAUL.espn_player_id)).toBe("T1");
  });

  it("re-tiers a player moved across a line without writing a tier", async () => {
    const user = userEvent.setup();
    await openBoard();
    expect(tierOf(GIANNIS.espn_player_id)).toBe("T2");

    // Up one: he lands at rank 2, which is inside the tier-1 band.
    await user.click(screen.getByRole("button", { name: `Move ${GIANNIS.name} up` }));

    await waitFor(() => expect(putOrder).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(tierOf(GIANNIS.espn_player_id)).toBe("T1"));
    // The bands didn't move. He did.
    expect(putTiers).not.toHaveBeenCalled();
    expect(dividersOnScreen()).toEqual([1, 3]);
  });

  it("resets a scope to the automatic cut, behind a confirm", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await openBoard();

    await user.click(screen.getByRole("button", { name: "Reset tiers to auto" }));

    expect(confirm.mock.calls[0][0]).toContain("discarded");
    await waitFor(() => expect(reseedTiers).toHaveBeenCalledWith("overall", "dynasty"));
    // Scoped: it throws away the dividers, not the board.
    expect(resetBoard).not.toHaveBeenCalled();
    expect(putOrder).not.toHaveBeenCalled();
  });

  it("does nothing when that confirm is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await openBoard();

    await user.click(screen.getByRole("button", { name: "Reset tiers to auto" }));

    expect(reseedTiers).not.toHaveBeenCalled();
  });

  it("says a tier write is saving, and what it saved", async () => {
    const user = userEvent.setup();
    await openBoard();

    await user.click(
      screen.getByRole("button", { name: "Move the start of tier 2 down one" }),
    );

    await waitFor(() =>
      expect(
        screen
          .getAllByRole("status")
          .some((node) => node.textContent?.includes("Tier break moved to 4")),
      ).toBe(true),
    );
  });

  it("keeps drawing the dividers inside the window and the search results", async () => {
    const user = userEvent.setup();
    board.mockResolvedValue(deepMasterBoard(400));
    render(<MasterBoardPage />);
    await screen.findByText("Player 1");

    // Cuts at 1, 13, 60 and 200; only the first three are inside the 175-row window.
    expect(dividersOnScreen()).toEqual([1, 13, 60]);
    expect(tierOf(900012)).toBe("T2");

    await user.click(screen.getByRole("button", { name: /Show \d+ more/ }));
    expect(dividersOnScreen()).toEqual([1, 13, 60, 200]);

    // Under a search, a divider is drawn only where the row it belongs to is: the gaps
    // between two search hits are not gaps on the board.
    await user.type(screen.getByLabelText("Find a player"), "Player 13");
    expect(dividersOnScreen()).toEqual([13]);
  });
});

describe("the position filter", () => {
  /** The PF view: Boozer and Giannis, at their board ranks, with the PF scope's tiers. */
  function powerForwards() {
    return masterBoard({}, { position: "PF" });
  }

  it("refetches narrowed to that position and keeps the board ranks", async () => {
    const user = userEvent.setup();
    await openBoard();
    board.mockResolvedValue(powerForwards());

    await user.click(screen.getByRole("button", { name: "PF" }));

    await waitFor(() => expect(board).toHaveBeenLastCalledWith("dynasty", "PF"));
    await waitFor(() =>
      expect(onScreen()).toEqual([BOOZER.espn_player_id, GIANNIS.espn_player_id]),
    );
    // His place on our board does not change because we are looking at the forwards.
    expect(rowFor(BOOZER.espn_player_id).getAttribute("data-rank")).toBe("2");
    expect(rowFor(GIANNIS.espn_player_id).getAttribute("data-rank")).toBe("3");
  });

  it("switches the dividers to that position's own scope", async () => {
    const user = userEvent.setup();
    await openBoard();
    expect(dividersOnScreen()).toEqual([1, 3]);
    board.mockResolvedValue(powerForwards());

    await user.click(screen.getByRole("button", { name: "PF" }));

    // The PF cuts are [1, 2] and they count POWER FORWARDS: the second one falls between
    // Boozer and Giannis, who are ranks 2 and 3 on the board.
    await waitFor(() => expect(dividersOnScreen()).toEqual([1, 2]));
    expect(document.querySelector('tbody tr[data-tier-start="2"]')?.textContent).toContain(
      "among power forwards",
    );
    expect(tierOf(BOOZER.espn_player_id)).toBe("T1");
    expect(tierOf(GIANNIS.espn_player_id)).toBe("T2");
  });

  it("writes a divider edit against the position's scope, not the board's", async () => {
    const user = userEvent.setup();
    await openBoard();
    board.mockResolvedValue(powerForwards());
    await user.click(screen.getByRole("button", { name: "PF" }));
    await waitFor(() => expect(dividersOnScreen()).toEqual([1, 2]));

    await user.click(
      screen.getByRole("button", { name: "Remove the break before tier 2" }),
    );

    await waitFor(() => expect(putTiers).toHaveBeenCalledTimes(1));
    expect(putTiers.mock.calls[0]).toEqual(["PF", [1], "dynasty"]);
  });

  it("reseeds that position's tiers and no other scope", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await openBoard();
    board.mockResolvedValue(powerForwards());
    await user.click(screen.getByRole("button", { name: "PF" }));
    await waitFor(() => expect(dividersOnScreen()).toEqual([1, 2]));

    await user.click(screen.getByRole("button", { name: "Reset tiers to auto" }));

    await waitFor(() => expect(reseedTiers).toHaveBeenCalledWith("PF", "dynasty"));
  });

  it("turns reordering off, and says why", async () => {
    const user = userEvent.setup();
    await openBoard();
    board.mockResolvedValue(powerForwards());
    await user.click(screen.getByRole("button", { name: "PF" }));
    await waitFor(() => expect(onScreen()).toHaveLength(2));

    expect(screen.getByText(/Reordering is off here/)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: `Move ${GIANNIS.name} up` }),
    ).toHaveProperty("disabled", true);
    expect(
      screen.getByRole("button", { name: `Move ${BOOZER.name} down` }),
    ).toHaveProperty("disabled", true);
    expect(
      within(rowFor(BOOZER.espn_player_id)).getByLabelText(`Move ${BOOZER.name} to rank`),
    ).toHaveProperty("disabled", true);
    expect(putOrder).not.toHaveBeenCalled();
  });

  it("leaves the tag, the note and the exclude working under a filter", async () => {
    const user = userEvent.setup();
    await openBoard();
    board.mockResolvedValue(powerForwards());
    await user.click(screen.getByRole("button", { name: "PF" }));
    await waitFor(() => expect(onScreen()).toHaveLength(2));
    putEntry.mockResolvedValue(powerForwards());

    await user.click(screen.getByRole("button", { name: `Tag ${BOOZER.name}` }));
    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(1));
    expect(putEntry.mock.calls[0][1]).toEqual({ tag: "fade" });

    await user.click(screen.getByRole("button", { name: `Set ${GIANNIS.name} aside` }));
    await waitFor(() => expect(putEntry).toHaveBeenCalledTimes(2));
    expect(putEntry.mock.calls[1][1]).toEqual({ excluded: true });
  });

  it("restores the whole board and the overall tiers on All", async () => {
    const user = userEvent.setup();
    await openBoard();
    board.mockResolvedValue(powerForwards());
    await user.click(screen.getByRole("button", { name: "PF" }));
    await waitFor(() => expect(onScreen()).toHaveLength(2));

    board.mockResolvedValue(masterBoard());
    await user.click(screen.getByRole("button", { name: "All" }));

    await waitFor(() => expect(board).toHaveBeenLastCalledWith("dynasty", null));
    await waitFor(() => expect(onScreen()).toHaveLength(4));
    expect(dividersOnScreen()).toEqual([1, 3]);
    expect(screen.queryByText(/Reordering is off here/)).toBeNull();
    expect(
      screen.getByRole("button", { name: `Move ${GIANNIS.name} up` }),
    ).toHaveProperty("disabled", false);
  });

  it("says a position has nobody on it without pretending the board is empty", async () => {
    const user = userEvent.setup();
    await openBoard();
    // Nobody on the fixture board is a shooting guard.
    board.mockResolvedValue(masterBoard({}, { position: "SG" }));

    await user.click(screen.getByRole("button", { name: "SG" }));

    expect(
      await screen.findByText(/Nobody on your board is listed at shooting guards/),
    ).toBeTruthy();
    // The board is fine — this is a view of it, not a board with nobody on it.
    expect(screen.queryByText("Nothing to rank yet")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the horizon when the position changes", async () => {
    const user = userEvent.setup();
    await openBoard();

    board.mockResolvedValue(masterBoard({}, { horizon: "current_year" }));
    await user.click(screen.getByRole("button", { name: "Win now" }));
    await waitFor(() => expect(board).toHaveBeenLastCalledWith("current_year", null));

    board.mockResolvedValue(masterBoard({}, { horizon: "current_year", position: "PF" }));
    await user.click(screen.getByRole("button", { name: "PF" }));

    await waitFor(() => expect(board).toHaveBeenLastCalledWith("current_year", "PF"));
  });
});

/**
 * The cut-rank arithmetic itself, under the page.
 *
 * `PUT /master/tiers` refuses a list that is unsorted, duplicated, out of range or missing
 * its leading 1, and the page's defence against that 422 is not a check before the send — it
 * is that every edit is a function that cannot produce one. These are that claim, stated
 * once, where it can be tested without a DOM.
 */
describe("cut ranks", () => {
  /** The four rules `validate_cuts` in app/ranking/tiers.py enforces, as one predicate. */
  function isSendable(cuts: number[], size: number): boolean {
    if (size <= 0) return cuts.length === 0;
    return (
      cuts[0] === 1 &&
      cuts.every((rank, index) => index === 0 || rank > cuts[index - 1]) &&
      cuts.every((rank) => rank >= 1 && rank <= size)
    );
  }

  it("sorts, de-duplicates and restores the leading 1 whatever it is handed", () => {
    expect(normalizeCuts([12, 4, 4, 1], 50)).toEqual([1, 4, 12]);
    // The leading 1 is not optional: a list without it describes a board whose first tier is
    // tier 2, which is not a board.
    expect(normalizeCuts([4, 12], 50)).toEqual([1, 4, 12]);
    // A divider past the end of the board bands nobody.
    expect(normalizeCuts([1, 4, 900], 50)).toEqual([1, 4]);
    // And a scope with nobody in it has no bands at all, rather than one empty one.
    expect(normalizeCuts([1, 4], 0)).toEqual([]);
  });

  it("stays sendable through every edit the page can make", () => {
    const size = 40;
    let cuts = normalizeCuts([1, 8, 20], size);

    for (const edit of [
      () => addCut(cuts, 14, size),
      () => addCut(cuts, 1, size), // already a cut: a no-op, not a duplicate
      () => removeCut(cuts, 8, size),
      () => removeCut(cuts, 1, size), // refused: tier 1 starts at the top
      () => moveCut(cuts, 20, 21, size),
      () => moveCut(cuts, 21, 1, size), // clamped: rank 1 is not a gap
      () => moveCut(cuts, 14, 999, size), // clamped to the last rank
      () => addCut(cuts, -3, size),
    ]) {
      cuts = edit();
      expect(isSendable(cuts, size)).toBe(true);
    }
  });

  it("refuses to drop a divider on top of another one", () => {
    // [1, 8, 20] with 20 dragged onto 8 would de-duplicate to [1, 8] — silently deleting a
    // tier the drag never meant to remove. It collapses to a no-op instead.
    expect(moveCut([1, 8, 20], 20, 8, 40)).toEqual([1, 8, 20]);
    expect(nudgedCut([1, 8, 9], 9, 40, -1)).toBeNull();
    // And neither nudge leaves the board.
    expect(nudgedCut([1, 8], 8, 8, 1)).toBeNull();
    expect(nudgedCut([1, 2], 2, 40, -1)).toBeNull();
    expect(nudgedCut([1, 8], 8, 40, 1)).toBe(9);
  });

  it("describes the bands the dividers cut, to the end of the scope", () => {
    expect(tierBands([1, 4, 12], 20)).toEqual([
      { tier: 1, start: 1, end: 3 },
      { tier: 2, start: 4, end: 11 },
      { tier: 3, start: 12, end: 20 },
    ]);
    // Every rank is in a band, so the man below the last divider is in the bottom tier
    // rather than untiered.
    expect(shownTier([1, 4, 12], 20)).toBe(3);
    expect(shownTier([1, 4, 12], 3)).toBe(1);
    expect(shownTier([], 3)).toBeNull();
  });
});
