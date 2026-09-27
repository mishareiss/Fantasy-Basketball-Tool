import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type DraftStateResponse, type MasterPlayerRow } from "@/lib/api";
import { DraftRoomPage } from "@/components/draft/DraftRoomPage";
import {
  availabilityOf,
  availableBoard,
  boardColumn,
  candidates,
  cellOf,
  cellPickNumber,
  draftedIds,
  filterAvailable,
  picksByNumber,
  pickNumbersFor,
  playersById,
  positionColumn,
  rankingsColumns,
  roundOf,
  teamRoster,
  tierRuns,
} from "@/lib/draft";
import {
  DRAFT_MY_SLOT,
  DRAFT_ROUNDS,
  DRAFT_TEAMS,
  MASTER_SEEDS,
  draftAdvance,
  draftAvailability,
  draftState,
  masterBoard,
  snakeOrder,
} from "./fixtures";

/**
 * The draft room, and the arithmetic underneath it.
 *
 * Three kinds of test here. The first is `lib/draft`'s SNAKE on its own — the cell-to-pick
 * mapping, checked against a hand-written 4x3 grid and against the pick numbers the
 * backend's own test asserts for slot 2 of 10. It gets its own section because a grid whose
 * squares are off by a column still renders as a working board: nothing else would notice.
 *
 * The second is `lib/draft`'s BOARD DERIVATIONS — the available list, the six rankings
 * columns and their tiers — over a hand-written board of six players. Same argument: a
 * column that silently drops a man, or bands him one tier too low, looks like a working
 * page. The fixture is deliberately tiny and stated inline, so the expected answer can be
 * counted by eye.
 *
 * The third is the PAGE, with the api mocked wholesale (`ApiError` kept real, so the 404
 * that means "no draft yet" is told apart from a failure by its status). Its claims:
 *
 * * `GET /draft` answering 404 is the SETUP FORM, not an error panel;
 * * the four views are four tabs and the Board is the one you land on;
 * * the board fits — every seat is a column and nothing scrolls sideways;
 * * the sidebar is the standing list of who is left, and its three filters narrow it;
 * * the sidebar's second tab is a lineup card, switchable by seat and defaulting to mine;
 * * a Draft button enters the pick ON THE CLOCK whoever owns it — there is no board search
 *   any more, so that button is the room's only way in — and availability is re-read when the
 *   draft moves;
 * * the seat is changeable while the draft is empty and not afterwards.
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
      draftAvailability: vi.fn(),
      updateDraftConfig: vi.fn(),
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
const availability = vi.mocked(api.draftAvailability);
const updateConfig = vi.mocked(api.updateDraftConfig);
const board = vi.mocked(api.masterBoard);

const [WEMBY, BOOZER, GIANNIS, PAUL] = MASTER_SEEDS;

/** What a cell shows, by pick number: the player id in it, or null for an empty square. */
function cellPlayer(pickNumber: number): number | null {
  const cell = document.querySelector(`[data-cell="${pickNumber}"]`);
  if (!cell) throw new Error(`no cell for pick ${pickNumber}`);
  const player = cell.getAttribute("data-player");
  return player === null ? null : Number(player);
}

/** Open the room on one draft, with the availability read derived FROM that draft. */
async function openRoomOn(state: DraftStateResponse = draftState()) {
  getDraft.mockResolvedValue(state);
  availability.mockResolvedValue(draftAvailability(state));
  render(<DraftRoomPage />);
  await screen.findByRole("grid");
  await waitFor(() => expect(availability).toHaveBeenCalled());
}

const openRoom = openRoomOn;

/** The sidebar, and the player ids currently listed in it. */
function sidebar(tab: "available" | "teams" = "available"): HTMLElement {
  const found = document.querySelector(`[data-sidebar="${tab}"]`);
  if (!found) throw new Error(`no ${tab} sidebar`);
  return found as HTMLElement;
}

/** The sidebar's Draft button for one player — the room's only way to enter a pick. */
function draftButton(name: string): HTMLButtonElement {
  return within(sidebar()).getByRole("button", { name: `Draft ${name}` }) as HTMLButtonElement;
}

/** The Teams tab's lineup card: each row's slot LABEL as printed, and the name in it. */
function slotRows(): [string, string | null][] {
  return Array.from(sidebar("teams").querySelectorAll("[data-roster-slot]")).map((row) => [
    row.querySelector("[data-slot-label]")?.textContent ?? "",
    row.querySelector("[data-slot-player]")?.textContent ?? null,
  ]);
}

/** One row of the pick-entry search's results — the sidebar carries the same names. */
async function candidateFor(playerId: number): Promise<HTMLElement> {
  return await waitFor(() => {
    const found = document.querySelector(`[data-candidate="${playerId}"]`);
    if (!found) throw new Error(`no candidate for ${playerId} yet`);
    return found as HTMLElement;
  });
}

