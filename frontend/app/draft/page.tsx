import { DraftRoomPage } from "@/components/draft/DraftRoomPage";

export const metadata = {
  title: "Draft — Fantasy Basketball Dynasty Tool",
};

/**
 * The draft room: the snake board, pick entry, and the simulation controls.
 *
 * No Suspense boundary and nothing in the query string, for the same reason /my-board has
 * neither: this is a surface you are editing, not a view worth linking to. There is exactly
 * one live draft and the server holds all of it, so a URL could not say anything about this
 * page that opening it doesn't already say.
 *
 * As wide as My Board, and for a harder reason: the board is ten columns of names and it
 * scrolls sideways below that. Ten columns at a readable width is most of a laptop.
 */
export default function Draft() {
  return (
    <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">
      <DraftRoomPage />
    </main>
  );
}
