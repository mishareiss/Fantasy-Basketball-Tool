import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TeamView } from "@/components/team/TeamPage";
import {
  ApiError,
  api,
  type FreeAgentRow,
  type FreeAgentsParams,
  type PlanResponse,
  type TeamDayResponse,
  type TeamPlayer,
} from "@/lib/api";
import {
  FREE_AGENTS,
  GOBERT,
  GRANT,
  GREEN,
  WHITMORE,
  acquisitionRow,
  calendar,
  chainedDrop,
  freeAgentsFor,
  isoFor,
  planMove,
  planResponse,
  teamDay,
  warning,
} from "./teamFixtures";

/**
 * The waiver plan on the Team tab (task 31): the Actual | Plan toggle, the plan roster, the
 * acquisitions meter, the Plan tab and the move composer. The api client is mocked wholesale,
 * `ApiError` kept real.
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
      savePlanLineup: vi.fn(),
      autoFillLineup: vi.fn(),
      autoFillPlanLineup: vi.fn(),
      syncLineup: vi.fn(),
      freeAgents: vi.fn(),
      teamPlayer: vi.fn(),
      watchlist: vi.fn(),
      plan: vi.fn(),
      createPlanMove: vi.fn(),
      updatePlanMove: vi.fn(),
      deletePlanMove: vi.fn(),
      setAcquisitionOverride: vi.fn(),
    },
  };
});

const getCalendar = vi.mocked(api.teamCalendar);
const getDay = vi.mocked(api.teamDay);
const saveLineup = vi.mocked(api.saveLineup);
const savePlanLineup = vi.mocked(api.savePlanLineup);
const autoFill = vi.mocked(api.autoFillLineup);
const autoFillPlan = vi.mocked(api.autoFillPlanLineup);
const freeAgents = vi.mocked(api.freeAgents);
const getWatchlist = vi.mocked(api.watchlist);
const getPlan = vi.mocked(api.plan);
const createMove = vi.mocked(api.createPlanMove);
const updateMove = vi.mocked(api.updatePlanMove);
const deleteMove = vi.mocked(api.deletePlanMove);
const setOverride = vi.mocked(api.setAcquisitionOverride);

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

/** The free-agent pool: the three base rows plus the plan's three pickups. */
function agent(ref: typeof GRANT, ppg: number): FreeAgentRow {
  return {
    ...FREE_AGENTS[0],
    id: ref.id,
    name: ref.name,
    stats: { fantasy_ppg: ppg, games: 60 },
  };
}
const POOL: FreeAgentRow[] = [...FREE_AGENTS, agent(GRANT, 31.5), agent(GREEN, 27.25), agent(WHITMORE, 22)];

/** `q` filters by name, as the server does. */
function freeAgentsWith(pool: FreeAgentRow[]) {
  return async (params: FreeAgentsParams = {}) => {
    const q = params.q?.toLowerCase();
    return freeAgentsFor(params, q ? pool.filter((r) => r.name.toLowerCase().includes(q)) : pool);
  };
}

/** A planned pickup in the Plan view's BE row, with a backup and a pending claim. */
function plannedGrant(): TeamPlayer {
  return {
    id: GRANT.id,
    name: GRANT.name,
    nba_team: "POR",
    positions: ["PF"],
    eligible_slots: [3, 11, 12, 13],
    injury_status: null,
    game: null,
    stats: null,
    plan: { status: "planned", move_id: 1, alternatives: [GREEN], claim_pending: true },
  };
}

function planDay(sp = 1, overrides: Partial<TeamDayResponse> = {}): TeamDayResponse {
  const day = teamDay(sp, { view: "plan", source: "from_actual" });
  return {
    ...day,
    slots: day.slots.map((row) => (row.slot === "BE" && row.player ? { ...row, player: plannedGrant() } : row)),
    acquisitions: { ...day.acquisitions, used: 1, planned: 1, used_source: "espn" },
    planned_drops: [{ player: GOBERT, move_id: 1, alternatives: [] }],
    plan_warnings: [
      warning({ code: "acq_limit", message: "Matchup 1 goes over its 7 adds", matchup_id: 1 }),
      warning({ code: "no_game", message: "Jerami Grant has no game Tue 10/20", move_id: 1, severity: "info" }),
    ],
    ...overrides,
  };
}

