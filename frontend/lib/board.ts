/**
 * Board vocabulary shared by the controls, the table, and the inspector.
 *
 * The horizon is the board's one global switch, and three components have to agree about
 * what it is called and which number it selects — so they all read it from here.
 */

import type { BoardRow, Horizon } from "@/lib/api";

export const HORIZON_LABEL: Record<Horizon, string> = {
  current_year: "Win now",
  dynasty: "Dynasty",
};

export const HORIZON_HINT: Record<Horizon, string> = {
  current_year: "Projected fantasy points per game under our scoring, as-is.",
  dynasty: "The same per-game projection through the age/longevity curve.",
};

export function otherHorizon(horizon: Horizon): Horizon {
  return horizon === "dynasty" ? "current_year" : "dynasty";
}

/** The one number a horizon ranks by — mirrors `horizon_value` in app/api/players.py. */
export function horizonValue(row: BoardRow, horizon: Horizon): number {
  return horizon === "dynasty" ? row.dynasty_value : row.current_year_value;
}
