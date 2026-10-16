import type { GameInfo } from "@/lib/api";

/**
 * A game as a two-line cell: `@ LAL` over `7:00 PM`. The roster, the waiver list and the card
 * all draw it, so a game reads the same everywhere and no column has to be wide enough for the
 * one-line `@ LAL 7:00 PM`.
 *
 * Both lines are the server's (`display_matchup`, `display_time`, Pacific): the page stacks
 * them and never splits a string or formats a time itself. The one-line `display` is the
 * tooltip.
 */
export function GameCell({ game, align = "left" }: { game: GameInfo | null; align?: "left" | "center" }) {
  if (game === null) return null;
  return (
    <span
      data-game-matchup={game.display_matchup}
      title={game.display}
      className={`flex flex-col leading-tight ${align === "center" ? "items-center" : "items-start"}`}
    >
      <span className="whitespace-nowrap">{game.display_matchup}</span>
      <span className="text-[10px] whitespace-nowrap text-zinc-500 tabular-nums">
        {game.display_time}
      </span>
    </span>
  );
}