const MOVE_1 = planMove({ id: 1, sp: 2, pickups: [GRANT, GREEN] });

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("localStorage", memoryStorage());
  getCalendar.mockResolvedValue(calendar());
  getDay.mockImplementation(async ({ sp = 1, basis = "projected", view } = {}) =>
    view === "plan" ? { ...planDay(sp), basis } : teamDay(sp, { basis }),
  );
  freeAgents.mockImplementation(freeAgentsWith(POOL));
  getWatchlist.mockResolvedValue({ season: 2027, entries: [] });
  getPlan.mockResolvedValue(planResponse({ moves: [MOVE_1] }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(props: Partial<React.ComponentProps<typeof TeamView>> = {}) {
  const onUrlChange = vi.fn();
  render(<TeamView initialDay={null} initialBasis="projected" onUrlChange={onUrlChange} {...props} />);
  return { onUrlChange };
}

const roster = () => screen.getByRole("region", { name: "Roster" });
const waivers = () => screen.getByRole("region", { name: "Waiver list" });
const planTab = () => screen.getByRole("region", { name: "Waiver plan" });
const moveRow = (id: number) => planTab().querySelector<HTMLElement>(`li[data-plan-move="${id}"]`)!;

async function ready() {
  await screen.findByRole("button", { name: "Luka Doncic" });
  await within(waivers()).findByRole("button", { name: "Free Agent Guy" });
}

async function openPlanTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: /Plan/ }));
  await within(planTab()).findByText("Waiver plan");
  await waitFor(() => expect(planTab().querySelector("[data-plan-move], [data-plan-empty]")).toBeTruthy());
}

function plain(response: PlanResponse) {
  return { ...response, move_id: null };
}

