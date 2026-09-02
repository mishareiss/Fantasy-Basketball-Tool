import { Suspense } from "react";

import { BoardPage } from "@/components/board/BoardPage";
import { BoardLoading } from "@/components/board/BoardStates";

/**
 * The board is the app's front door: the ranked, tiered list this whole backend exists to
 * produce. The reachability probe it replaced now lives in the footer, and in full at /status.
 *
 * `BoardPage` reads the query string, so it has to sit under a Suspense boundary — see the
 * useSearchParams guide in node_modules/next/dist/docs.
 */
export default function Home() {
  return (
    <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">
      <Suspense fallback={<BoardLoading />}>
        <BoardPage />
      </Suspense>
    </main>
  );
}
