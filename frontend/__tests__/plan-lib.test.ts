import { describe, expect, it } from "vitest";

import {
  addCandidate,
  composerPrefill,
  createBody,
  draftProblems,
  dropOptions,
  dropText,
  groupMovesByMatchup,
  inPlanLabel,
  meter,
  moveCandidate,
  moveNumbers,
  moveSentence,
  patchBody,
  removeCandidate,
  reorderWithinDay,
  splitWarnings,
  statusChip,
} from "@/lib/plan";
import { teamHref } from "@/lib/team";
import {
  CALENDAR_DAYS,
  GOBERT,
  GRANT,
  GREEN,
  MATCHUPS,
  WHITMORE,
  acquisitionRow,
  chainedDrop,
  planMove,
  ref,
  teamDay,
  warning,
} from "./teamFixtures";

/**
 * The waiver plan's rules (lib/plan.ts). Days: sp 1 = Tue 10/20, sp 2 = Wed 10/21, sp 4 =
 * Fri 10/23; Matchup 1 is sp 1–6, Matchup 2 is 7–13.
 */

describe("grouping and order", () => {
  it("groups by matchup in calendar order, moves by day then sequence then id", () => {
    const moves = [
      planMove({ id: 9, sp: 8 }),
      planMove({ id: 3, sp: 4, sequence: 1 }),
      planMove({ id: 5, sp: 4, sequence: 0 }),
      planMove({ id: 1, sp: 2 }),
      planMove({ id: 2, sp: 4, sequence: 1 }),
    ];
    const groups = groupMovesByMatchup(moves, [acquisitionRow(2), acquisitionRow(1)], MATCHUPS);
    expect(groups.map((g) => g.matchupId)).toEqual([1, 2]);
    expect(groups[0].moves.map((m) => m.id)).toEqual([1, 5, 2, 3]);
    expect(groups[0].acquisition?.matchup_id).toBe(1);
    expect(groups[1].moves.map((m) => m.id)).toEqual([9]);
  });

  it("keeps a matchup with an acquisition row and no moves, and numbers moves in plan order", () => {
    const moves = [planMove({ id: 40, sp: 4 }), planMove({ id: 7, sp: 2 })];
    const groups = groupMovesByMatchup(moves, [acquisitionRow(1), acquisitionRow(2)], MATCHUPS);
    expect(groups[1]).toMatchObject({ matchupId: 2, moves: [] });
    expect([...moveNumbers(moves)]).toEqual([
      [7, 1],
      [40, 2],
    ]);
  });
});

describe("the move sentence", () => {
  const first = planMove({ id: 11, sp: 2, pickups: [GRANT, GREEN] });
  const numbers = moveNumbers([first, planMove({ id: 12, sp: 4 })]);

  it("reads a named drop with backups", () => {
    expect(moveSentence(first, numbers)).toBe(
      "Wed 10/21 · Drop No Game Center → Add Jerami Grant (backups: Draymond Green)",
    );
  });

  it("reads a chained drop unresolved, then resolved", () => {
    const chained = planMove({
      id: 12,
      sp: 4,
      pickups: [WHITMORE],
      drop: chainedDrop(11, "Jerami Grant (or Draymond Green)", [GREEN]),
    });
    expect(moveSentence(chained, numbers)).toBe(
      "Fri 10/23 · Drop move 1's pickup: Jerami Grant (or Draymond Green) → Add Cam Whitmore",
    );
    const settled = { ...chained, drop: chainedDrop(11, "Draymond Green") };
    expect(dropText(settled, numbers)).toBe("Drop move 1's pickup: Draymond Green");
  });

  it("leaves the name off a broken chain", () => {
    const broken = planMove({
      id: 12,
      sp: 4,
      drop: { ...chainedDrop(11, "Jerami Grant"), problem: "skipped" },
    });
    expect(dropText(broken, numbers)).toBe("Drop move 1's pickup");
    expect(dropText({ ...broken, drop: { ...broken.drop, from_move_id: 99 } }, numbers)).toBe(
      "Drop a deleted move's pickup",
    );
  });
});

describe("the status chip", () => {
  it("picks Planned, Overdue, Claim pending, Skipped and Done", () => {
    expect(statusChip(planMove({ id: 1, sp: 2 })).label).toBe("Planned");
    expect(statusChip(planMove({ id: 1, sp: 2, overdue: true })).label).toBe("Overdue");
    expect(statusChip(planMove({ id: 1, sp: 2, overdue: true, claim_pending: true })).label).toBe(
      "Claim pending",
    );
    expect(statusChip(planMove({ id: 1, sp: 2, status: "skipped" })).label).toBe("Skipped");
    expect(
      statusChip(planMove({ id: 1, sp: 2, status: "done", resolved_player_id: GRANT.id, pickups: [GRANT, GREEN] }))
        .label,
    ).toBe("Done ✓");
  });

  it("names the backup a done move actually got", () => {
    const done = planMove({
      id: 1,
      sp: 2,
      status: "done",
      resolved_player_id: GREEN.id,
      pickups: [GRANT, GREEN],
    });
    expect(statusChip(done)).toMatchObject({ kind: "done", label: "Done ✓ · got Draymond Green" });
  });
});

