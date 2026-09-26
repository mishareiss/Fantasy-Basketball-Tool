"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  type DraftPlanResponse,
  type DraftStateResponse,
  type PlanPickRow,
  type PlanPlayerRow,
} from "@/lib/api";
import {
  AVAILABILITY_FILL,
  AVAILABILITY_LABEL,
  AVAILABILITY_TEXT,
  PLAN_PICKS,
  availabilityDescription,
  availabilityPercent,
  availabilityTone,
  morePlanPicks,
} from "@/lib/draft";
import { MISSING, positions as positionList } from "@/lib/format";
import { tagStyle } from "@/lib/masterboard";
import { QUIET_BUTTON } from "./DraftStates";

/**
 * The plan: my board at each of my next few picks, with the odds each name lasts that long.
 *
 * This is the one panel on the site that answers a question the board cannot. My board says
 * who I want; the draft says who is gone; neither says whether the man I want will still be
 * there in seventeen picks. `GET /draft/plan` is a thousand simulated rooms answering
 * exactly that, and everything below is its presentation.
 *
 * FOUR PICKS, NOT TWENTY. The plan defaults to every remaining pick of mine, which from pick
 * 1 means simulating nearly the whole draft a thousand times for an answer about round 14
 * that nobody is deciding on the clock. So the panels open on the next `PLAN_PICKS` and walk
 * out from there on request.
 *
 * IT RE-READS ON EVERY PICK, because it has to: the availability of a player is a statement
 * about the picks already made, and one more pick changes every number on screen. The effect
 * keys on `picks_made` (and completion), which is the cheapest honest description of "the
 * draft moved" — and it is request-key guarded, the same `pending` ticket the room uses on
 * its writes, because a four-pick Monte Carlo takes long enough that a slow answer to an old
 * state could otherwise land on top of a fresh one.
 *
 * A PANEL IS REFERENCE UNLESS IT IS MY TURN. A row drafts the player only when I am on the
 * clock AND the panel is the pick I am on the clock FOR (`picks_away === 0`). Clicking a name
 * in the round-3 panel cannot mean anything — the pick in between hasn't happened — so those
 * rows are not buttons at all rather than buttons that refuse. Opponents' picks go in through
 * the search box, which is the control that can name any seat's man.
 *
 * A FAILED PLAN IS A NOTE, NOT A PAGE ERROR. The board, the clock and every control still
 * work without it; the same call `CatalogUnavailable` makes about the search box.
 */

