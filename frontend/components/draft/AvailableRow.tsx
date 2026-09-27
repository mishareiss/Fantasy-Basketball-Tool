"use client";

import type { MasterPlayerRow } from "@/lib/api";
import {
  AVAILABILITY_TEXT,
  availabilityDescription,
  availabilityPercent,
  availabilityTone,
} from "@/lib/draft";
import { MISSING, positions as positionList } from "@/lib/format";
import { tagStyle } from "@/lib/masterboard";

/**
 * One available player, wherever he is drawn: the sidebar and all six rankings columns.
 *
 * Both surfaces answer the same question — "who is left, and will he last?" — so they are
 * one row rather than two that drift. What differs is width, and that is the `compact` flag:
 * a column six across has no room for the positions or the tag's word, so it prints the
 * number and the name and nothing else. Everything the row can say is on the ROW; there is
 * no hover-only fact, because a draft is read at a glance.
 *
 * THE DRAFT BUTTON ENTERS THE PICK ON THE CLOCK, whoever owns it. It is not my-pick-only:
 * the sidebar is the room's ONLY player search now (the board's entry box is gone), so it is
 * also how an opponent's pick gets typed in in manual mode — find him, press Draft, and the
 * backend puts him where the snake says. `clock` is what stops that being a surprise: the row
 * says whose pick it would be before you press it. Disabled, not absent, when there is no pick
 * to make at all — a control that vanished under a moving clock is harder to use than one that
 * greys out.
 *
 * AVAILABILITY IS A NUMBER FIRST. The percentage is printed; the colour only ramps it. A
 * player the field doesn't rank has no percentage at all (`null`, not 0) — he is outside
 * the draft's universe, so no simulation could have taken him and none could have left him.
 *
 * AND IT IS ALWAYS ABOUT A FUTURE PICK. `targetPick` is the pick number the percentage was
 * computed at — my next one while I am waiting, the one AFTER this one while I am on the clock,
 * because everybody is trivially 100% at a pick I am already making. The number is therefore
 * labelled with the pick it belongs to rather than left to be read as "right now".
 */

const CHIP = "rounded px-1 py-0.5 text-[10px] font-semibold tracking-wide uppercase";

export function AvailableRow({
  player,
  availability,
  targetPick = null,
  picksAway,
  clock = null,
  canDraft,
  isBusy,
  onDraft,
  compact = false,
}: {
  player: MasterPlayerRow;
  /** His chance of lasting to `targetPick`, or null when the field doesn't rank him. */
  availability: number | null;
  /** Which pick that is — always a later one than the clock. Null when there isn't one. */
  targetPick?: number | null;
  /** How far off that pick is, for the sentence the percentage carries. */
  picksAway: number | null;
  /** Whose pick this button would enter: "Your pick — 7", "Pick 5 · Team 3", or null when
      there is none. Display only — the backend reads the clock off its own state. */
  clock?: string | null;
  /** Somebody is on the clock and nothing is in flight: this row can enter that pick. */
  canDraft: boolean;
  isBusy: boolean;
  onDraft: (playerId: number) => void;
  compact?: boolean;
}) {
  const tag = tagStyle(player.tag);
  const percent = availability === null ? null : availabilityPercent(availability);
  const tone = availability === null ? null : availabilityTone(availability);

  return (
    <div
      data-available-player={player.espn_player_id}
      className="flex items-center gap-1.5 px-1.5 py-1"
    >
      <span className="w-7 shrink-0 text-right font-mono text-[11px] text-zinc-500 tabular-nums">
        {player.rank ?? MISSING}
      </span>

      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span
            title={player.name}
            className="truncate text-[13px] font-medium text-zinc-900 dark:text-zinc-100"
          >
            {player.name}
          </span>
          {compact || tag === null ? null : (
            <span className={`${CHIP} shrink-0 ${tag.className}`} data-tag={player.tag}>
              {tag.label}
            </span>
          )}
        </span>
        {compact ? null : (
          <span className="truncate font-mono text-[10px] text-zinc-500">
            {positionList(player.positions)}
            {player.nba_team ? ` · ${player.nba_team}` : ""}
          </span>
        )}
      </span>

      {/* The compact column keeps the tag as a dot: the word doesn't fit, and losing the
          fact that this is a man you wanted would be worse than losing the word. */}
      {compact && tag !== null ? (
        <span
          data-tag={player.tag}
          title={tag.label}
          className={`${CHIP} shrink-0 px-1 py-0`}
          style={{ lineHeight: 1 }}
        >
          <span className={`${tag.className} rounded px-1`}>{tag.label[0]}</span>
        </span>
      ) : null}

      <span
        data-availability={percent ?? ""}
        data-availability-tone={tone ?? ""}
        title={
          percent === null
            ? "No source ranks him, so he is not in this draft's universe and there is no availability to compute."
            : availabilityDescription(availability ?? 0, picksAway ?? 0, targetPick)
        }
        className={`w-9 shrink-0 text-right font-mono text-[11px] font-semibold tabular-nums ${
          tone === null ? "text-zinc-400 dark:text-zinc-600" : AVAILABILITY_TEXT[tone]
        }`}
      >
        {percent === null ? MISSING : `${percent}%`}
      </span>

      <button
        type="button"
        disabled={!canDraft || isBusy}
        onClick={() => onDraft(player.espn_player_id)}
        aria-label={`Draft ${player.name}`}
        title={
          canDraft
            ? `${clock ?? "The pick on the clock"}: take ${player.name}.`
            : "Nothing to enter: the draft is over, or a pick is still being saved."
        }
        className="shrink-0 rounded border border-zinc-300 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-700 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-500 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
      >
        Draft
      </button>
    </div>
  );
}