describe("warnings", () => {
  it("splits per-move warnings from the plan-level ones", () => {
    const { byMove, general } = splitWarnings([
      warning({ code: "acq_limit", message: "over", matchup_id: 1 }),
      warning({ code: "no_game", message: "no game", move_id: 4, severity: "info" }),
      warning({ code: "on_waivers", message: "waivers", move_id: 4 }),
    ]);
    expect(general.map((w) => w.code)).toEqual(["acq_limit"]);
    expect(byMove.get(4)?.map((w) => w.code)).toEqual(["no_game", "on_waivers"]);
  });
});

describe("reorder within a day", () => {
  const moves = [
    planMove({ id: 1, sp: 2, sequence: 0 }),
    planMove({ id: 2, sp: 2, sequence: 1 }),
    planMove({ id: 3, sp: 2, sequence: 2 }),
    planMove({ id: 4, sp: 4, sequence: 0 }),
  ];

  it("swaps with the neighbour and sends only the sequences that change", () => {
    expect(reorderWithinDay(moves, 3, -1)).toEqual([
      { id: 3, sequence: 1 },
      { id: 2, sequence: 2 },
    ]);
    expect(reorderWithinDay(moves, 1, 1)).toEqual([
      { id: 2, sequence: 0 },
      { id: 1, sequence: 1 },
    ]);
  });

  it("is a no-op at either end and never crosses days", () => {
    expect(reorderWithinDay(moves, 1, -1)).toEqual([]);
    expect(reorderWithinDay(moves, 3, 1)).toEqual([]);
    expect(reorderWithinDay(moves, 4, -1)).toEqual([]);
  });

  it("renumbers a day whose sequences collide", () => {
    const tied = [planMove({ id: 1, sp: 2, sequence: 5 }), planMove({ id: 2, sp: 2, sequence: 5 })];
    expect(reorderWithinDay(tied, 2, -1)).toEqual([
      { id: 2, sequence: 0 },
      { id: 1, sequence: 1 },
    ]);
  });
});

describe("the meter", () => {
  it("reads used + planned against the limit, red when over", () => {
    expect(meter({ used: 1, planned: 3, limit: 7 })).toMatchObject({
      text: "1 used + 3 planned · 4/7",
      over: false,
      sourceLabel: "used: ESPN",
    });
    expect(meter({ used: 5, planned: 3, limit: 7, used_source: "override" })).toMatchObject({
      text: "5 used + 3 planned · 8/7",
      over: true,
      source: "override",
      sourceLabel: "used: your override",
    });
  });

  it("keeps the old short text in the Actual view while nothing is planned", () => {
    expect(meter({ used: 2, limit: 7 }, true).text).toBe("2 / 7");
    expect(meter({ used: 2, planned: 0, limit: 7 }).text).toBe("2 used + 0 planned · 2/7");
    expect(meter({ used: 2, planned: 1, limit: null }, true).text).toBe("2 used + 1 planned · 3/—");
  });

  it("trusts the server's over flag when it sends one", () => {
    expect(meter({ used: 1, planned: 1, limit: 7, over: true }).over).toBe(true);
  });
});

describe("the composer's drop options", () => {
  const planDay = teamDay(4, {
    view: "plan",
    slots: teamDay(4).slots.map((row) =>
      row.slot === "BE" && row.player
        ? { ...row, player: { ...row.player, id: GRANT.id, name: GRANT.name, plan: { status: "planned", move_id: 1, alternatives: [], claim_pending: false } } }
        : row,
    ),
  });

  it("lists the real plan roster, then the chain options landing on or before the day", () => {
    const moves = [
      planMove({ id: 1, sp: 2, pickups: [GRANT, GREEN] }),
      planMove({ id: 2, sp: 4, pickups: [WHITMORE] }),
      planMove({ id: 3, sp: 5 }),
      planMove({ id: 4, sp: 3, status: "skipped" }),
    ];
    const options = dropOptions(planDay, moves, 4);
    expect(options.filter((o) => o.choice.kind === "player").map((o) => o.label)).toEqual([
      "Luka Doncic",
      "LeBron James",
      "No Game Center",
      "Utility Guard",
    ]);
    expect(options.filter((o) => o.choice.kind === "move").map((o) => o.label)).toEqual([
      "Pickup from move 1: Jerami Grant (or Draymond Green)",
      "Pickup from move 3: Cam Whitmore",
    ]);
    // Moves are numbered in plan order: id 4 (sp 3) is move 2, id 2 (sp 4) is move 3.
    expect(options.find((o) => o.key === "move:3")).toBeUndefined();
    expect(options.find((o) => o.key === "move:4")).toBeUndefined();
  });

  it("only offers chains that land by the day, never the move being edited or a taken pickup", () => {
    const moves = [
      planMove({ id: 1, sp: 2 }),
      planMove({ id: 2, sp: 4, drop: chainedDrop(1, "Jerami Grant") }),
      planMove({ id: 3, sp: 3 }),
    ];
    expect(dropOptions(planDay, moves, 2).map((o) => o.key)).not.toContain("move:3");
    expect(dropOptions(planDay, moves, 4).map((o) => o.key)).not.toContain("move:1");
    expect(dropOptions(planDay, moves, 4, 2).map((o) => o.key)).toContain("move:1");
    expect(dropOptions(planDay, moves, 4, 3).map((o) => o.key)).not.toContain("move:3");
    // An overdue move lands today, whatever day it was planned for.
    const overdue = [planMove({ id: 5, sp: 9, lands_sp: 1, overdue: true })];
    expect(dropOptions(planDay, overdue, 2).map((o) => o.key)).toContain("move:5");
  });
});