function listedIn(container: HTMLElement): number[] {
  return Array.from(container.querySelectorAll("[data-available-player]")).map((row) =>
    Number(row.getAttribute("data-available-player")),
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
  updateConfig.mockResolvedValue(draftState());
  simulate.mockResolvedValue(draftAdvance(draftState(), []));
  availability.mockResolvedValue(draftAvailability());
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
    expect(candidates(catalog, new Set([GIANNIS.espn_player_id]), "antetokounmpo")).toEqual([]);
  });

  it("stops at the limit", () => {
    expect(candidates(catalog, new Set(), "a", 2).length).toBe(2);
  });

  it("reads the drafted set and the log index off the live state", () => {
    const state = draftState({
      picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
    });
    expect(draftedIds(state)).toEqual(new Set([WEMBY.espn_player_id, BOOZER.espn_player_id]));
    expect(picksByNumber(state.log).get(2)?.espn_player_id).toBe(BOOZER.espn_player_id);
  });
});

/* --- the board derivations -------------------------------------------------------------- */

/**
 * Six players, hand-written, because the interesting claims are all about COUNTING.
 *
 * The guards are the payoff: three point guards on the board (1, 3, 5), cut into two tiers
 * at PG ranks 1 and 3. Draft the first two and the third is still a TIER 2 point guard —
 * his band is his place in the whole position order, not his place in what is left. A test
 * over the available list alone would call him tier 1 and read as though it passed.
 */
function player(
  rank: number,
  id: number,
  name: string,
  positions: string[],
  extra: Partial<MasterPlayerRow> = {},
): MasterPlayerRow {
  return {
    rank,
    espn_player_id: id,
    name,
    nba_team: "FA",
    positions,
    age: 25,
    tag: null,
    note: null,
    excluded: false,
    is_new: false,
    is_stale: false,
    consensus_rank: rank,
    delta: 0,
    overall_tier: null,
    position_tier: null,
    position_scope: null,
    drafted: false,
    drafted_by_slot: null,
    drafted_by_me: false,
    updated_at: "2027-10-01T09:00:00Z",
    ...extra,
  };
}

const HAND = [
  player(1, 101, "Ada Guard", ["PG"], { tag: "target" }),
  player(2, 102, "Bo Wing", ["SG", "SF"]),
  player(3, 103, "Cy Point", ["PG"]),
  player(4, 104, "Di Big", ["C"], { tag: "fade", nba_team: "MIL" }),
  player(5, 105, "Ed Combo", ["PG", "SG"]),
  player(null as unknown as number, 106, "Fi Aside", ["C"], { excluded: true, rank: null }),
];

const HAND_TIERS = [
  { scope: "overall", size: 5, cut_ranks: [1, 3], tier_count: 2 },
  { scope: "PG", size: 3, cut_ranks: [1, 3], tier_count: 2 },
  { scope: "SG", size: 2, cut_ranks: [1], tier_count: 1 },
  { scope: "SF", size: 1, cut_ranks: [1], tier_count: 1 },
  { scope: "PF", size: 0, cut_ranks: [], tier_count: 0 },
  { scope: "C", size: 1, cut_ranks: [1], tier_count: 1 },
];

/**
 * The lineup card, which is a RULE and not a fact on the response: nothing stored says which
 * slot a drafted player occupies. `teamRoster` decides it, and it has to decide it exactly the
 * way `app/draft/needs.py:RosterFill.add` does — the same greedy assignment already produces
 * every seat's `open_needs`, so a second reading of it would put a man in the PG slot on screen
 * while the backend went on calling PG a need.
 */
describe("lib/draft — a team's roster, laid out in slots", () => {
  const byId = playersById(HAND);
  /** Our shape, shrunk: one of each starter, two utility, two bench. */
  const SLOTS = { PG: 1, SG: 1, SF: 1, PF: 1, C: 1, UT: 2, BE: 2 };
  const labels = (rows: ReturnType<typeof teamRoster>) => rows.map((row) => row.slot);
  const filled = (rows: ReturnType<typeof teamRoster>) =>
    rows.filter((row) => row.occupant !== null).map((row) => [row.slot, row.occupant?.name]);

  it("runs the card in lineup order however the roster dict is written", () => {
    // Written back to front, and read PG, SG, SF, PF, C, UT, bench anyway.
    const scrambled = { BE: 2, UT: 2, C: 1, PF: 1, SF: 1, SG: 1, PG: 1 };
    expect(labels(teamRoster([], byId, scrambled))).toEqual([
      "PG",
      "SG",
      "SF",
      "PF",
      "C",
      "UT",
      "UT",
      "BE",
      "BE",
    ]);
    // An empty seat is the whole card, every slot open.
    expect(filled(teamRoster([], byId, SLOTS))).toEqual([]);
    // And a position with two slots gets two rows.
    expect(labels(teamRoster([], byId, { PG: 2, C: 1 }))).toEqual(["PG", "PG", "C"]);
  });

  it("gives a PG/SG the PG slot while both are open, and the next guard the other", () => {
    // Ed Combo is listed PG/SG, and PG comes first — the backend's own greedy order.
    expect(filled(teamRoster([105], byId, SLOTS))).toEqual([["PG", "Ed Combo"]]);
    // Bo Wing is SG/SF: he takes SG, so Ed still lands at PG rather than being crowded out.
    expect(filled(teamRoster([102, 105], byId, SLOTS))).toEqual([
      ["PG", "Ed Combo"],
      ["SG", "Bo Wing"],
    ]);
  });

  it("sends the overflow guard to UT and the one after him to the bench", () => {
    // One point guard slot, one utility, one bench — and three men who are only guards.
    const thin = { PG: 1, UT: 1, BE: 1 };
    const rows = teamRoster([101, 103, 105], byId, thin);

    expect(rows.map((row) => [row.slot, row.occupant?.name])).toEqual([
      ["PG", "Ada Guard"],
      ["UT", "Cy Point"],
      ["BE", "Ed Combo"],
    ]);
  });

  it("leaves the slots nobody fills blank, in place", () => {
    // Bo Wing (SG/SF) and Di Big (C): two filled slots, seven open ones, order untouched.
    const rows = teamRoster([102, 104], byId, SLOTS);

    expect(rows.map((row) => [row.slot, row.occupant?.name ?? null])).toEqual([
      ["PG", null],
      ["SG", "Bo Wing"],
      ["SF", null],
      ["PF", null],
      ["C", "Di Big"],
      ["UT", null],
      ["UT", null],
      ["BE", null],
      ["BE", null],
    ]);
  });

  it("lists a player the catalog has never heard of rather than dropping him", () => {
    // He is in the log, so the seat really does hold him — and with no positions to be
    // eligible at, the positionless slot is where he belongs.
    const rows = teamRoster([999], byId, SLOTS);

    expect(filled(rows)).toEqual([["UT", "Player 999"]]);
    expect(rows.find((row) => row.occupant !== null)?.occupant?.playerId).toBe(999);
  });
});

