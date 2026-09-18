import { MarketPage } from "@/components/market/MarketPage";

export const metadata = {
  title: "Market lines — Fantasy Basketball Dynasty Tool",
};

/**
 * The one source that is kept rather than imported.
 *
 * No Suspense boundary, for the same reason the importer has none: the page's state is a
 * form and a book name, not the query string.
 */
export default function Market() {
  return (
    <main className="mx-auto w-full max-w-[1100px] flex-1 px-4 py-6 sm:px-6">
      <MarketPage />
    </main>
  );
}