describe("the pickup list", () => {
  it("adds without duplicates, reorders, and removes", () => {
    let list = addCandidate([], GRANT);
    list = addCandidate(list, GREEN);
    list = addCandidate(list, GRANT);
    expect(list.map((p) => p.id)).toEqual([GRANT.id, GREEN.id]);
    list = moveCandidate(list, 1, -1);
    expect(list.map((p) => p.id)).toEqual([GREEN.id, GRANT.id]);
    expect(moveCandidate(list, 0, -1).map((p) => p.id)).toEqual([GREEN.id, GRANT.id]);
    expect(removeCandidate(list, 0).map((p) => p.id)).toEqual([GRANT.id]);
  });
});

describe("composer prefill", () => {
  const context = { waiverDay: 3, today: 1 };

  it("prefills each entry point", () => {
    expect(composerPrefill({ kind: "waiver", player: GRANT }, context)).toEqual({
      moveId: null,
      sp: 3,
      drop: null,
      pickups: [GRANT],
      note: "",
    });
    expect(
      composerPrefill({ kind: "roster-drop", player: { id: 3, name: "No Game Center" }, sp: 5 }, context),
    ).toEqual({
      moveId: null,
      sp: 5,
      drop: { kind: "player", id: 3, name: "No Game Center" },
      pickups: [],
      note: "",
    });
    expect(composerPrefill({ kind: "add" }, context).sp).toBe(3);
    expect(composerPrefill({ kind: "add" }, { waiverDay: null, today: 1 }).sp).toBe(1);
  });

  it("prefills an edit from the move, chained drops included", () => {
    const move = planMove({
      id: 12,
      sp: 4,
      pickups: [WHITMORE, GREEN],
      drop: chainedDrop(11, "Jerami Grant"),
      note: "if Grant clears",
    });
    expect(composerPrefill({ kind: "edit", move }, context)).toEqual({
      moveId: 12,
      sp: 4,
      drop: { kind: "move", moveId: 11 },
      pickups: [WHITMORE, GREEN],
      note: "if Grant clears",
    });
  });
});

describe("the request bodies", () => {
  it("sends exactly one drop reference and the candidates in order", () => {
    const named = { moveId: null, sp: 2, drop: { kind: "player" as const, id: 3, name: "x" }, pickups: [GRANT, GREEN], note: " " };
    expect(createBody(named)).toEqual({ sp: 2, drop_player_id: 3, candidate_ids: [201, 202], note: null });
    const chained = { ...named, drop: { kind: "move" as const, moveId: 11 }, note: "hi" };
    expect(patchBody(chained)).toEqual({ sp: 2, drop_from_move_id: 11, candidate_ids: [201, 202], note: "hi" });
  });

  it("names what's missing before a save", () => {
    expect(draftProblems({ moveId: null, sp: 1, drop: null, pickups: [], note: "" })).toEqual([
      "Choose who to drop.",
      "Add at least one pickup.",
    ]);
  });
});

describe("the waiver list's In plan badge", () => {
  it("names each planned day, marking a backup", () => {
    const calendar = { days: CALENDAR_DAYS };
    expect(inPlanLabel({ in_plan: [] }, calendar)).toBeNull();
    expect(inPlanLabel({}, calendar)).toBeNull();
    expect(
      inPlanLabel({ in_plan: [{ move_id: 2, sp: 4, rank: 2 }, { move_id: 1, sp: 2, rank: 1 }] }, calendar),
    ).toBe("In plan · Wed, Fri (backup)");
  });
});

describe("teamHref", () => {
  it("carries ?view=plan and leaves the default Actual out", () => {
    expect(teamHref(2, "projected", "plan")).toBe("/team?day=2&view=plan");
    expect(teamHref(2, "l7", "actual")).toBe("/team?day=2&basis=l7");
    expect(teamHref(null, "projected")).toBe("/team");
  });
});

// Keep the fixture helpers honest.
it("builds a ref", () => {
  expect(ref(1, "A").name).toBe("A");
  expect(GOBERT.id).toBe(3);
});
