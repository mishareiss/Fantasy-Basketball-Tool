import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { AuthGate } from "@/components/AuthGate";
import { BackendStatusStrip } from "@/components/BackendStatus";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Fantasy Basketball Dynasty Tool",
  description: "Draft prep, valuation, and league analysis for our dynasty league.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        {/* Five pages worth navigating between: the board this exists to produce, MY board
            (the same players in the order I actually want them, kept by hand), the DRAFT
            room those two are for, the importer that feeds them, and the market lines — the
            one source that is entered rather than imported and replaced. Draft sits after
            My Board because that is the order they are used in: build the board, then draft
            off it. Status stays in the footer — it is a diagnostic, not a destination. */}
        <nav className="flex items-center gap-4 border-b border-zinc-200 px-4 py-2.5 text-sm sm:px-6 dark:border-zinc-800">
          <Link href="/" className="font-medium text-zinc-800 hover:underline dark:text-zinc-200">
            Board
          </Link>
          <Link
            href="/my-board"
            className="font-medium text-zinc-800 hover:underline dark:text-zinc-200"
          >
            My Board
          </Link>
          <Link
            href="/draft"
            className="font-medium text-zinc-800 hover:underline dark:text-zinc-200"
          >
            Draft
          </Link>
          <Link
            href="/import"
            className="font-medium text-zinc-800 hover:underline dark:text-zinc-200"
          >
            Import
          </Link>
          <Link
            href="/market"
            className="font-medium text-zinc-800 hover:underline dark:text-zinc-200"
          >
            Market
          </Link>
        </nav>
        {/* The shared-password gate wraps the PAGES and nothing else, so the nav stays where it
            is and the status strip below keeps answering on the login screen — "API ok /
            Database connected" beside a password box is the difference between a forgotten
            password and a stopped backend. It shows nothing at all until a call comes back 401,
            which on a backend with no APP_ACCESS_TOKEN set is never. */}
        <AuthGate>{children}</AuthGate>
        {/* The reachability probe, demoted to a strip: still the fastest way to tell an
            empty board from a stopped backend, without owning the page any more. */}
        <footer className="mt-auto flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-zinc-200 px-4 py-3 sm:px-6 dark:border-zinc-800">
          <BackendStatusStrip />
          <Link
            href="/status"
            className="text-xs text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
          >
            Status
          </Link>
        </footer>
      </body>
    </html>
  );
}