type Settled =
  | { status: "ready"; plan: DraftPlanResponse }
  | { status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

const CHIP = "rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase";

export function DraftPlan({
  state,
  onPick,
  isBusy,
}: {
  state: DraftStateResponse;
  /** Draft him — only ever reachable from the on-the-clock panel. See the header. */
  onPick: (playerId: number) => void;
  isBusy: boolean;
}) {
  const remaining = state.my_remaining_pick_numbers.length;
  const [wanted, setWanted] = useState(PLAN_PICKS);
  // Keyed by the read it answers, the same guard /my-board keeps on its board: a plan
  // computed over the picks that had been made a moment ago is not this draft's plan. The
  // "updating…" state is DERIVED from that key rather than set, so there is no second piece
  // of state to fall out of step with the first.
  const [settled, setSettled] = useState<(Settled & { key: string }) | null>(null);

  /** Which plan read is the latest one issued — an older answer must not land on top of it. */
  const pending = useRef(0);
  const { picks_made: made, is_complete: complete } = state;
  const key = `${wanted}:${made}:${complete}`;

  useEffect(() => {
    const ticket = (pending.current += 1);
    // Rebuilt here rather than closed over, so the effect's dependencies are the three
    // things the request is actually made of.
    const answering = `${wanted}:${made}:${complete}`;
    api
      .draftPlan({ picks: wanted })
      .then((plan) => {
        if (ticket === pending.current) setSettled({ key: answering, status: "ready", plan });
      })
      .catch((caught: unknown) => {
        if (ticket === pending.current) {
          setSettled({ key: answering, status: "error", error: asApiError(caught) });
        }
      });
    // `made` and `complete` ARE the draft, as far as this read is concerned: every write on
    // the page moves one of them, and neither moves without a write.
  }, [wanted, made, complete]);

  // The panels on screen while a re-read is in flight are the ones from the pick before —
  // dimmed and labelled, rather than blanked, because a plan that vanished on every pick
  // would be unreadable exactly when the room is moving fastest.
  const updating = settled?.key !== key;

  if (settled === null) {
    return (
      <section className="flex flex-col gap-2" aria-busy>
        <PlanHeader />
        <p role="status" className="text-xs text-zinc-500">
          Simulating the room…
        </p>
      </section>
    );
  }

  if (settled.status === "error") {
    return (
      <section className="flex flex-col gap-2">
        <PlanHeader />
        <PlanUnavailable error={settled.error} />
      </section>
    );
  }

  const { plan } = settled;
  const more = morePlanPicks(wanted, remaining);

  return (
    <section className="flex flex-col gap-3" aria-busy={updating} data-plan>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <PlanHeader />
        <p role="status" className="font-mono text-xs text-zinc-500">
          {updating
            ? "updating…"
            : plan.is_complete
              ? "draft complete"
              : `${plan.iterations.toLocaleString()} rooms · seed ${plan.seed} · ${plan.available_on_board} still on your board`}
        </p>
      </div>

      {plan.is_complete || plan.picks.length === 0 ? (
        <p
          data-plan="empty"
          className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900"
        >
          {plan.is_complete
            ? "The draft is over — there is nothing left to plan for."
            : "Nothing to plan: no ranked player on your board is both still available and ranked by the field."}
        </p>
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            {plan.picks.map((pick) => (
              <PlanPanel
                key={pick.pick_number}
                pick={pick}
                // Both halves of the rule, and they are separate facts: the panel has to be
                // the pick on the clock, and the clock has to be mine.
                actionable={state.is_my_pick && pick.picks_away === 0}
                onPick={onPick}
                isBusy={isBusy}
              />
            ))}
          </div>

          {more === null ? null : (
            <button
              type="button"
              onClick={() => setWanted(more)}
              disabled={updating}
              title="Each extra pick is another round the thousand simulated rooms have to play out, so this is a step rather than a switch."
              className={`${QUIET_BUTTON} self-start`}
            >
              Plan {more - wanted} more {more - wanted === 1 ? "pick" : "picks"}
            </button>
          )}
        </>
      )}
    </section>
  );
}

function PlanHeader() {
  return (
    <div className="flex flex-col gap-0.5">
      <h2 className="text-sm font-semibold tracking-tight">Your next picks</h2>
      <p className="text-xs text-zinc-600 dark:text-zinc-400">
        Your board at each of them, with the share of a thousand simulated rooms in which the
        man was still there. A later pick assumes you take nobody in between, so it can only
        overstate who survives.
      </p>
    </div>
  );
}

function PlanPanel({
  pick,
  actionable,
  onPick,
  isBusy,
}: {
  pick: PlanPickRow;
  actionable: boolean;
  onPick: (playerId: number) => void;
  isBusy: boolean;
}) {
  const now = pick.picks_away === 0;
  return (
    <article
      data-plan-pick={pick.pick_number}
      data-plan-away={pick.picks_away}
      className={`flex flex-col gap-2 rounded-lg border p-3 ${
        actionable
          ? "border-amber-300 bg-amber-50/60 dark:border-amber-500/40 dark:bg-amber-500/5"
          : "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"
      }`}
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Pick {pick.pick_number}
        </h3>
        <span className="font-mono text-xs text-zinc-500">round {pick.round}</span>
        <span
          className={`${CHIP} ${
            now
              ? "bg-amber-200 text-amber-900 dark:bg-amber-500/25 dark:text-amber-200"
              : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
          }`}
        >
          {now ? "on the clock" : `${pick.picks_away} away`}
        </span>
      </header>

      <p className="flex flex-wrap items-center gap-1 text-[11px] text-zinc-500">
        <span className="tracking-wide uppercase">Still starting nobody at</span>
        {pick.open_needs.length === 0 ? (
          <span className="text-zinc-400 dark:text-zinc-600">
            nothing — your starting five is set
          </span>
        ) : (
          pick.open_needs.map((need) => (
            <span
              key={need}
              data-need={need}
              className={`${CHIP} bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200`}
            >
              {need}
            </span>
          ))
        )}
      </p>

      <PlanList
        title="Targets"
        empty="None of your targets is still on the board."
        players={pick.targets}
        pick={pick}
        actionable={actionable}
        onPick={onPick}
        isBusy={isBusy}
      />
      <PlanList
        title="Best available"
        empty="Nobody on your board is both still available and ranked by the field."
        players={pick.best_available}
        pick={pick}
        actionable={actionable}
        onPick={onPick}
        isBusy={isBusy}
      />
    </article>
  );
}

function PlanList({
  title,
  empty,
  players,
  pick,
  actionable,
  onPick,
  isBusy,
}: {
  title: string;
  empty: string;
  players: PlanPlayerRow[];
  pick: PlanPickRow;
  actionable: boolean;
  onPick: (playerId: number) => void;
  isBusy: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">{title}</h4>
      {players.length === 0 ? (
        <p className="text-xs text-zinc-400 dark:text-zinc-600">{empty}</p>
      ) : (
        <ul aria-label={`${title} at pick ${pick.pick_number}`} className="flex flex-col">
          {players.map((player) => (
            <li
              key={player.espn_player_id}
              data-plan-player={player.espn_player_id}
              className="border-b border-zinc-100 last:border-b-0 dark:border-zinc-900"
            >
              <PlanPlayer
                player={player}
                pick={pick}
                actionable={actionable}
                onPick={onPick}
                isBusy={isBusy}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PlanPlayer({
  player,
  pick,
  actionable,
  onPick,
  isBusy,
}: {
  player: PlanPlayerRow;
  pick: PlanPickRow;
  actionable: boolean;
  onPick: (playerId: number) => void;
  isBusy: boolean;
}) {
  const body = <PlanPlayerBody player={player} pick={pick} />;

  // Not a disabled button: a disabled control says "this could happen and currently can't",
  // and a name in the round-3 panel is never going to be clickable. It is reference.
  if (!actionable) {
    return <div className="w-full px-1 py-1.5">{body}</div>;
  }

  return (
    <button
      type="button"
      disabled={isBusy}
      onClick={() => onPick(player.espn_player_id)}
      title={`Draft ${player.name} at pick ${pick.pick_number}`}
      className="w-full cursor-pointer rounded px-1 py-1.5 text-left transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-500 dark:hover:bg-amber-500/15"
    >
      {body}
    </button>
  );
}

function PlanPlayerBody({ player, pick }: { player: PlanPlayerRow; pick: PlanPickRow }) {
  const tag = tagStyle(player.tag);
  return (
    <div className="flex items-center gap-2">
      <span className="w-8 shrink-0 text-right font-mono text-xs tabular-nums text-zinc-500">
        {player.rank ?? MISSING}
      </span>
      {player.tier === null ? null : (
        <span
          data-plan-tier={player.tier}
          title={`Tier ${player.tier} on your board.`}
          className="shrink-0 rounded bg-zinc-100 px-1 font-mono text-[10px] text-zinc-500 tabular-nums dark:bg-zinc-800 dark:text-zinc-400"
        >
          T{player.tier}
        </span>
      )}

      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
          <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {player.name}
          </span>
          <span className="text-[11px] text-zinc-500">{positionList(player.positions)}</span>
          {tag === null ? null : <span className={`${CHIP} ${tag.className}`}>{tag.label}</span>}
          {player.fills_need ? (
            <span
              data-fills-need
              title="He covers a dedicated starter slot you still have nobody in."
              className={`${CHIP} bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200`}
            >
              Fills need
            </span>
          ) : null}
        </span>
        <span
          className="block font-mono text-[10px] text-zinc-400 tabular-nums dark:text-zinc-600"
          title="Where the field this draft is modelled against has him — the board the availability is actually computed over."
        >
          field {player.field_rank ?? MISSING}
        </span>
      </span>

      <Availability value={player.availability} picksAway={pick.picks_away} />
    </div>
  );
}

/**
 * The chance he lasts: a percentage, a word, and a bar — in that order of authority.
 *
 * The colour ramp is the last of the three on purpose. A colour-blind reader and a
 * greyscale screenshot both still get the number and the label; the bar and the hue are
 * what make a panel of eight names scannable without reading any of them.
 */
function Availability({ value, picksAway }: { value: number; picksAway: number }) {
  const percent = availabilityPercent(value);
  const tone = availabilityTone(value);
  const description = availabilityDescription(value, picksAway);

  return (
    <span
      data-availability={percent}
      data-availability-tone={tone}
      title={description}
      className="flex w-20 shrink-0 flex-col items-end gap-0.5"
    >
      <span className={`font-mono text-xs font-semibold tabular-nums ${AVAILABILITY_TEXT[tone]}`}>
        {percent}%
      </span>
      <span
        aria-hidden
        className="h-1 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
      >
        <span
          className={`block h-full rounded-full ${AVAILABILITY_FILL[tone]}`}
          style={{ width: `${percent}%` }}
        />
      </span>
      <span aria-hidden className="text-[10px] text-zinc-500">
        {AVAILABILITY_LABEL[tone]}
      </span>
      <span className="sr-only">{description}</span>
    </span>
  );
}

/**
 * The plan failed, but the draft didn't.
 *
 * Sibling of `CatalogUnavailable` and the same argument: the grid, the clock and every
 * control still work — the one thing that doesn't is the probability column, which is a
 * separate read and the most expensive one on the page.
 */
export function PlanUnavailable({ error }: { error: ApiError }) {
  return (
    <p
      data-plan="unavailable"
      className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"
    >
      Couldn’t build the plan, so there are no availability numbers:{" "}
      {error.detail ?? error.message}. The draft itself is fine — the grid, the clock and the
      pick entry all still work. The plan is your board joined to the simulation, so an empty{" "}
      <Link href="/my-board" className="underline">
        My Board
      </Link>{" "}
      is the usual reason.
    </p>
  );
}
