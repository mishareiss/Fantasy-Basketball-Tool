import { describe, expect, it } from "vitest";

import {
  BENCH_SLOT,
  buildMove,
  canMoveTo,
  canPlay,
  clampDay,
  currentAssignments,
  dayLabel,
  defaultSyncWindow,
  groupDaysByMatchup,
  injuryBadge,
  legalTargets,
  lineupSourceLabel,
  matchupRange,
  syncRange,
  teamHref,
  toggleDay,
  waiverBadge,
} from "@/lib/team";
import { CALENDAR_DAYS, MATCHUPS, calendar, slots } from "./teamFixtures";

/**
 * The Team tab's rules. The grid (teamFixtures.slots) is, by row:
 * 0 PG Luka (PG/SG/G/UT) · 1 SG empty · 2 SF LeBron (SF/PF/F/UT) · 3 PF empty ·
 * 4 C center (C/UT) · 5 UT guard (PG/UT) · 6 UT empty · 7 BE wing (SG/SF/UT) · 8 BE empty ·
 * 9 IR empty. Everyone has BE and IR in eligible_slots, as ESPN gives them.
 */

describe("slot legality", () => {
  it("lets a player into an empty slot he is eligible for", () => {
    expect(canMoveTo(slots(), 0, 1)).toBe(true); // Luka -> SG
    expect(canMoveTo(slots(), 2, 3)).toBe(true); // LeBron -> PF
  });

  it("refuses an empty slot he isn't eligible for", () => {
    expect(canMoveTo(slots(), 4, 1)).toBe(false); // center -> SG
    expect(canMoveTo(slots(), 0, 3)).toBe(false); // Luka -> PF
  });

  it("always allows the bench", () => {
    expect(canPlay([], BENCH_SLOT)).toBe(true);
    for (const from of [0, 2, 4, 5]) expect(canMoveTo(slots(), from, 8)).toBe(true);
  });

  it("allows a swap only when BOTH players fit the other's slot", () => {
    // Luka (PG) <-> guard (UT): Luka can play UT, the guard can play PG.
    expect(canMoveTo(slots(), 0, 5)).toBe(true);
    // Luka (PG) <-> wing (BE): Luka can sit, but the wing can't play PG.
    expect(canMoveTo(slots(), 0, 7)).toBe(false);
    // Wing (BE) -> SF occupied by LeBron: the wing fits SF, LeBron can go to the bench.
    expect(canMoveTo(slots(), 7, 2)).toBe(true);
    // Luka -> SF: he doesn't fit SF, whatever LeBron can do.
    expect(canMoveTo(slots(), 0, 2)).toBe(false);
  });

  it("treats IR like any other slot: it needs IR in eligible_slots", () => {
    expect(canMoveTo(slots(), 4, 9)).toBe(true);
    const grid = slots();
    grid[4] = { ...grid[4], player: { ...grid[4].player!, eligible_slots: [4, 11, 12] } };
    expect(canMoveTo(grid, 4, 9)).toBe(false);
  });

  it("never targets the mover's own row, its own slot, or a row with nobody to move", () => {
    expect(canMoveTo(slots(), 0, 0)).toBe(false);
    expect(canMoveTo(slots(), 5, 6)).toBe(false); // UT -> UT
    expect(canMoveTo(slots(), 1, 0)).toBe(false); // an empty row moves nobody
  });

  it("lists every legal destination", () => {
    expect([...legalTargets(slots(), 0)].sort()).toEqual([1, 5, 6, 8, 9]);
  });
});

describe("buildMove", () => {
  function everyoneOnce(assignments: { player_id: number }[]) {
    const ids = assignments.map((a) => a.player_id).sort();
    expect(ids).toEqual(currentAssignments(slots()).map((a) => a.player_id).sort());
  }

  it("moves a player into an empty slot and keeps everyone else where they were", () => {
    const moved = buildMove(slots(), 0, 1);
    everyoneOnce(moved);
    expect(moved).toContainEqual({ player_id: 1, slot_id: 1 });
    expect(moved).toContainEqual({ player_id: 2, slot_id: 2 });
    expect(moved).toContainEqual({ player_id: 5, slot_id: 12 });
  });

  it("swaps the two players in a swap", () => {
    const moved = buildMove(slots(), 0, 5);
    everyoneOnce(moved);
    expect(moved).toContainEqual({ player_id: 1, slot_id: 11 });
    expect(moved).toContainEqual({ player_id: 4, slot_id: 0 });
  });
});