describe("the Actual | Plan toggle", () => {
  it("defaults to Actual, and Plan round-trips through ?view=plan", async () => {
    const user = userEvent.setup();
    const { onUrlChange } = renderPage();
    await ready();
    expect(getDay).toHaveBeenCalledWith({ sp: 1, basis: "projected" });
    expect(screen.getByRole("button", { name: "Actual view" }).getAttribute("aria-pressed")).toBe("true");
    // The Actual view never reads the plan.
    expect(getPlan).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Plan view" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=1&view=plan");
    await waitFor(() =>
      expect(getDay).toHaveBeenLastCalledWith({ sp: 1, basis: "projected", view: "plan" }),
    );
    expect(within(roster()).queryByRole("button", { name: "Sync lineup from ESPN" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Actual view" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=1");
    expect(within(roster()).getByRole("button", { name: "Sync lineup from ESPN" })).toBeTruthy();
  });

  it("opens on the Plan view from the URL, and day steps keep it", async () => {
    const user = userEvent.setup();
    const { onUrlChange } = renderPage({ initialView: "plan" });
    await ready();
    expect(getDay).toHaveBeenCalledWith({ sp: 1, basis: "projected", view: "plan" });
    await user.click(screen.getByRole("button", { name: "Next day" }));
    expect(onUrlChange).toHaveBeenLastCalledWith("/team?day=2&view=plan");
  });

  it("saves Plan-view moves to the plan's lineup, never the Actual one", async () => {
    savePlanLineup.mockImplementation(async (sp) => ({ ...planDay(sp), days_written: 1 }));
    const user = userEvent.setup();
    renderPage({ initialView: "plan" });
    await ready();
    const reads = getDay.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Move Luka Doncic" }));
    const sg = roster().querySelector<HTMLElement>('tr[data-slot="SG"]')!;
    await user.click(within(sg).getByRole("button", { name: "Here" }));
    expect(savePlanLineup).toHaveBeenCalledWith(1, expect.objectContaining({ through_sp: null }));
    expect(saveLineup).not.toHaveBeenCalled();
    await waitFor(() => expect(getDay.mock.calls.length).toBe(reads + 1));
    expect(getDay).toHaveBeenLastCalledWith({ sp: 1, basis: "projected", view: "plan" });
  });
});

describe("Auto-fill in the Plan view", () => {
  it("fills the plan's lineup, never the Actual one, and re-reads the Plan day", async () => {
    autoFillPlan.mockImplementation(async ({ sp }) => ({
      ...planDay(sp),
      days_written: 1,
      changed: true,
      days: [{ sp, starters_playing: 6, slots: 7, benched_playing: [] }],
    }));
    const user = userEvent.setup();
    renderPage({ initialView: "plan" });
    await ready();
    const reads = getDay.mock.calls.length;

    expect(within(roster()).queryByRole("button", { name: "Auto-fill lineup" })).toBeNull();
    await user.click(within(roster()).getByRole("button", { name: "Auto-fill plan lineup" }));
    expect(autoFillPlan).toHaveBeenCalledWith({ sp: 1, through_sp: null });
    expect(autoFill).not.toHaveBeenCalled();
    expect(savePlanLineup).not.toHaveBeenCalled();
    expect(await screen.findByText("6 of 7 starters playing.")).toBeTruthy();
    await waitFor(() => expect(getDay.mock.calls.length).toBe(reads + 1));
    expect(getDay).toHaveBeenLastCalledWith({ sp: 1, basis: "projected", view: "plan" });
  });
});

describe("the plan roster", () => {
  it("tags a planned pickup in words, with his backup and a pending claim", async () => {
    renderPage({ initialView: "plan" });
    await ready();
    const row = await waitFor(() => {
      const found = roster().querySelector<HTMLElement>('tr[data-planned="true"]');
      expect(found).toBeTruthy();
      return found!;
    });
    expect(row.dataset.slot).toBe("BE");
    expect(within(row).getByText("Jerami Grant")).toBeTruthy();
    // The day comes from the plan's move once it's read.
    expect(await within(row).findByText("Planned · Wed 10/21")).toBeTruthy();
    expect(within(row).getByText("or Draymond Green")).toBeTruthy();
    expect(within(row).getByText("Claim pending")).toBeTruthy();
    // A planned pickup can't be dropped from the roster (he's his move's chain option).
    expect(within(row).queryByRole("button", { name: /Plan a drop/ })).toBeNull();
  });

  it("shows the dropped-today strip and the day's warnings, warning vs info", async () => {
    renderPage({ initialView: "plan" });
    await ready();
    const strip = roster().querySelector<HTMLElement>("[data-dropped-today]")!;
    expect(within(strip).getByText("No Game Center")).toBeTruthy();
    const acq = roster().querySelector<HTMLElement>('[data-plan-warning="acq_limit"]')!;
    expect(acq.dataset.severity).toBe("warning");
    expect(acq.className).toContain("amber");
    expect(acq.textContent).toContain("Warning:");
    const info = roster().querySelector<HTMLElement>('[data-plan-warning="no_game"]')!;
    expect(info.dataset.severity).toBe("info");
    expect(info.className).not.toContain("amber");
    expect(info.textContent).toContain("Note:");
  });

  it("draws neither in the Actual view", async () => {
    renderPage();
    await ready();
    expect(roster().querySelector("[data-dropped-today]")).toBeNull();
    expect(roster().querySelector("[data-plan-warning]")).toBeNull();
    expect(within(roster()).queryByRole("button", { name: /Plan a drop/ })).toBeNull();
  });

  it("names the day a carried lineup comes from, in both views", async () => {
    getDay.mockImplementation(async ({ sp = 1, view } = {}) =>
      view === "plan"
        ? planDay(sp, { lineup_source: "plan_carried", carried_from_sp: 1, carried_from_date: isoFor(1) })
        : teamDay(sp, { source: "carried", carried_from_sp: 1, carried_from_date: isoFor(1) }),
    );
    const user = userEvent.setup();
    renderPage({ initialDay: 3 });
    await ready();
    expect(within(roster()).getByText("Carried from Tue 10/20")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Plan view" }));
    expect(await within(roster()).findByText("Plan: carried from Tue 10/20")).toBeTruthy();
  });
});

describe("the acquisitions meter", () => {
  const meterText = () => roster().querySelector<HTMLElement>("[data-acquisitions]")!;

  it("reads used + planned under the limit, with ESPN as the source", async () => {
    renderPage({ initialView: "plan" });
    await ready();
    await waitFor(() => expect(meterText().textContent).toBe("Acquisitions 1 used + 1 planned · 2/7"));
    expect(meterText().dataset.over).toBeUndefined();
    expect(roster().querySelector("[data-acquisitions-source]")!.textContent).toBe("(used: ESPN)");
  });

  it("goes red and says so when over, and names an override", async () => {
    getDay.mockImplementation(async ({ sp = 1 } = {}) =>
      planDay(sp, {
        acquisitions: { used: 5, limit: 7, matchup_id: 1, planned: 3, used_source: "override" },
      }),
    );
    renderPage({ initialView: "plan" });
    await ready();
    await waitFor(() => expect(meterText().textContent).toBe("Acquisitions 5 used + 3 planned · 8/7"));
    expect(meterText().dataset.over).toBe("true");
    expect(within(roster()).getByText("over limit")).toBeTruthy();
    expect(roster().querySelector("[data-acquisitions-source]")!.textContent).toBe(
      "(used: your override)",
    );
  });
});

describe("the Plan tab", () => {
  const MOVES = [
    planMove({ id: 1, sp: 2, sequence: 0, pickups: [GRANT, GREEN] }),
    planMove({
      id: 2,
      sp: 2,
      sequence: 1,
      pickups: [WHITMORE],
      drop: chainedDrop(1, "Jerami Grant (or Draymond Green)", [GREEN]),
    }),
    planMove({ id: 5, sp: 8, pickups: [GREEN], overdue: true }),
  ];

  beforeEach(() => {
    getPlan.mockResolvedValue(
      planResponse({
        moves: MOVES,
        acquisitions: [acquisitionRow(1, { planned: 2 }), acquisitionRow(2, { used: 6, planned: 2, over: true })],
        warnings: [
          warning({ code: "acq_limit", message: "Matchup 2 is over its limit", matchup_id: 2 }),
          warning({ code: "on_waivers", message: "Grant is on waivers until Thu", move_id: 1 }),
        ],
      }),
    );
  });

  it("groups by matchup with each meter, and orders moves by day then sequence", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);

    const sections = [...planTab().querySelectorAll<HTMLElement>("[data-plan-matchup]")];
    expect(sections.map((s) => s.dataset.planMatchup)).toEqual(["1", "2"]);
    expect([...sections[0].querySelectorAll<HTMLElement>("li[data-plan-move]")].map((li) => li.dataset.planMove)).toEqual(["1", "2"]);
    expect(within(sections[0]).getByText("Acquisitions 0 used + 2 planned · 2/7")).toBeTruthy();
    const over = within(sections[1]).getByText("Acquisitions 6 used + 2 planned · 8/7");
    expect(over.dataset.over).toBe("true");
    // The plan-level warning sits at the top; the move's own sits under its row.
    const general = planTab().querySelector<HTMLElement>("[data-plan-general-warnings]")!;
    expect(within(general).getByText(/Matchup 2 is over its limit/)).toBeTruthy();
    expect(within(moveRow(1)).getByText(/Grant is on waivers until Thu/)).toBeTruthy();
  });

  it("reads named and chained drops, and the status chips", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);

    const sentence = (id: number) => moveRow(id).querySelector("[data-move-sentence]")!.textContent;
    expect(sentence(1)).toBe("Wed 10/21 · Drop No Game Center → Add Jerami Grant (backups: Draymond Green)");
    expect(sentence(2)).toBe(
      "Wed 10/21 · Drop move 1's pickup: Jerami Grant (or Draymond Green) → Add Cam Whitmore",
    );
    expect(within(moveRow(1)).getByText("Planned")).toBeTruthy();
    expect(within(moveRow(5)).getByText("Overdue")).toBeTruthy();
  });

  it("shows a done move with the backup it got, and the chain settled on him", async () => {
    getPlan.mockResolvedValue(
      planResponse({
        moves: [
          { ...MOVES[0], status: "done", resolved_player_id: GREEN.id, lands_sp: null },
          { ...MOVES[1], drop: chainedDrop(1, "Draymond Green") },
        ],
        warnings: [
          warning({ code: "different_drop", severity: "info", message: "ESPN dropped someone else", move_id: 1 }),
        ],
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    expect(within(moveRow(1)).getByText("Done ✓ · got Draymond Green")).toBeTruthy();
    expect(within(moveRow(1)).getByText(/ESPN dropped someone else/)).toBeTruthy();
    expect(moveRow(2).querySelector("[data-move-sentence]")!.textContent).toContain(
      "Drop move 1's pickup: Draymond Green →",
    );
  });

  it("shows a broken chain's problem", async () => {
    getPlan.mockResolvedValue(
      planResponse({ moves: [{ ...MOVES[1], drop: { ...chainedDrop(1, "?"), problem: "missing" } }] }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    expect(within(moveRow(2)).getByText("Broken chain: that move no longer exists.")).toBeTruthy();
  });

  it("reorders within the day by sending the new sequences, then re-reads", async () => {
    updateMove.mockResolvedValue({ ...planResponse(), move_id: 2 });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    expect((within(moveRow(1)).getByRole("button", { name: "Move 1 earlier" }) as HTMLButtonElement).disabled).toBe(true);
    const reads = getPlan.mock.calls.length;

    await user.click(within(moveRow(2)).getByRole("button", { name: "Move 2 earlier" }));
    expect(updateMove.mock.calls).toEqual([
      [2, { sequence: 0 }],
      [1, { sequence: 1 }],
    ]);
    await waitFor(() => expect(getPlan.mock.calls.length).toBe(reads + 1));
  });

  it("marks a move done with the candidate chosen inline", async () => {
    updateMove.mockResolvedValue({ ...planResponse(), move_id: 1 });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    await user.click(within(moveRow(1)).getByRole("button", { name: "Mark done" }));
    const picker = moveRow(1).querySelector<HTMLElement>("[data-mark-done]")!;
    await user.click(within(picker).getByRole("button", { name: "Draymond Green (backup)" }));
    expect(updateMove).toHaveBeenCalledWith(1, { status: "done", resolved_player_id: GREEN.id });
  });

  it("skips and restores", async () => {
    updateMove.mockResolvedValue({ ...planResponse(), move_id: 1 });
    getPlan.mockResolvedValue(
      planResponse({ moves: [MOVES[0], { ...MOVES[2], status: "skipped", lands_sp: null, overdue: false }] }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    await user.click(within(moveRow(1)).getByRole("button", { name: "Skip" }));
    expect(updateMove).toHaveBeenLastCalledWith(1, { status: "skipped" });
    await waitFor(() => expect(within(moveRow(5)).getByRole("button", { name: "Restore to planned" })).toBeTruthy());
    await user.click(within(moveRow(5)).getByRole("button", { name: "Restore to planned" }));
    expect(updateMove).toHaveBeenLastCalledWith(5, { status: "planned", resolved_player_id: null });
  });

  it("asks inline before repointing dependents on a delete, then deletes with repoint", async () => {
    deleteMove.mockImplementation(async (id, repoint) => {
      if (!repoint) {
        throw new ApiError("/team/plan/moves/1 responded 409", 409, "Move 1 is the drop of move(s) 2", undefined, {
          message: "Move 1 is the drop of move(s) 2",
          move_id: 1,
          dependent_move_ids: [2],
        });
      }
      return { ...planResponse(), deleted_move_id: id, repointed: [2] };
    });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);

    await user.click(within(moveRow(1)).getByRole("button", { name: "Delete" }));
    expect(deleteMove).toHaveBeenLastCalledWith(1, false);
    const prompt = await waitFor(() => {
      const found = moveRow(1).querySelector<HTMLElement>("[data-delete-prompt]");
      expect(found).toBeTruthy();
      return found!;
    });
    expect(prompt.textContent).toContain(
      "Move 2 drops this move’s pickup. Repoint it to a named drop and delete?",
    );
    await user.click(within(prompt).getByRole("button", { name: "Repoint and delete" }));
    expect(deleteMove).toHaveBeenLastCalledWith(1, true);
  });

  it("deletes a move nobody depends on straight away", async () => {
    deleteMove.mockResolvedValue({ ...planResponse(), deleted_move_id: 5, repointed: [] });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    const reads = getPlan.mock.calls.length;
    await user.click(within(moveRow(5)).getByRole("button", { name: "Delete" }));
    expect(deleteMove).toHaveBeenCalledWith(5, false);
    await waitFor(() => expect(getPlan.mock.calls.length).toBe(reads + 1));
  });

  it("sets and clears a matchup's acquisitions override", async () => {
    setOverride.mockResolvedValue(plain(planResponse()));
    getPlan.mockResolvedValue(
      planResponse({
        moves: MOVES,
        acquisitions: [acquisitionRow(1, { planned: 2 }), acquisitionRow(2, { used: 3, used_source: "override" })],
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);

    const first = planTab().querySelector<HTMLElement>('[data-acquisition-row="1"]')!;
    expect(within(first).queryByRole("button", { name: "Clear override" })).toBeNull();
    await user.click(within(first).getByRole("button", { name: "Override used…" }));
    expect(within(first).getByText(/replaces ESPN’s count/)).toBeTruthy();
    const input = within(first).getByRole("spinbutton", { name: "Adds used in Matchup 1" });
    await user.clear(input);
    await user.type(input, "4");
    await user.click(within(first).getByRole("button", { name: "Set override" }));
    expect(setOverride).toHaveBeenCalledWith(1, 4);

    const second = planTab().querySelector<HTMLElement>('[data-acquisition-row="2"]')!;
    expect(within(second).getByText("(used: your override)")).toBeTruthy();
    await user.click(within(second).getByRole("button", { name: "Clear override" }));
    expect(setOverride).toHaveBeenLastCalledWith(2, null);
  });

  it("remembers the tab per browser", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    expect(window.localStorage.getItem("team.rightPanel")).toBe("plan");
  });
});

describe("the move composer", () => {
  const dialog = () => screen.getByRole("dialog");
  const pickups = () =>
    [...dialog().querySelectorAll<HTMLElement>("li[data-pickup]")].map(
      (li) => li.querySelector(".truncate")!.textContent,
    );

  async function dropOptionsReady() {
    await within(dialog()).findByRole("radiogroup", { name: "Drop" });
  }

  it("opens from + Plan with that player as the primary, on the waiver panel's first day", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: /^Thu\s*10\/22$/ }));
    await user.click(within(waivers()).getByRole("button", { name: /^Tue\s*10\/20$/ }));
    await user.click(within(waivers()).getByRole("button", { name: "Plan a pickup of Jerami Grant" }));

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect((within(dialog()).getByRole("combobox", { name: "Move day" }) as HTMLSelectElement).value).toBe("3");
    expect(pickups()).toEqual(["Jerami Grant"]);
    expect(within(dialog()).getByText("Primary")).toBeTruthy();
    // His game and FPTS/G on the chosen day.
    await waitFor(() =>
      expect(dialog().querySelector('li[data-pickup="201"] [data-pickup-fpts]')!.textContent).toBe("31.5"),
    );
    expect(getDay).toHaveBeenCalledWith({ sp: 3, view: "plan" });
  });

  it("opens from Drop… in the Plan view with that drop, on the roster's day", async () => {
    const user = userEvent.setup();
    renderPage({ initialView: "plan", initialDay: 4 });
    await ready();
    await user.click(await within(roster()).findByRole("button", { name: "Plan a drop of Luka Doncic" }));
    await dropOptionsReady();
    expect((within(dialog()).getByRole("combobox", { name: "Move day" }) as HTMLSelectElement).value).toBe("4");
    expect((within(dialog()).getByRole("radio", { name: /Luka Doncic/ }) as HTMLInputElement).checked).toBe(true);
    expect(pickups()).toEqual([]);
  });

  it("opens empty from Add move, and prefilled from Edit", async () => {
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    await user.click(within(planTab()).getByRole("button", { name: "Add move" }));
    await dropOptionsReady();
    expect(pickups()).toEqual([]);
    expect(within(dialog()).queryByRole("radio", { checked: true })).toBeNull();
    await user.click(within(dialog()).getByRole("button", { name: "Cancel" }));

    await user.click(within(moveRow(1)).getByRole("button", { name: "Edit" }));
    await dropOptionsReady();
    expect(within(dialog()).getByRole("heading", { name: "Edit move 1" })).toBeTruthy();
    expect((within(dialog()).getByRole("combobox", { name: "Move day" }) as HTMLSelectElement).value).toBe("2");
    expect(pickups()).toEqual(["Jerami Grant", "Draymond Green"]);
    expect((within(dialog()).getByRole("radio", { name: /No Game Center/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("offers chain options only for moves landing on or before the day", async () => {
    getPlan.mockResolvedValue(
      planResponse({ moves: [MOVE_1, planMove({ id: 2, sp: 5, pickups: [WHITMORE] })] }),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    await user.click(within(planTab()).getByRole("button", { name: "Add move" }));
    await user.selectOptions(within(dialog()).getByRole("combobox", { name: "Move day" }), "4");
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 4, view: "plan" }));
    await dropOptionsReady();
    expect(within(dialog()).getByRole("radio", { name: /Pickup from move 1: Jerami Grant \(or Draymond Green\)/ })).toBeTruthy();
    expect(within(dialog()).queryByRole("radio", { name: /Pickup from move 2/ })).toBeNull();

    await user.selectOptions(within(dialog()).getByRole("combobox", { name: "Move day" }), "5");
    expect(await within(dialog()).findByRole("radio", { name: /Pickup from move 2: Cam Whitmore/ })).toBeTruthy();
  });

  it("adds pickups by search and from the watchlist, reorders and removes them", async () => {
    getWatchlist.mockResolvedValue({
      season: 2027,
      entries: [
        { player_id: GREEN.id, name: GREEN.name, nba_team: "GSW", positions: ["PF"], injury_status: null, owner: "FREEAGENT", added_at: "2026-10-15T00:00:00Z", note: null },
        { player_id: 1, name: "Luka Doncic", nba_team: "LAL", positions: ["PG"], injury_status: null, owner: { team_id: 8, name: "Emzep" }, added_at: "2026-10-15T00:00:00Z", note: null },
      ],
    });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: "Plan a pickup of Jerami Grant" }));
    await screen.findByRole("dialog");

    await user.type(within(dialog()).getByRole("searchbox", { name: "Search free agents" }), "whit");
    await user.click(await within(dialog()).findByRole("button", { name: "Add Cam Whitmore" }));
    // A rostered watchlist player isn't offered.
    expect(within(dialog()).queryByRole("button", { name: "Add Luka Doncic from watchlist" })).toBeNull();
    await user.click(await within(dialog()).findByRole("button", { name: "Add Draymond Green from watchlist" }));
    expect(pickups()).toEqual(["Jerami Grant", "Cam Whitmore", "Draymond Green"]);

    await user.click(within(dialog()).getByRole("button", { name: "Move Draymond Green up" }));
    expect(pickups()).toEqual(["Jerami Grant", "Draymond Green", "Cam Whitmore"]);
    await user.click(within(dialog()).getByRole("button", { name: "Remove Jerami Grant" }));
    expect(pickups()).toEqual(["Draymond Green", "Cam Whitmore"]);
    expect(within(dialog()).getByText("Primary")).toBeTruthy();
  });

  it("POSTs the exact body, then closes and re-reads the plan, the day and the waiver list", async () => {
    createMove.mockResolvedValue({
      ...planResponse({
        moves: [MOVE_1, planMove({ id: 7, sp: 1, pickups: [GRANT, GREEN] })],
        warnings: [
          warning({ code: "on_waivers", message: "Grant clears Thu", move_id: 7 }),
          warning({ code: "acq_limit", message: "not this move", matchup_id: 1 }),
        ],
      }),
      move_id: 7,
    });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: "Plan a pickup of Jerami Grant" }));
    await dropOptionsReady();
    await user.click(within(dialog()).getByRole("radio", { name: /No Game Center/ }));
    await user.type(within(dialog()).getByRole("searchbox", { name: "Search free agents" }), "draymond");
    await user.click(await within(dialog()).findByRole("button", { name: "Add Draymond Green" }));

    const plans = getPlan.mock.calls.length;
    const days = getDay.mock.calls.length;
    const lists = freeAgents.mock.calls.length;
    await user.click(within(dialog()).getByRole("button", { name: "Save move" }));
    expect(createMove).toHaveBeenCalledWith({
      sp: 1,
      drop_player_id: 3,
      candidate_ids: [GRANT.id, GREEN.id],
      note: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(getPlan.mock.calls.length).toBeGreaterThan(plans));
    await waitFor(() => expect(getDay.mock.calls.length).toBeGreaterThan(days));
    await waitFor(() => expect(freeAgents.mock.calls.length).toBeGreaterThan(lists));

    // The move's own warnings, as an inline note on the Plan tab.
    await user.click(screen.getByRole("tab", { name: /Plan/ }));
    const notice = planTab().querySelector<HTMLElement>("[data-plan-notice]")!;
    expect(within(notice).getByText("Saved move 1.")).toBeTruthy();
    expect(within(notice).getByText(/Grant clears Thu/)).toBeTruthy();
    expect(within(notice).queryByText(/not this move/)).toBeNull();
  });

  it("POSTs a chained drop as drop_from_move_id", async () => {
    createMove.mockResolvedValue({ ...planResponse(), move_id: 9 });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    await user.click(within(planTab()).getByRole("button", { name: "Add move" }));
    await user.selectOptions(within(dialog()).getByRole("combobox", { name: "Move day" }), "4");
    await waitFor(() => expect(getDay).toHaveBeenLastCalledWith({ sp: 4, view: "plan" }));
    await user.click(await within(dialog()).findByRole("radio", { name: /Pickup from move 1/ }));
    await user.type(within(dialog()).getByRole("searchbox", { name: "Search free agents" }), "whit");
    await user.click(await within(dialog()).findByRole("button", { name: "Add Cam Whitmore" }));
    await user.type(within(dialog()).getByRole("textbox", { name: "Move note" }), "if Grant clears");
    await user.click(within(dialog()).getByRole("button", { name: "Save move" }));
    expect(createMove).toHaveBeenCalledWith({
      sp: 4,
      drop_from_move_id: 1,
      candidate_ids: [WHITMORE.id],
      note: "if Grant clears",
    });
  });

  it("PATCHes on edit", async () => {
    updateMove.mockResolvedValue({ ...planResponse({ moves: [MOVE_1] }), move_id: 1 });
    const user = userEvent.setup();
    renderPage();
    await ready();
    await openPlanTab(user);
    await user.click(within(moveRow(1)).getByRole("button", { name: "Edit" }));
    await dropOptionsReady();
    await user.click(within(dialog()).getByRole("button", { name: "Move Draymond Green up" }));
    await user.click(within(dialog()).getByRole("button", { name: "Save changes" }));
    expect(updateMove).toHaveBeenCalledWith(1, {
      sp: 2,
      drop_player_id: 3,
      candidate_ids: [GREEN.id, GRANT.id],
      note: null,
    });
    expect(createMove).not.toHaveBeenCalled();
  });

  it("shows a 422's reasons inline and stays open", async () => {
    createMove.mockRejectedValue(
      new ApiError("/team/plan/moves responded 422", 422, undefined, [
        "candidate_ids lists 201 more than once",
      ]),
    );
    const user = userEvent.setup();
    renderPage();
    await ready();
    await user.click(within(waivers()).getByRole("button", { name: "Plan a pickup of Jerami Grant" }));
    await dropOptionsReady();
    // Nothing chosen to drop: said here, without a request.
    await user.click(within(dialog()).getByRole("button", { name: "Save move" }));
    expect(within(dialog()).getByText("Choose who to drop.")).toBeTruthy();
    expect(createMove).not.toHaveBeenCalled();

    await user.click(within(dialog()).getByRole("radio", { name: /Luka Doncic/ }));
    await user.click(within(dialog()).getByRole("button", { name: "Save move" }));
    expect(await within(dialog()).findByText("candidate_ids lists 201 more than once")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("the waiver panel's plan bits", () => {
  it("badges a row that is in the plan", async () => {
    freeAgents.mockImplementation(
      freeAgentsWith(
        POOL.map((row) =>
          row.id === GRANT.id
            ? { ...row, in_plan: [{ move_id: 1, sp: 2, rank: 1 }] }
            : row.id === GREEN.id
              ? { ...row, in_plan: [{ move_id: 1, sp: 2, rank: 2 }] }
              : row,
        ),
      ),
    );
    renderPage();
    await ready();
    const row = (id: number) => waivers().querySelector<HTMLElement>(`tr[data-waiver-player="${id}"]`)!;
    expect(within(row(GRANT.id)).getByText("In plan · Wed")).toBeTruthy();
    expect(within(row(GREEN.id)).getByText("In plan · Wed (backup)")).toBeTruthy();
    expect(row(101).querySelector("[data-in-plan]")).toBeNull();
    expect(within(row(101)).getByRole("button", { name: "Plan a pickup of Free Agent Guy" })).toBeTruthy();
  });
});
