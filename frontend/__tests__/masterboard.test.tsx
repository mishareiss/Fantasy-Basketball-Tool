import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { MasterBoardPage } from "@/components/masterboard/MasterBoardPage";
import { PAGE } from "@/lib/masterboard";
import { MASTER_ASIDE, MASTER_SEEDS, deepMasterBoard, masterBoard } from "./fixtures";

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
      resetMasterBoard: vi.fn(),
    },
  };
});

const board = vi.mocked(api.masterBoard);
const putOrder = vi.mocked(api.putMasterOrder);
const putEntry = vi.mocked(api.putMasterEntry);
const resetBoard = vi.mocked(api.resetMasterBoard);

const [WEMBY, BOOZER, GIANNIS, PAUL] = MASTER_SEEDS;
const AYTON = MASTER_ASIDE;

/** The order actually on screen, read off the rows rather than off the ranks they print. */
function onScreen(): number[] {
  return Array.from(document.querySelectorAll("tbody tr")).map((row) =>
    Number(row.getAttribute("data-player")),
  );
}

function rowFor(playerId: number): HTMLElement {
  const row = document.querySelector(`tbody tr[data-player="${playerId}"]`);
  if (!row) throw new Error(`no row for player ${playerId}`);
  return row as HTMLElement;
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

    await waitFor(() => expect(board).toHaveBeenLastCalledWith("current_year"));
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