describe("the calendar", () => {
  it("groups chosen days under their matchups across a boundary", () => {
    const groups = groupDaysByMatchup([7, 5, 6, 5], CALENDAR_DAYS, MATCHUPS);
    expect(groups.map((g) => [g.matchup?.id, g.days.map((d) => d.sp)])).toEqual([
      [1, [5, 6]],
      [2, [7]],
    ]);
  });

  it("toggles a chip and keeps the selection sorted", () => {
    expect(toggleDay([3, 1], 2)).toEqual([1, 2, 3]);
    expect(toggleDay([1, 2, 3], 2)).toEqual([1, 3]);
  });

  it("writes days and matchup ranges the way ESPN does", () => {
    expect(dayLabel("2026-10-20")).toBe("Tue 10/20");
    expect(matchupRange("2026-10-20", "2026-10-25")).toBe("Oct 20–25");
    expect(matchupRange("2026-10-26", "2026-11-01")).toBe("Oct 26–Nov 1");
  });

  it("clamps a day into the season", () => {
    expect(clampDay(0, 20)).toBe(1);
    expect(clampDay(99, 20)).toBe(20);
  });

  it("mirrors the server's sync window and pulls the shown day's matchup outside it", () => {
    expect(defaultSyncWindow(calendar())).toEqual({ first: 1, last: 13 });
    expect(syncRange(calendar(), 4)).toEqual({});
    expect(syncRange(calendar(), 13)).toEqual({});
    expect(syncRange(calendar(), 15)).toEqual({ first_sp: 14, last_sp: 20 });
    expect(syncRange(calendar({ effective_period: 9 }), 3)).toEqual({ first_sp: 1, last_sp: 6 });
  });
});

describe("badges", () => {
  it("labels all four lineup sources", () => {
    expect(lineupSourceLabel("espn")).toBe("ESPN lineup");
    expect(lineupSourceLabel("manual")).toBe("Edited here");
    expect(lineupSourceLabel("carried", "2026-10-21")).toBe("Carried from Wed 10/21");
    expect(lineupSourceLabel("carried")).toBe("Carried from an earlier day");
    expect(lineupSourceLabel("roster")).toBe("From roster");
  });

  it("labels the Plan view's three lineup sources", () => {
    expect(lineupSourceLabel("plan_edit")).toBe("Plan: edited here");
    expect(lineupSourceLabel("plan_carried", "2026-10-22")).toBe("Plan: carried from Thu 10/22");
    expect(lineupSourceLabel("plan_carried")).toBe("Plan: carried from an earlier day");
    expect(lineupSourceLabel("from_actual")).toBe("Plan: from the actual lineup");
  });

  it("shows an injury badge only for an unhealthy status", () => {
    expect(injuryBadge(null)).toBeNull();
    expect(injuryBadge("ACTIVE")).toBeNull();
    expect(injuryBadge("NORMAL")).toBeNull();
    expect(injuryBadge("OUT")).toBe("O");
    expect(injuryBadge("DAY_TO_DAY")).toBe("DTD");
    expect(injuryBadge("SOMETHING_NEW")).toBe("SOMETHING NEW");
  });

  it("writes the waiver badge from the server's string, or says the time is unknown", () => {
    expect(waiverBadge({ clears_at_display: "clears Thu 10/22", clears_at_known: true })).toBe(
      "W · clears Thu 10/22",
    );
    expect(waiverBadge({ clears_at_display: null, clears_at_known: false })).toBe(
      "W · clear time unknown",
    );
  });
});

describe("teamHref", () => {
  it("leaves defaults out of the URL", () => {
    expect(teamHref(null, "projected")).toBe("/team");
    expect(teamHref(3, "projected")).toBe("/team?day=3");
    expect(teamHref(3, "l7")).toBe("/team?day=3&basis=l7");
  });
});
