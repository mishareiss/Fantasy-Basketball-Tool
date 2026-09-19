import { MasterBoardPage } from "@/components/masterboard/MasterBoardPage";

export const metadata = {
  title: "My board — Fantasy Basketball Dynasty Tool",
};

/**
 * The master ranking: our own order, stored.
 *
 * No Suspense boundary and nothing in the query string, for the same reason the market page
 * has neither: the page's state is a board you are editing, not a view you would want to
 * share a link to. The one dial it has (the horizon) is a lens over the same board, and a
 * URL that could put someone else's lens on your order would be a trap rather than a feature.
 *
 * Wider than the other pages on purpose — a row carries a rank, a player, two reference
 * numbers, a tag, a note and six controls, and squeezing that into 1100px wraps the controls
 * onto a second line just where the drag handles are.
 */
export default function MyBoard() {
  return (
    <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">
      <MasterBoardPage />
    </main>
  );
}
