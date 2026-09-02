"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { HORIZONS, POSITIONS, type Horizon, type Position, type TiersMode } from "@/lib/api";
import { BoardView, DEFAULT_CONTROLS } from "./BoardView";
import { LIMITS, type BoardControlValues } from "./BoardControls";

/**
 * The router half of the board: URL query string in, URL query string out.
 *
 * Kept separate from `BoardView` so exactly one component knows about Next's router — which
 * keeps the board renderable (and testable) without an App Router context around it.
 *
 * `replace`, not `push`: the query string is a bookmark of the board you are looking at, not
 * a trail of every toggle you flipped on the way there.
 */

export function parseControls(params: URLSearchParams): BoardControlValues {
  const horizon = params.get("horizon");
  const position = params.get("position")?.toUpperCase();
  const limit = Number(params.get("limit"));
  const tiers = params.get("tiers");

  return {
    horizon: HORIZONS.includes(horizon as Horizon)
      ? (horizon as Horizon)
      : DEFAULT_CONTROLS.horizon,
    position: POSITIONS.includes(position as Position) ? (position as Position) : null,
    limit: LIMITS.includes(limit as (typeof LIMITS)[number]) ? limit : DEFAULT_CONTROLS.limit,
    tiers: tiers === "off" || tiers === "auto" ? (tiers as TiersMode) : DEFAULT_CONTROLS.tiers,
  };
}

/** Only non-default values go in the URL, so a shared link stays readable. */
export function toQuery(controls: BoardControlValues): string {
  const params = new URLSearchParams();
  if (controls.horizon !== DEFAULT_CONTROLS.horizon) params.set("horizon", controls.horizon);
  if (controls.position) params.set("position", controls.position);
  if (controls.limit !== DEFAULT_CONTROLS.limit) params.set("limit", String(controls.limit));
  if (controls.tiers !== DEFAULT_CONTROLS.tiers) params.set("tiers", controls.tiers);
  const encoded = params.toString();
  return encoded ? `/?${encoded}` : "/";
}

export function BoardPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  return (
    <BoardView
      initialControls={parseControls(new URLSearchParams(searchParams.toString()))}
      onControlsChange={(controls) => router.replace(toQuery(controls), { scroll: false })}
    />
  );
}
