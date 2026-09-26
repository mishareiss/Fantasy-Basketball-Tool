import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type DraftStateResponse } from "@/lib/api";
import { DraftRoomPage } from "@/components/draft/DraftRoomPage";
import {
  candidates,
  cellOf,
  cellPickNumber,
  draftedIds,
  picksByNumber,
  pickNumbersFor,
  roundOf,
} from "@/lib/draft";
import {
  DRAFT_MY_SLOT,
  DRAFT_ROUNDS,
  DRAFT_TEAMS,
  MASTER_SEEDS,
  draftAdvance,
  draftPlan,
  draftState,
  masterBoard,
  snakeOrder,
} from "./fixtures";

/**
 * The draft room, and the snake underneath it.
 *
 * Two kinds of test here. The first half is `lib/draft` on its own — the cell-to-pick
 * mapping, checked against a hand-written 4x3 snake and against the pick numbers the
 * backend's own test asserts for slot 2 of 10. It gets its own section because a grid whose
 * squares are off by a column still renders as a working board: nothing else in the suite
 * would notice.
 *
 * The second half is the page, with the api mocked wholesale (`ApiError` kept real, so the
 * 404 that means "no draft yet" is told apart from a failure by its status). Its claims:
 *
 * * `GET /draft` answering 404 is the SETUP FORM, not an error panel;
 * * the log lands in the right squares of the grid, even-numbered rounds included;
 * * one search box enters every pick — the snake only lets the seat on the clock pick, so
 *   this is how an opponent's pick gets typed in as well as mine;
 * * every control replaces the whole state from what the server answered, which is the one
 *   discipline this page has;
 * * the mode toggle is UI only: it hides the sim controls and writes nothing.
 *
 * The fixture is 4 teams x 3 rounds on purpose — see `fixtures.ts` and the CI note there.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getDraft: vi.fn(),
      createDraft: vi.fn(),
      resetDraft: vi.fn(),
      applyPick: vi.fn(),
      editPick: vi.fn(),
      simulate: vi.fn(),
      undoPick: vi.fn(),
      draftPlan: vi.fn(),
      masterBoard: vi.fn(),
    },
  };
});

const getDraft = vi.mocked(api.getDraft);
const createDraft = vi.mocked(api.createDraft);
const resetDraft = vi.mocked(api.resetDraft);
const applyPick = vi.mocked(api.applyPick);
const editPick = vi.mocked(api.editPick);
const simulate = vi.mocked(api.simulate);
const undoPick = vi.mocked(api.undoPick);
const plan = vi.mocked(api.draftPlan);
const board = vi.mocked(api.masterBoard);

const [WEMBY, BOOZER, GIANNIS, PAUL] = MASTER_SEEDS;

/** What a cell shows, by pick number: the player id in it, or null for an empty square. */
function cellPlayer(pickNumber: number): number | null {
  const cell = document.querySelector(`[data-cell="${pickNumber}"]`);
  if (!cell) throw new Error(`no cell for pick ${pickNumber}`);
  const player = cell.getAttribute("data-player");
  return player === null ? null : Number(player);
}

async function openRoom() {
  render(<DraftRoomPage />);
  await screen.findByRole("grid");
}

/**
 * Open the room on one draft, with the plan derived FROM that draft.
 *
 * The two reads have to describe the same room or the panels are about a draft that isn't
 * on screen — `draftPlan` takes the state for exactly that reason.
 */
async function openRoomOn(
  state: DraftStateResponse,
  options: Parameters<typeof draftPlan>[0] = {},
) {
  getDraft.mockResolvedValue(state);
  plan.mockResolvedValue(draftPlan({ state, ...options }));
  render(<DraftRoomPage />);
  await screen.findByRole("grid");
  await waitFor(() => expect(plan).toHaveBeenCalled());
}

/** One plan panel, by the pick number it plans for. */
function panelFor(pickNumber: number): HTMLElement {
  const panel = document.querySelector(`[data-plan-pick="${pickNumber}"]`);
  if (!panel) throw new Error(`no plan panel for pick ${pickNumber}`);
  return panel as HTMLElement;
}

