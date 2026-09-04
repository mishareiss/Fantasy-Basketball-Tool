import { ImportPage } from "@/components/import/ImportPage";

export const metadata = {
  title: "Import — Fantasy Basketball Dynasty Tool",
};

/**
 * The browser half of `make import`.
 *
 * No Suspense boundary here, unlike the board: the importer keeps its whole state in the
 * form rather than the query string, because half of that state is a pasted table nobody
 * wants in a URL.
 */
export default function Import() {
  return (
    <main className="mx-auto w-full max-w-[1100px] flex-1 px-4 py-6 sm:px-6">
      <ImportPage />
    </main>
  );
}
