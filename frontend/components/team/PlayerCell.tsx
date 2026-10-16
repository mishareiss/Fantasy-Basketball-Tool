"use client";

import { MISSING, positions as positionList } from "@/lib/format";
import { injuryBadge } from "@/lib/team";
import { Badge } from "./TeamStates";

/**
 * A player's name (the button that opens his card), his NBA team and positions, and an injury
 * badge when he isn't healthy. Shared by the roster and the waiver list so a player reads the
 * same in both.
 */
export function PlayerCell({
  player,
  onOpen,
  children,
}: {
  player: {
    id: number;
    name: string;
    nba_team: string | null;
    positions: string[];
    injury_status: string | null;
  };
  onOpen: () => void;
  /** Extra badges after the injury one (the waiver list's clear time). */
  children?: React.ReactNode;
}) {
  const injury = injuryBadge(player.injury_status);
  return (
    <div className="flex min-w-0 flex-col">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <button
          type="button"
          data-open-player={player.id}
          onClick={onOpen}
          className="truncate text-left font-medium text-zinc-900 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 dark:text-zinc-100"
        >
          {player.name}
        </button>
        {injury ? (
          <Badge tone="rose" data-injury={player.injury_status ?? undefined}>
            {injury}
          </Badge>
        ) : null}
        {children}
      </div>
      <span className="text-[11px] text-zinc-500">
        {player.nba_team ?? MISSING} · {positionList(player.positions)}
      </span>
    </div>
  );
}
