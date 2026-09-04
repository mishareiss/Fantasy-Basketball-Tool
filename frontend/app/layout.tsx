import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

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
        {/* Two pages worth navigating between: the board this exists to produce, and the
            importer that feeds it. Status stays in the footer — it is a diagnostic, not a
            destination. */}
        <nav className="flex items-center gap-4 border-b border-zinc-200 px-4 py-2.5 text-sm sm:px-6 dark:border-zinc-800">
          <Link href="/" className="font-medium text-zinc-800 hover:underline dark:text-zinc-200">
            Board
          </Link>
          <Link
            href="/import"
            className="font-medium text-zinc-800 hover:underline dark:text-zinc-200"
          >
            Import
          </Link>
        </nav>
        {children}
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