describe("lib/draft — the available board and its columns", () => {
  it("drops whoever is drafted, whoever is set aside, and nobody else", () => {
    const available = availableBoard(HAND, new Set([102]));

    expect(available.map((row) => row.espn_player_id)).toEqual([101, 103, 104, 105]);
    // Order is MY order whatever the array came in as.
    expect(available.map((row) => row.rank)).toEqual([1, 3, 4, 5]);
  });

  it("puts a two-position player in each of his columns", () => {
    const available = availableBoard(HAND, new Set());
    const sg = positionColumn(available, HAND, HAND_TIERS, "SG");
    const sf = positionColumn(available, HAND, HAND_TIERS, "SF");

    expect(sg.rows.map((row) => row.player.espn_player_id)).toEqual([102, 105]);
    expect(sf.rows.map((row) => row.player.espn_player_id)).toEqual([102]);
  });

  it("tiers a position by his band in the FULL order, not in what is left", () => {
    // Every point guard is there: PG ranks 1, 2, 3 and the cuts are at 1 and 3.
    const whole = positionColumn(availableBoard(HAND, new Set()), HAND, HAND_TIERS, "PG");
    expect(whole.rows.map((row) => [row.scopeRank, row.tier])).toEqual([
      [1, 1],
      [2, 1],
      [3, 2],
    ]);

    // The first two guards go. The third is STILL tier 2 — the band is over the board, and
    // the board does not renumber itself because the room took two men out of it.
    const thinned = positionColumn(
      availableBoard(HAND, new Set([101, 103])),
      HAND,
      HAND_TIERS,
      "PG",
    );
    expect(thinned.rows.map((row) => [row.player.espn_player_id, row.scopeRank, row.tier])).toEqual(
      [[105, 3, 2]],
    );
    // And the divider is drawn, because tier 2 is where this column now starts.
    expect(thinned.rows[0].startsTier).toBe(true);
  });

  it("gives a position nobody plays an empty column rather than an error", () => {
    const column = positionColumn(availableBoard(HAND, new Set()), HAND, HAND_TIERS, "PF");
    expect(column.rows).toEqual([]);
    expect(column.size).toBe(0);
  });

  it("reads the overall column off board ranks and the board's own cuts", () => {
    const column = boardColumn(availableBoard(HAND, new Set()), HAND, HAND_TIERS, "overall");

    expect(column.rows.map((row) => [row.scopeRank, row.tier])).toEqual([
      [1, 1],
      [2, 1],
      [3, 2],
      [4, 2],
      [5, 2],
    ]);
    // Two bands, so two dividers — one at the top of each.
    expect(column.rows.filter((row) => row.startsTier).map((row) => row.tier)).toEqual([1, 2]);
    expect(tierRuns(column.rows).map((run) => [run.tier, run.rows.length])).toEqual([
      [1, 2],
      [2, 3],
    ]);
  });

  it("builds the six columns the rankings view draws, in lineup order", () => {
    const columns = rankingsColumns(availableBoard(HAND, new Set()), HAND, HAND_TIERS);

    expect(columns.map((column) => column.scope)).toEqual([
      "overall",
      "PG",
      "SG",
      "SF",
      "PF",
      "C",
    ]);
    expect(columns.map((column) => column.rows.length)).toEqual([5, 3, 2, 1, 0, 1]);
  });

  it("narrows the sidebar three ways and reorders it none", () => {
    const available = availableBoard(HAND, new Set());
    const ids = (rows: MasterPlayerRow[]) => rows.map((row) => row.espn_player_id);
    const filters = { term: "", targetsOnly: false, positions: [] as never[] };

    expect(ids(filterAvailable(available, filters))).toEqual([101, 102, 103, 104, 105]);
    expect(ids(filterAvailable(available, { ...filters, targetsOnly: true }))).toEqual([101]);
    // Name or team, the same matcher /my-board uses.
    expect(ids(filterAvailable(available, { ...filters, term: "combo" }))).toEqual([105]);
    expect(ids(filterAvailable(available, { ...filters, term: "mil" }))).toEqual([104]);
    // Positions are multi-select and read as ANY of them.
    expect(ids(filterAvailable(available, { ...filters, positions: ["C"] }))).toEqual([104]);
    expect(ids(filterAvailable(available, { ...filters, positions: ["C", "SF"] }))).toEqual([
      102, 104,
    ]);
    // And they compose without reordering anything.
    expect(
      ids(filterAvailable(available, { term: "", targetsOnly: true, positions: ["C"] })),
    ).toEqual([]);
  });

  it("tells a player nobody ranks apart from one who is gone in every room", () => {
    expect(availabilityOf({ "101": 0.42 }, 101)).toBe(0.42);
    expect(availabilityOf({ "101": 0 }, 101)).toBe(0);
    expect(availabilityOf({ "101": 0.42 }, 999)).toBeNull();
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
    await openRoomOn(
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
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

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
    await openRoomOn(
      draftState({
        teamCount: 2,
        rounds: 1,
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );

    expect(document.querySelector("[data-clock]")?.getAttribute("data-clock")).toBe("complete");
    // Nothing left to enter, so the sidebar says so and its buttons are dead.
    expect(sidebar().querySelector("[data-sidebar-clock]")?.textContent).toContain(
      "The draft is over",
    );
    for (const button of within(sidebar()).queryAllByRole("button", { name: /^Draft / })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("has no search box above the board, but a made pick can still be re-searched", async () => {
    const user = userEvent.setup();
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

    // The standing entry box is gone in both its forms: mine and an opponent's.
    expect(screen.queryByLabelText(/pick \d+ for team/i)).toBeNull();
    expect(screen.queryByLabelText(/^your pick/i)).toBeNull();
    expect(screen.queryByPlaceholderText(/type a name/i)).toBeNull();
    // What is left is the sidebar's list, and its button is live for the seat on the clock.
    expect(draftButton(BOOZER.name).disabled).toBe(false);

    // The edit panel keeps its own search, because re-deciding pick 1 is a different act
    // from making pick 2 and the sidebar only ever talks about the pick that is next.
    await user.click(document.querySelector('[data-cell="1"] button') as HTMLElement);
    expect(await screen.findByLabelText(/change this pick to/i)).toBeTruthy();
    expect(screen.getByPlaceholderText(/type a name/i)).toBeTruthy();
  }, 15000);

  it("enters the pick on the clock from the sidebar even when it is not mine", async () => {
    // Pick 1 is team 1's, so this is the manual-mode case the board search used to cover:
    // find what the room just took, press Draft, and the snake puts him in seat 1.
    const after = draftState({ picks: [{ playerId: GIANNIS.espn_player_id }] });
    applyPick.mockResolvedValue(after);
    const user = userEvent.setup();
    await openRoom();

    expect(sidebar().querySelector("[data-sidebar-clock]")?.textContent).toContain(
      "Pick 1 · Team 1",
    );
    await user.click(draftButton(GIANNIS.name));

    await waitFor(() => expect(applyPick).toHaveBeenCalledTimes(1));
    // No `team_slot`: the backend defaults it to the clock, and the snake has only one answer.
    expect(applyPick).toHaveBeenCalledWith({ player_id: GIANNIS.espn_player_id });
    // The board on screen is the one the server answered with, not a local patch.
    await waitFor(() => expect(cellPlayer(1)).toBe(GIANNIS.espn_player_id));
    expect(document.querySelector("[data-clock]")?.getAttribute("data-clock")).toBe("mine");
  }, 15000);

  it("surfaces the backend's refusal rather than swallowing it", async () => {
    applyPick.mockRejectedValue(
      new ApiError("/draft/picks responded 422", 422, "player 2779 has already been drafted"),
    );
    const user = userEvent.setup();
    await openRoom();

    await user.click(draftButton(PAUL.name));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("already been drafted");
  }, 15000);

  it("edits a pick already made: click the cell, choose somebody else", async () => {
    editPick.mockResolvedValue(
      draftState({
        picks: [{ playerId: GIANNIS.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );
    const user = userEvent.setup();
    await openRoomOn(
      draftState({
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );

    await user.click(document.querySelector('[data-cell="1"] button') as HTMLElement);
    await user.type(await screen.findByLabelText(/change this pick to/i), "giannis");
    await user.click(await candidateFor(GIANNIS.espn_player_id));

    await waitFor(() => expect(editPick).toHaveBeenCalledTimes(1));
    expect(editPick).toHaveBeenCalledWith(1, { player_id: GIANNIS.espn_player_id });
    await waitFor(() => expect(cellPlayer(1)).toBe(GIANNIS.espn_player_id));
    // The panel closes on success, and the player it replaced is available again.
    await waitFor(() => expect(screen.queryByLabelText(/change this pick to/i)).toBeNull());
  }, 15000);

  it("advances the room to my pick, and steps it one pick at a time", async () => {
    // Seat 3, so one opponent pick does not put me on the clock and disable the buttons —
    // which is itself the behaviour the next test is about.
    const stepped = draftState({
      mySlot: 3,
      picks: [{ playerId: WEMBY.espn_player_id, isAuto: true }],
    });
    simulate.mockResolvedValue(draftAdvance(stepped, stepped.log));
    const user = userEvent.setup();
    await openRoomOn(draftState({ mySlot: 3 }));

    await user.click(screen.getByRole("button", { name: /advance to my pick/i }));
    await waitFor(() => expect(simulate).toHaveBeenCalledWith({}));

    await user.click(screen.getByRole("button", { name: /step one pick/i }));
    await waitFor(() => expect(simulate).toHaveBeenCalledWith({ count: 1 }));

    // The room's pick is on the board, marked as the room's.
    await waitFor(() => expect(cellPlayer(1)).toBe(WEMBY.espn_player_id));
    expect(document.querySelector('[data-cell="1"] [data-auto]')).toBeTruthy();
  }, 15000);

  it("offers no advance when I am already on the clock — it would be a no-op", async () => {
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

    const advance = screen.getByRole("button", { name: /advance to my pick/i });
    expect((advance as HTMLButtonElement).disabled).toBe(true);
    expect(advance.getAttribute("title")).toContain("waiting on you");
  });

  it("undoes the last pick and replaces the board from the answer", async () => {
    undoPick.mockResolvedValue(draftState());
    const user = userEvent.setup();
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

    await user.click(screen.getByRole("button", { name: /undo last pick/i }));

    await waitFor(() => expect(undoPick).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(cellPlayer(1)).toBeNull());
  }, 15000);

  it("starts over behind a confirm, and does nothing when it is declined", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

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
    // Manual mode is where the sidebar's button earns its keep: it is still live, and it is
    // still addressed to whoever is on the clock, which is how an opponent's pick is entered.
    expect(sidebar().querySelector("[data-sidebar-clock]")?.textContent).toContain(
      "Pick 1 · Team 1",
    );
    expect(draftButton(WEMBY.name).disabled).toBe(false);
    // Undo and reset stay too — a mis-entry happens in either mode.
    expect(screen.getByRole("button", { name: /undo last pick/i })).toBeTruthy();
    // And nothing was written: mode is advisory, and there is no endpoint for it.
    expect(createDraft).not.toHaveBeenCalled();
    expect(applyPick).not.toHaveBeenCalled();
  }, 15000);

  it("opens on the mode the draft was started in", async () => {
    await openRoomOn(draftState({ mode: "manual" }));

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
});

/* --- the four views --------------------------------------------------------------------- */

describe("the tabbed views", () => {
  it("opens on the board and switches to each of the other three", async () => {
    const user = userEvent.setup();
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

    // The Board is where you land, and the old stacked plan panels are gone for good.
    expect(document.querySelector('[data-board="fluid"]')).toBeTruthy();
    expect(document.querySelector("[data-plan-pick]")).toBeNull();

    await user.click(screen.getByRole("button", { name: "List" }));
    expect(document.querySelector('[data-view="list"]')).toBeTruthy();
    expect(screen.queryByRole("grid")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Roster" }));
    expect(document.querySelector('[data-view="roster"]')).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Rankings" }));
    expect(document.querySelector('[data-view="rankings"]')).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Board" }));
    expect(screen.getByRole("grid")).toBeTruthy();
  }, 15000);

  it("fits every seat on the board with no sideways scroll", async () => {
    await openRoom();

    const grid = screen.getByRole("grid");
    // A fluid grid: one round column, then one `1fr` track per seat, on every row. Not the
    // old fixed `w-36` cells inside a `min-w-max` box, which is what made it scroll.
    expect(grid.className).not.toContain("min-w-max");
    expect(grid.style.getPropertyValue("--teams")).toBe(String(DRAFT_TEAMS));
    for (const row of Array.from(grid.querySelectorAll('[role="row"]'))) {
      expect(row.className).toContain("grid-cols-[2.25rem_repeat(var(--teams),minmax(0,1fr))]");
    }
    expect(grid.querySelectorAll("[data-team]").length).toBe(DRAFT_TEAMS);
    for (const cell of Array.from(grid.querySelectorAll("[data-cell]"))) {
      // No `w-36`-style fixed width anywhere; `min-w-0` is the opposite instruction.
      expect(cell.className).not.toMatch(/(?:^|\s)w-\d/);
    }
    // And nothing between the grid and the page scrolls sideways.
    for (
      let node: HTMLElement | null = grid;
      node !== null;
      node = node.parentElement as HTMLElement | null
    ) {
      expect(node.className ?? "").not.toContain("overflow-x-auto");
    }
  });

  it("heads every column with the team's name, and marks mine", async () => {
    await openRoomOn(draftState({ teamNames: { "1": "Sam", "3": "The Process" } }));

    const headers = Array.from(document.querySelectorAll("[data-team]")).map(
      (cell) => cell.textContent,
    );
    // Named seats carry their name; unnamed ones are "Team N", the backend's own default.
    expect(headers).toEqual(["Sam", "Team 2 (You)", "The Process", "Team 4"]);
  });

  it("groups the list view by round, with the overall pick numbers", async () => {
    await openRoomOn(
      draftState({
        picks: [
          { playerId: WEMBY.espn_player_id },
          { playerId: BOOZER.espn_player_id },
          { playerId: GIANNIS.espn_player_id },
          { playerId: PAUL.espn_player_id },
          { playerId: 4278067 },
        ],
        teamNames: { "1": "Sam" },
      }),
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "List" }));

    const rounds = Array.from(document.querySelectorAll("[data-round]")).map((section) =>
      section.getAttribute("data-round"),
    );
    expect(rounds).toEqual(["1", "2"]);

    // Round 1 is picks 1-4 in snake order; round 2 starts at 5, which is seat 4's.
    const first = document.querySelector('[data-round="1"]') as HTMLElement;
    expect(
      Array.from(first.querySelectorAll("[data-list-pick]")).map((row) =>
        row.getAttribute("data-list-pick"),
      ),
    ).toEqual(["1", "2", "3", "4"]);
    // The row says the number, the team by NAME, and the player.
    const opener = first.querySelector('[data-list-pick="1"]') as HTMLElement;
    expect(opener.textContent).toContain("1");
    expect(opener.textContent).toContain("Sam");
    expect(opener.textContent).toContain(WEMBY.name);
    // Mine is marked wherever it appears.
    expect(first.querySelector('[data-list-pick="2"]')?.textContent).toContain("(You)");

    const second = document.querySelector('[data-round="2"]') as HTMLElement;
    expect(
      Array.from(second.querySelectorAll("[data-list-pick]")).map((row) =>
        row.getAttribute("data-list-pick"),
      ),
    ).toEqual(["5"]);
  }, 15000);

  it("shows every team's roster, unmade picks included, as numbered blanks", async () => {
    const user = userEvent.setup();
    await openRoomOn(
      draftState({
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
      }),
    );
    await user.click(screen.getByRole("button", { name: "Roster" }));

    const mine = document.querySelector(`[data-roster-team="${DRAFT_MY_SLOT}"]`) as HTMLElement;
    expect(mine.textContent).toContain("Team 2 (You)");
    // My seat owns 2, 7 and 10 in a 4x3 snake — all three are rows, in ascending order.
    const rows = Array.from(mine.querySelectorAll("[data-roster-pick]"));
    expect(rows.map((row) => row.getAttribute("data-roster-pick"))).toEqual(["2", "7", "10"]);
    // The one that happened has a name on it; the two that haven't are numbered blanks.
    expect(rows[0].textContent).toContain(BOOZER.name);
    expect(rows[0].hasAttribute("data-unmade")).toBe(false);
    expect(rows[1].hasAttribute("data-unmade")).toBe(true);
    expect(rows[2].hasAttribute("data-unmade")).toBe(true);
    // A roster is reference: nothing here drafts anybody.
    expect(within(mine).queryAllByRole("button")).toEqual([]);
  }, 15000);

  it("draws six rankings columns with my tiers, the odds, and a draft button each", async () => {
    const user = userEvent.setup();
    // My pick, so the buttons are live — pick 1 is in and pick 2 is mine.
    await openRoomOn(draftState({ picks: [{ playerId: PAUL.espn_player_id }] }));
    await user.click(screen.getByRole("button", { name: "Rankings" }));

    const columns = Array.from(document.querySelectorAll("[data-column]")).map((column) =>
      column.getAttribute("data-column"),
    );
    expect(columns).toEqual(["overall", "PG", "SG", "SF", "PF", "C"]);

    // The power forwards are Boozer and Giannis, and MASTER_CUTS cuts them into two tiers —
    // so that column carries a divider between them.
    const pf = document.querySelector('[data-column="PF"]') as HTMLElement;
    expect(listedIn(pf)).toEqual([BOOZER.espn_player_id, GIANNIS.espn_player_id]);
    expect(
      Array.from(pf.querySelectorAll("[data-tier-divider]")).map((line) =>
        line.getAttribute("data-tier-divider"),
      ),
    ).toEqual(["1", "2"]);

    // Chris Paul went at pick 1, so the point guards are empty and he is nowhere.
    const pg = document.querySelector('[data-column="PG"]') as HTMLElement;
    expect(listedIn(pg)).toEqual([]);

    // Every row carries its percentage and a live Draft button, because it is my pick.
    const wemby = pf.ownerDocument.querySelector(
      `[data-column="overall"] [data-available-player="${WEMBY.espn_player_id}"]`,
    ) as HTMLElement;
    expect(wemby.querySelector("[data-availability]")?.textContent).toMatch(/%$/);
    const draft = within(wemby).getByRole("button", { name: `Draft ${WEMBY.name}` });
    await user.click(draft);
    await waitFor(() => expect(applyPick).toHaveBeenCalledWith({ player_id: WEMBY.espn_player_id }));
  }, 15000);
});

/* --- the sidebar ------------------------------------------------------------------------ */

describe("the available sidebar", () => {
  it("lists who is left in my order, with tags and the odds", async () => {
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

    // Wemby went at pick 1; the rest are in board order. The catalog is never the source of
    // who is gone — that comes off the live log.
    expect(listedIn(sidebar())).toEqual([
      BOOZER.espn_player_id,
      GIANNIS.espn_player_id,
      PAUL.espn_player_id,
    ]);

    const boozer = sidebar().querySelector(
      `[data-available-player="${BOOZER.espn_player_id}"]`,
    ) as HTMLElement;
    expect(boozer.textContent).toContain(BOOZER.name);
    expect(boozer.textContent).toContain("PF");
    expect(boozer.querySelector("[data-tag]")?.getAttribute("data-tag")).toBe("target");
    expect(boozer.querySelector("[data-availability]")?.textContent).toMatch(/^\d+%$/);
  });

  it("narrows by search, by targets-only and by position", async () => {
    const user = userEvent.setup();
    await openRoom();
    const panel = sidebar();

    await user.type(within(panel).getByPlaceholderText(/search/i), "giannis");
    expect(listedIn(panel)).toEqual([GIANNIS.espn_player_id]);
    await user.clear(within(panel).getByPlaceholderText(/search/i));

    await user.click(panel.querySelector('[data-filter="targets"]') as HTMLElement);
    expect(listedIn(panel)).toEqual([BOOZER.espn_player_id]);
    await user.click(panel.querySelector('[data-filter="targets"]') as HTMLElement);

    // Multi-select: a centre and a point guard, both showing, in board order.
    await user.click(panel.querySelector('[data-filter="position-C"]') as HTMLElement);
    expect(listedIn(panel)).toEqual([WEMBY.espn_player_id]);
    await user.click(panel.querySelector('[data-filter="position-PG"]') as HTMLElement);
    expect(listedIn(panel)).toEqual([WEMBY.espn_player_id, PAUL.espn_player_id]);

    await user.click(panel.querySelector('[data-filter="position-all"]') as HTMLElement);
    expect(listedIn(panel).length).toBe(4);
  }, 15000);

  it("says whose pick its button would enter, mine or somebody else's", async () => {
    await openRoom();

    // Somebody else's, and live anyway: with the board search gone this is the only way to
    // enter what the room just did, so the panel says which pick it is about.
    const theirs = draftButton(WEMBY.name);
    expect(theirs.disabled).toBe(false);
    expect(theirs.getAttribute("title")).toContain("Pick 1 · Team 1");
    expect(sidebar().querySelector("[data-sidebar-clock]")?.textContent).toContain(
      "Pick 1 · Team 1",
    );
  });

  it("drafts him straight off the row when the clock is mine", async () => {
    const user = userEvent.setup();
    // Pick 1 is in, so pick 2 — my seat's — is on the clock.
    await openRoomOn(draftState({ picks: [{ playerId: PAUL.espn_player_id }] }));

    const live = draftButton(WEMBY.name);
    expect(live.disabled).toBe(false);
    expect(live.getAttribute("title")).toContain("Your pick");

    await user.click(live);

    // No `team_slot`: the backend defaults it to the clock, whoever that is.
    await waitFor(() => expect(applyPick).toHaveBeenCalledWith({ player_id: WEMBY.espn_player_id }));
  }, 15000);

  it("re-reads availability when the draft moves, because every number just changed", async () => {
    const user = userEvent.setup();
    await openRoom();
    const first = availability.mock.calls.length;

    const after = draftState({ picks: [{ playerId: WEMBY.espn_player_id }] });
    applyPick.mockResolvedValue(after);
    availability.mockResolvedValue(draftAvailability(after));

    await user.click(draftButton(WEMBY.name));

    await waitFor(() => expect(availability.mock.calls.length).toBeGreaterThan(first));
    // And the man who just went is out of the sidebar, off the live log.
    await waitFor(() => expect(listedIn(sidebar())).not.toContain(WEMBY.espn_player_id));
  }, 15000);

  it("labels the percentages with the FUTURE pick they are about", async () => {
    // Pick 1 is in, so pick 2 — mine — is on the clock. Everybody would be 100% at pick 2, so
    // the numbers are about pick 7, my next one after this, and the panel says so.
    await openRoomOn(draftState({ picks: [{ playerId: PAUL.espn_player_id }] }));

    const target = sidebar().querySelector("[data-availability-target]");
    expect(target?.getAttribute("data-availability-target")).toBe("7");
    expect(target?.textContent).toContain("7");
    const wemby = sidebar().querySelector(
      `[data-available-player="${WEMBY.espn_player_id}"] [data-availability]`,
    );
    // Five picks of waiting, so he is no longer a certainty, and the tooltip names the pick.
    expect(wemby?.getAttribute("data-availability")).not.toBe("100");
    expect(wemby?.getAttribute("title")).toContain("pick 7");
  });

  it("swaps Available's filters for Teams' dropdown and back", async () => {
    const user = userEvent.setup();
    await openRoom();

    await user.click(screen.getByRole("button", { name: "Teams" }));
    expect(document.querySelector('[data-sidebar="teams"]')).toBeTruthy();
    expect(document.querySelector('[data-sidebar="available"]')).toBeNull();
    // Available's controls are gone with it — a position chip that narrowed a lineup card is
    // not a question anybody has.
    expect(screen.queryByPlaceholderText(/search name or team/i)).toBeNull();
    expect(document.querySelector('[data-filter="targets"]')).toBeNull();

    await user.click(screen.getByRole("button", { name: "Available" }));
    expect(document.querySelector('[data-sidebar="available"]')).toBeTruthy();
    expect(listedIn(sidebar()).length).toBe(4);
  }, 15000);

  it("opens Teams on my own card, in slots, with the empty ones blank", async () => {
    const user = userEvent.setup();
    // Pick 1 is team 1's (Wembanyama, a centre); pick 2 is mine (Boozer, a power forward).
    await openRoomOn(
      draftState({
        picks: [{ playerId: WEMBY.espn_player_id }, { playerId: BOOZER.espn_player_id }],
        teamNames: { "1": "Sam" },
      }),
    );
    await user.click(screen.getByRole("button", { name: "Teams" }));

    // Mine by default, named and marked.
    const select = sidebar("teams").querySelector("[data-team-select]") as HTMLSelectElement;
    expect(select.value).toBe(String(DRAFT_MY_SLOT));
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      "Sam",
      "Team 2 (You)",
      "Team 3",
      "Team 4",
    ]);

    // The card is the roster's shape: five starters, two utility, then the bench.
    const rows = slotRows();
    expect(rows.slice(0, 7).map(([slot]) => slot)).toEqual([
      "PG",
      "SG",
      "SF",
      "PF",
      "C",
      "UT",
      "UT",
    ]);
    expect(rows.length).toBe(20);
    // Boozer is a PF and that slot was open, so that is where he is — and he is the only name
    // on my card. Every other slot, the utility ones included, is a labelled blank.
    expect(rows.filter(([, name]) => name !== null)).toEqual([["PF", BOOZER.name]]);
    expect(rows.filter(([slot, name]) => slot === "UT" && name === null).length).toBe(2);

    // A card, not a list of picks: no numbers, no percentages, nothing to press.
    const card = sidebar("teams").querySelector("[data-roster-slots]") as HTMLElement;
    expect(within(card).queryAllByRole("button")).toEqual([]);
    expect(card.querySelector("[data-availability]")).toBeNull();
    expect(card.textContent).not.toContain("2");

    // And the dropdown moves to another seat's card: Wembanyama is a centre.
    await user.selectOptions(select, "1");
    expect(slotRows().filter(([, name]) => name !== null)).toEqual([["C", WEMBY.name]]);
  }, 15000);

  it("keeps the room working when the availability read fails", async () => {
    getDraft.mockResolvedValue(draftState());
    availability.mockRejectedValue(
      new ApiError("/draft/availability responded 404", 404, "There is no draft."),
    );
    render(<DraftRoomPage />);
    await screen.findByRole("grid");

    const note = await waitFor(() => {
      const found = document.querySelector('[data-availability-read="unavailable"]');
      if (!found) throw new Error("no notice yet");
      return found;
    });
    expect(note.textContent).toContain("There is no draft.");
    // A note, not the page's error panel — the list is still the list, without percentages.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(listedIn(sidebar()).length).toBe(4);
  });
});

/* --- the seats -------------------------------------------------------------------------- */

describe("the seats panel", () => {
  it("changes my seat while the draft is empty", async () => {
    const user = userEvent.setup();
    updateConfig.mockResolvedValue(draftState({ mySlot: 3 }));
    await openRoom();

    const form = screen.getByRole("form", { name: /seats and names/i });
    await user.clear(within(form).getByLabelText(/my seat/i));
    await user.type(within(form).getByLabelText(/my seat/i), "3");
    await user.type(within(form).getByLabelText("Team 1"), "Sam");
    await user.click(within(form).getByRole("button", { name: /save seats/i }));

    await waitFor(() => expect(updateConfig).toHaveBeenCalledTimes(1));
    expect(updateConfig).toHaveBeenCalledWith({ my_slot: 3, team_names: { "1": "Sam" } });
    // The answer is the board: seat 3 is mine now, with no reset and no lost picks.
    await waitFor(() =>
      expect(document.querySelector('[data-team="3"]')?.textContent).toContain("(You)"),
    );
  }, 15000);

  it("drops the seat input once a pick exists, and renames without it", async () => {
    const user = userEvent.setup();
    await openRoomOn(draftState({ picks: [{ playerId: WEMBY.espn_player_id }] }));

    // Folded away on a running draft: it is a rename now, not a setup step.
    await user.click(screen.getByRole("button", { name: /rename teams/i }));
    const form = screen.getByRole("form", { name: /seats and names/i });

    expect(within(form).queryByLabelText(/my seat/i)).toBeNull();
    expect(form.textContent).toContain("Reconfigure");

    await user.type(within(form).getByLabelText("Team 4"), "Zo");
    await user.click(within(form).getByRole("button", { name: /save seats/i }));

    await waitFor(() => expect(updateConfig).toHaveBeenCalledTimes(1));
    // Names only — the seat is not in the body at all, because it cannot move.
    expect(updateConfig).toHaveBeenCalledWith({ team_names: { "4": "Zo" } });
  }, 15000);
});
