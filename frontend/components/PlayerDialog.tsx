"use client";

import { useEffect, useRef } from "react";

/**
 * The player dialog's shell: the backdrop, the panel, the heading and its Close button, and
 * the keyboard half of "dismissable" (Escape closes, focus goes back where it came from).
 *
 * Lifted out of `masterboard/PlayerDetail` unchanged when the Team tab's player card needed the
 * same dialog: two cards about one player should open, look and close the same way. What goes
 * inside — and the fetch that fills it — stays each card's own.
 */

export const DIALOG_SECTION = "flex flex-col gap-2";
export const DIALOG_HEADING = "text-[11px] font-semibold tracking-wide text-zinc-500 uppercase";

export function PlayerDialog({
  headingId,
  title,
  titleAction,
  subtitle,
  closeLabel,
  onClose,
  children,
}: {
  headingId: string;
  title: string;
  /** Something that belongs beside the name (the Team card's watchlist star). */
  titleAction?: React.ReactNode;
  subtitle: React.ReactNode;
  closeLabel: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDivElement | null>(null);
  // Where focus was when this opened, so closing puts it back on the row rather than at the
  // top of a thousand-row table.
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    opener.current = document.activeElement;
    dialog.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Restoring focus is the half of "dismissable" that is easy to forget and the only half
      // a keyboard notices.
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      // The backdrop. A click on it closes; a click inside the panel does not, which is what
      // the `stopPropagation` on the panel below is for.
      onClick={onClose}
      data-detail-backdrop
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-zinc-900/40 p-4 pt-10 backdrop-blur-sm"
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="flex w-full max-w-2xl flex-col gap-5 rounded-lg border border-zinc-200 bg-white p-5 shadow-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 dark:border-zinc-800 dark:bg-zinc-950"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1.5">
              {titleAction}
              <h2
                id={headingId}
                className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
              >
                {title}
              </h2>
            </div>
            <p className="text-xs text-zinc-500">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={closeLabel}
            className="rounded-md px-2 py-1 text-sm text-zinc-500 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 dark:hover:bg-zinc-900"
          >
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