/** Every pick number the plan has a panel for, in the order they are drawn. */
function panelsOnScreen(): number[] {
  return Array.from(document.querySelectorAll("[data-plan-pick]")).map((panel) =>
    Number(panel.getAttribute("data-plan-pick")),
  );
}

beforeEach(() => {
  getDraft.mockResolvedValue(draftState());
  board.mockResolvedValue(masterBoard());
  createDraft.mockResolvedValue(draftState());
  resetDraft.mockResolvedValue(draftState());
  applyPick.mockResolvedValue(draftState());
  editPick.mockResolvedValue(draftState());
  undoPick.mockResolvedValue(draftState());
  simulate.mockResolvedValue(draftAdvance(draftState(), []));
  plan.mockResolvedValue(draftPlan());
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

/* --- the snake ------------------------------------------------------------------------ */

describe("lib/draft — the snake", () => {
  it("maps the 4x3 grid to the snake you can count on your fingers", () => {
    // Round 1 runs left to right, round 2 right to left, round 3 left to right again.
    const grid = [
      [1, 2, 3, 4],
      [8, 7, 6, 5],
      [9, 10, 11, 12],
    ];
    for (const [index, row] of grid.entries()) {
      const round = index + 1;
      for (const [seat, expected] of row.entries()) {
        expect(cellPickNumber(round, seat + 1, 4)).toBe(expected);
      }
    }
  });

  it("agrees with a snake built by concatenating the rounds", () => {
    const order = snakeOrder(DRAFT_TEAMS, DRAFT_ROUNDS);
    for (const [index, slot] of order.entries()) {
      const number = index + 1;
      expect(cellOf(number, DRAFT_TEAMS)).toEqual({
        round: Math.floor(index / DRAFT_TEAMS) + 1,
        teamSlot: slot,
      });
      expect(cellPickNumber(cellOf(number, DRAFT_TEAMS).round, slot, DRAFT_TEAMS)).toBe(number);
    }
  });

  it("round-trips every cell of a real 10x20 board", () => {
    for (let round = 1; round <= 20; round += 1) {
      for (let seat = 1; seat <= 10; seat += 1) {
        const number = cellPickNumber(round, seat, 10);
        expect(cellOf(number, 10)).toEqual({ round, teamSlot: seat });
        expect(roundOf(number, 10)).toBe(round);
      }
    }
    // Every pick number is used exactly once, which is the thing a wrong formula breaks.
    const all = Array.from({ length: 20 }, (_, r) =>
      Array.from({ length: 10 }, (_, s) => cellPickNumber(r + 1, s + 1, 10)),
    ).flat();
    expect(new Set(all).size).toBe(200);
  });

  it("gives my column the pick numbers the backend gives my seat", () => {
    expect(pickNumbersFor(DRAFT_MY_SLOT, DRAFT_TEAMS, DRAFT_ROUNDS)).toEqual([2, 7, 10]);
    // The same alternating 17-then-3 wait backend/tests/test_api_draft.py asserts.
    expect(pickNumbersFor(2, 10, 20).slice(0, 5)).toEqual([2, 19, 22, 39, 42]);
    expect(pickNumbersFor(1, 10, 20).slice(0, 4)).toEqual([1, 20, 21, 40]);
  });

  it("throws on a cell outside the grid rather than quietly returning one inside it", () => {
    expect(() => cellPickNumber(1, 5, 4)).toThrow("outside 1..4");
    expect(() => cellPickNumber(0, 1, 4)).toThrow(">= 1");
    expect(() => cellOf(0, 4)).toThrow(">= 1");
  });
});

describe("lib/draft — who the search offers", () => {
  const catalog = masterBoard().players;

  it("offers nobody until something is typed", () => {
    expect(candidates(catalog, new Set(), "")).toEqual([]);
    expect(candidates(catalog, new Set(), "   ")).toEqual([]);
  });

  it("finds a player accent-insensitively and drops whoever is drafted", () => {
    const found = candidates(catalog, new Set(), "antetokounmpo");
    expect(found.map((row) => row.espn_player_id)).toEqual([GIANNIS.espn_player_id]);
    expect(
      candidates(catalog, new Set([GIANNIS.espn_player_id]), "antetokounmpo"),
    ).toEqual([]);
  });

  it("stops at the limit", () => {
    expect(candidates(catalog, new Set(), "a", 2).length).toBe(2);
  });

  it("reads the drafted set and the log index off the live state", () => {
    const state = draftState({
      picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
    });
    expect(draftedIds(state)).toEqual(
      new Set([WEMBY.espn_player_id, BOOZER.espn_player_id]),
    );
    expect(picksByNumber(state.log).get(2)?.espn_player_id).toBe(BOOZER.espn_player_id);
  });
});

/* --- the page -------------------------------------------------------------------------- */

describe("the draft room", () => {
  it("shows the setup form when there is no draft, and starts one", async () => {
    getDraft.mockRejectedValue(new ApiError("/draft responded 404", 404, "There is no draft."));
    const user = userEvent.setup();
    render(<DraftRoomPage />);
    const form = await screen.findByRole("form", { name: /start a draft/i });

    await user.clear(screen.getByLabelText(/my seat/i));
    await user.type(screen.getByLabelText(/my seat/i), "3");
    await user.click(within(form).getByRole("button", { name: /start draft/i }));

    await waitFor(() => expect(createDraft).toHaveBeenCalledTimes(1));
    // No draft to replace, so no `reset=true`: that flag is the reconfigure path's.
    expect(createDraft).toHaveBeenCalledWith({ mode: "simulation", my_slot: 3 }, false);
    await screen.findByRole("grid");
  }, 15000);

  it("does not call anything else a 404 could be mistaken for", async () => {
    getDraft.mockRejectedValue(new ApiError("/draft responded 404", 404));
    render(<DraftRoomPage />);
    await screen.findByRole("form", { name: /start a draft/i });

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("grid")).toBeNull();
  });

  it("shows a failure panel for a read that failed for any other reason", async () => {
    getDraft.mockRejectedValue(new ApiError("/draft responded 400", 400, "no such horizon"));
    render(<DraftRoomPage />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("no such horizon");
    expect(screen.queryByRole("form", { name: /start a draft/i })).toBeNull();
  });

  it("puts every pick in the square the snake says owns it", async () => {
    getDraft.mockResolvedValue(
      draftState({
        picks: [
          { playerId: WEMBY.espn_player_id },
          { playerId: BOOZER.espn_player_id },
          { playerId: GIANNIS.espn_player_id, isAuto: true },
          { playerId: PAUL.espn_player_id },
          // Pick 5 is round 2 — the row that runs the other way, and the one a wrong
          // mapping puts in column 1 instead of column 4.
          { playerId: 4278067 },
        ],
      }),
    );
    await openRoom();

    expect(cellPlayer(1)).toBe(WEMBY.espn_player_id);
    expect(cellPlayer(2)).toBe(BOOZER.espn_player_id);
    expect(cellPlayer(5)).toBe(4278067);
    expect(cellPlayer(6)).toBeNull();
    expect(cellPlayer(12)).toBeNull();

    // And pick 5 really is round 2, seat 4 — read off the DOM rather than off the helper.
    const row = document.querySelector('[data-cell="5"]')?.closest('[role="row"]');
    const cells = Array.from(row?.querySelectorAll("[data-cell]") ?? []);
    expect(cells.map((cell) => cell.getAttribute("data-cell"))).toEqual(["8", "7", "6", "5"]);
  });

  it("says whose pick it is, and says YOUR PICK when it is mine", async () => {
    getDraft.mockResolvedValue(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));
    await openRoom();

    const clock = document.querySelector("[data-clock]");
    expect(clock?.getAttribute("data-clock")).toBe("mine");
    expect(clock?.textContent).toContain("YOUR PICK");
    expect(clock?.textContent).toContain("pick 2");
  });

  it("names the seat on the clock when it is somebody else's", async () => {
    await openRoom();

    const clock = document.querySelector("[data-clock]");
    expect(clock?.getAttribute("data-clock")).toBe("theirs");
    expect(clock?.textContent).toContain("Team 1 on the clock");
  });

  it("says the draft is complete once every pick is in", async () => {
    getDraft.mockResolvedValue(
      draftState({
        teamCount: 2,
        rounds: 1,
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );
    await openRoom();

    expect(document.querySelector("[data-clock]")?.getAttribute("data-clock")).toBe("complete");
    // Nothing left to enter, so the entry box is gone.
    expect(screen.queryByLabelText(/pick \d+ for team/i)).toBeNull();
  });

  it("drafts the player you pick out of the search, for whoever is on the clock", async () => {
    const after = draftState({ picks: [{ playerId: GIANNIS.espn_player_id }] });
    applyPick.mockResolvedValue(after);
    const user = userEvent.setup();
    await openRoom();

    await user.type(screen.getByLabelText(/pick 1 for team 1/i), "giannis");
    await user.click(await screen.findByText(GIANNIS.name));

    await waitFor(() => expect(applyPick).toHaveBeenCalledTimes(1));
    // No `team_slot`: the backend defaults it to the clock, and the snake has only one
    // answer. This is also how manual mode enters an OPPONENT's pick.
    expect(applyPick).toHaveBeenCalledWith({ player_id: GIANNIS.espn_player_id });
    // The board on screen is the one the server answered with, not a local patch.
    await waitFor(() => expect(cellPlayer(1)).toBe(GIANNIS.espn_player_id));
    expect(document.querySelector("[data-clock]")?.getAttribute("data-clock")).toBe("mine");
  }, 15000);

  it("never offers a player who has already been taken", async () => {
    getDraft.mockResolvedValue(draftState({ picks: [{ playerId: GIANNIS.espn_player_id }] }));
    const user = userEvent.setup();
    await openRoom();

    await user.type(screen.getByLabelText(/your pick/i), "giannis");

    expect(await screen.findByText(/nobody on your board matches/i)).toBeTruthy();
    expect(document.querySelector("[data-candidate]")).toBeNull();
  }, 15000);

  it("surfaces the backend's refusal rather than swallowing it", async () => {
    applyPick.mockRejectedValue(
      new ApiError("/draft/picks responded 422", 422, "player 2779 has already been drafted"),
    );
    const user = userEvent.setup();
    await openRoom();

    await user.type(screen.getByLabelText(/pick 1 for team 1/i), "paul");
    await user.click(await screen.findByText(PAUL.name));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("already been drafted");
  }, 15000);

  it("edits a pick already made: click the cell, choose somebody else", async () => {
    getDraft.mockResolvedValue(
      draftState({
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );
    editPick.mockResolvedValue(
      draftState({
        picks: [{ playerId: GIANNIS.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );
    const user = userEvent.setup();
    await openRoom();

    await user.click(document.querySelector('[data-cell="1"] button') as HTMLElement);
    await user.type(await screen.findByLabelText(/change this pick to/i), "giannis");
    await user.click(await screen.findByText(GIANNIS.name));

    await waitFor(() => expect(editPick).toHaveBeenCalledTimes(1));
    expect(editPick).toHaveBeenCalledWith(1, { player_id: GIANNIS.espn_player_id });
    await waitFor(() => expect(cellPlayer(1)).toBe(GIANNIS.espn_player_id));
    // The panel closes on success, and the player it replaced is available again.
    await waitFor(() => expect(screen.queryByLabelText(/change this pick to/i)).toBeNull());
  }, 15000);

  it("advances the room to my pick, and steps it one pick at a time", async () => {
    // Seat 3, so one opponent pick does not put me on the clock and disable the buttons —
    // which is itself the behaviour the next test is about.
    getDraft.mockResolvedValue(draftState({ mySlot: 3 }));
    const stepped = draftState({
      mySlot: 3,
      picks: [{ playerId: WEMBY.espn_player_id, isAuto: true }],
    });
    simulate.mockResolvedValue(draftAdvance(stepped, stepped.log));
    const user = userEvent.setup();
    await openRoom();

    await user.click(screen.getByRole("button", { name: /advance to my pick/i }));
    await waitFor(() => expect(simulate).toHaveBeenCalledWith({}));

    await user.click(screen.getByRole("button", { name: /step one pick/i }));
    await waitFor(() => expect(simulate).toHaveBeenCalledWith({ count: 1 }));

    // The room's pick is on the board, marked as the room's.
    await waitFor(() => expect(cellPlayer(1)).toBe(WEMBY.espn_player_id));
    expect(document.querySelector('[data-cell="1"] [data-auto]')).toBeTruthy();
  }, 15000);

  it("offers no advance when I am already on the clock — it would be a no-op", async () => {
    getDraft.mockResolvedValue(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));
    await openRoom();

    const advance = screen.getByRole("button", { name: /advance to my pick/i });
    expect((advance as HTMLButtonElement).disabled).toBe(true);
    expect(advance.getAttribute("title")).toContain("waiting on you");
  });

  it("undoes the last pick and replaces the board from the answer", async () => {
    getDraft.mockResolvedValue(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));
    undoPick.mockResolvedValue(draftState());
    const user = userEvent.setup();
    await openRoom();

    await user.click(screen.getByRole("button", { name: /undo last pick/i }));

    await waitFor(() => expect(undoPick).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(cellPlayer(1)).toBeNull());
  }, 15000);

  it("starts over behind a confirm, and does nothing when it is declined", async () => {
    getDraft.mockResolvedValue(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    await openRoom();

    await user.click(screen.getByRole("button", { name: /start over/i }));
    expect(confirm).toHaveBeenCalled();
    expect(resetDraft).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: /start over/i }));
    await waitFor(() => expect(resetDraft).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(cellPlayer(1)).toBeNull());
  }, 15000);

  it("hides the simulation controls in manual mode and writes nothing", async () => {
    const user = userEvent.setup();
    await openRoom();
    expect(screen.getByRole("button", { name: /advance to my pick/i })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Manual" }));

    expect(screen.queryByRole("button", { name: /advance to my pick/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /step one pick/i })).toBeNull();
    // Manual mode is where the ONE search box earns its keep: it is still here, and it is
    // still addressed to whoever is on the clock, which is how an opponent's pick is typed.
    expect(screen.getByLabelText(/pick 1 for team 1/i)).toBeTruthy();
    // Undo and reset stay too — a mis-entry happens in either mode.
    expect(screen.getByRole("button", { name: /undo last pick/i })).toBeTruthy();
    // And nothing was written: mode is advisory, and there is no endpoint for it.
    expect(createDraft).not.toHaveBeenCalled();
    expect(applyPick).not.toHaveBeenCalled();
  }, 15000);

  it("opens on the mode the draft was started in", async () => {
    getDraft.mockResolvedValue(draftState({ mode: "manual" }));
    await openRoom();

    expect(screen.queryByRole("button", { name: /advance to my pick/i })).toBeNull();
  });

  it("reads the catalog once, and keeps working when it fails", async () => {
    board.mockRejectedValue(new ApiError("/master/board responded 404", 404, "nothing synced"));
    await openRoom();

    expect(board).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/couldn’t read your board/i)).toBeTruthy();
    // The draft itself is untouched: the grid and the clock are both still there.
    expect(document.querySelector("[data-clock]")).toBeTruthy();
  });

  it("marks my column, whether or not the squares in it are filled", async () => {
    await openRoom();

    const header = document.querySelector(`[data-team="${DRAFT_MY_SLOT}"]`);
    expect(header?.textContent).toContain("you");
    // My seat's squares are 2, 7 and 10 in a 4x3 snake — the column the plan is about.
    for (const number of pickNumbersFor(DRAFT_MY_SLOT, DRAFT_TEAMS, DRAFT_ROUNDS)) {
      expect(document.querySelector(`[data-cell="${number}"]`)).toBeTruthy();
    }
  });
});

/* --- the plan panels -------------------------------------------------------------------- */

/**
 * `GET /draft/plan`, on screen.
 *
 * Four claims, and three of them are about restraint rather than about markup:
 *
 * * the panels show my next few picks — not all twenty, because planning from pick 1 means
 *   simulating nearly the whole draft a thousand times for an answer nobody needs yet;
 * * they RE-READ whenever the draft moves, because availability is a statement about the
 *   picks already made and one more pick changes every number in them;
 * * a name drafts the man only from the panel I am on the clock for. Elsewhere it is not a
 *   button at all, because the pick in between hasn't happened and a click could not mean
 *   anything;
 * * a plan that fails is a note beside a working draft, not an error instead of one.
 *
 * The fixture is the same tiny 4x3 draft the rest of the file uses. My seat is 2, so my
 * picks are 2, 7 and 10 and only two of them fit in the default two-panel plan.
 */
describe("the plan panels", () => {
  it("shows my next picks with both lists and the odds on every name", async () => {
    await openRoomOn(draftState());

    // My seat's remaining picks in a 4x3 snake are 2, 7, 10; the plan fixture asks for two.
    expect(panelsOnScreen()).toEqual([2, 7]);

    const next = panelFor(2);
    expect(next.textContent).toContain("round 1");
    // Pick 2 with pick 1 still to come: one away, not on the clock.
    expect(next.getAttribute("data-plan-away")).toBe("1");
    expect(next.textContent).toContain("1 away");

    // Two lists, and they are different lists: the target I tagged, and the top of my board.
    const targets = within(next).getByRole("list", { name: /targets at pick 2/i });
    const best = within(next).getByRole("list", { name: /best available at pick 2/i });
    expect(within(targets).getByText(BOOZER.name)).toBeTruthy();
    expect(within(best).getByText(WEMBY.name)).toBeTruthy();
    expect(within(best).getByText(BOOZER.name)).toBeTruthy();

    // The availability is a NUMBER before it is a colour — both are on the row, and the
    // percentage is what a greyscale screenshot still carries.
    const wemby = best.querySelector(`[data-plan-player="${WEMBY.espn_player_id}"]`);
    const availability = wemby?.querySelector("[data-availability]");
    expect(availability?.getAttribute("data-availability")).toBe("92");
    expect(availability?.getAttribute("data-availability-tone")).toBe("likely");
    expect(availability?.textContent).toContain("92%");

    // And my open needs, which are the same at every planned pick by construction.
    expect(Array.from(next.querySelectorAll("[data-need]")).map((chip) => chip.textContent))
      .toEqual(["PG", "SG", "SF", "PF", "C"]);
  });

  it("falls off across my later picks — the one property the simulation guarantees", async () => {
    await openRoomOn(draftState());

    const at = (pickNumber: number) =>
      Number(
        panelFor(pickNumber)
          .querySelector(`[data-plan-player="${WEMBY.espn_player_id}"] [data-availability]`)
          ?.getAttribute("data-availability"),
      );

    // Pick 7 is five picks further out than pick 2, and nothing of mine intervenes.
    expect(at(7)).toBeLessThan(at(2));
  });

  it("re-reads the plan when the draft moves, because every number in it just changed", async () => {
    const user = userEvent.setup();
    await openRoomOn(draftState());
    const first = plan.mock.calls.length;

    // The room takes Wemby at pick 1 — entered through the search box, the way an
    // opponent's pick always is.
    const after = draftState({ picks: [{ playerId: WEMBY.espn_player_id }] });
    applyPick.mockResolvedValue(after);
    plan.mockResolvedValue(draftPlan({ state: after, best: [BOOZER.espn_player_id] }));

    await user.type(screen.getByLabelText(/pick 1 for team 1/i), "wemb");
    // Scoped to the search result: the plan lists him too, which is the whole point.
    await user.click(
      await waitFor(() => {
        const found = document.querySelector(`[data-candidate="${WEMBY.espn_player_id}"]`);
        if (!found) throw new Error("no candidate yet");
        return found as HTMLElement;
      }),
    );

    await waitFor(() => expect(plan.mock.calls.length).toBeGreaterThan(first));
    // And the re-read is what is on screen: the man who just went is gone from the lists.
    await waitFor(() =>
      expect(
        panelFor(2).querySelector(`[data-plan-player="${WEMBY.espn_player_id}"]`),
      ).toBeNull(),
    );
  }, 15000);

  it("drafts from the panel only when it is my pick AND the panel is that pick", async () => {
    const user = userEvent.setup();
    // Pick 1 is in, so pick 2 — mine — is on the clock and zero away.
    const mine = draftState({ picks: [{ playerId: WEMBY.espn_player_id }] });
    await openRoomOn(mine, { best: [BOOZER.espn_player_id, GIANNIS.espn_player_id] });

    const now = panelFor(2);
    expect(now.getAttribute("data-plan-away")).toBe("0");
    expect(now.textContent).toContain("on the clock");

    // The later panel is reference: no button on it at all, rather than a disabled one that
    // implies a click could ever work there.
    const later = panelFor(7);
    expect(within(later).queryAllByRole("button")).toEqual([]);

    const row = now.querySelector(
      `[data-plan-player="${GIANNIS.espn_player_id}"] button`,
    ) as HTMLElement;
    await user.click(row);

    await waitFor(() => expect(applyPick).toHaveBeenCalledTimes(1));
    // No `team_slot`: the backend defaults it to whoever is on the clock, same as the box.
    expect(applyPick).toHaveBeenCalledWith({ player_id: GIANNIS.espn_player_id });
  }, 15000);

  it("is read-only on every panel while somebody else is on the clock", async () => {
    await openRoomOn(draftState());

    // Pick 2 is mine and it is the next one, but the clock is team 1's — so nothing here
    // is clickable, and the panel is not styled as the one on the clock either.
    expect(panelFor(2).getAttribute("data-plan-away")).toBe("1");
    expect(within(panelFor(2)).queryAllByRole("button")).toEqual([]);
    expect(within(panelFor(7)).queryAllByRole("button")).toEqual([]);
  });

  it("asks for a few picks, and for more only when asked", async () => {
    const user = userEvent.setup();
    // Six rounds, so I own six picks and the opening four are genuinely a subset — in the
    // 4x3 draft the rest of this file uses, four already covers everything I have left.
    await openRoomOn(draftState({ rounds: 6 }));

    expect(plan).toHaveBeenLastCalledWith({ picks: 4 });

    // One press walks it out to all six, and the button goes: there is nothing further to
    // plan for, and offering to plan it anyway would re-run the simulation for nothing.
    await user.click(screen.getByRole("button", { name: /plan 2 more picks/i }));
    await waitFor(() => expect(plan).toHaveBeenLastCalledWith({ picks: 6 }));
    expect(screen.queryByRole("button", { name: /plan .* more/i })).toBeNull();
  }, 15000);

  it("says the draft is over instead of planning for picks that don't exist", async () => {
    await openRoomOn(
      draftState({
        teamCount: 2,
        rounds: 1,
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );

    expect(panelsOnScreen()).toEqual([]);
    expect(document.querySelector('[data-plan="empty"]')?.textContent).toMatch(
      /draft is over/i,
    );
  });

  it("degrades to a note when the plan fails, and leaves the draft working", async () => {
    getDraft.mockResolvedValue(draftState());
    plan.mockRejectedValue(new ApiError("/draft/plan responded 404", 404, "There is no draft."));
    render(<DraftRoomPage />);
    await screen.findByRole("grid");

    const note = await waitFor(() => {
      const found = document.querySelector('[data-plan="unavailable"]');
      if (!found) throw new Error("no notice yet");
      return found;
    });
    expect(note.textContent).toContain("There is no draft.");
    // A note, not the page's error panel — and the grid and clock are untouched.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.querySelector("[data-clock]")).toBeTruthy();
  });
});
