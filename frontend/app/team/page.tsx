import { Suspense } from "react";

import { TeamPage } from "@/components/team/TeamPage";
import { TeamLoading } from "@/components/team/TeamStates";

export const metadata = {
  title: "Team — Fantasy Basketball Dynasty Tool",
};

/**
 * The in-season Team tab: my roster day by day, and the waiver list beside it.
 *
 * Unlike My Board and the draft room, this page IS worth linking to a particular state of: the
 * roster's day (and its stat basis) live in the query string, so a refresh keeps the day you
 * were on. `TeamPage` reads the query string, so it sits under a Suspense boundary (the
 * useSearchParams guide in node_modules/next/dist/docs).
 *
 * As wide as the board: the roster and the waiver list sit side by side on a laptop.
 */
export default function Team() {
  return (
    <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">
      <Suspense fallback={<TeamLoading label="Reading the calendar…" />}>
        <TeamPage />
      </Suspense>
    </main>
  );
}
