/**
 * The watchlist star: filled when he's on our shared watchlist, hollow when not. The caller
 * owns the toggle (optimistic, then a refetch); this is only the button.
 */
export function WatchStar({
  watched,
  name,
  busy = false,
  onToggle,
}: {
  watched: boolean;
  name: string;
  busy?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={watched}
      aria-label={watched ? `Unwatch ${name}` : `Watch ${name}`}
      title={watched ? "On the watchlist — click to remove" : "Add to the watchlist"}
      data-watch-star={watched ? "on" : "off"}
      disabled={busy}
      onClick={onToggle}
      className={`shrink-0 rounded px-0.5 text-base leading-none disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 ${
        watched
          ? "text-amber-500 hover:text-amber-600 dark:text-amber-400"
          : "text-zinc-300 hover:text-amber-500 dark:text-zinc-600 dark:hover:text-amber-400"
      }`}
    >
      <span aria-hidden>{watched ? "★" : "☆"}</span>
    </button>
  );
}
