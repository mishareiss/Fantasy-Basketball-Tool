/**
 * Number formatting for the board.
 *
 * The board is read by scanning columns, so every cell has to be the same width and the
 * same precision, and a missing number has to look missing rather than like a zero.
 */

/** An em dash, for a value the backend gave us as null. Not "0" — we don't know it. */
export const MISSING = "—";

export function decimal(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? MISSING : value.toFixed(digits);
}

/** The age multiplier, which is only ever interesting to two places: 1.08, 0.94. */
export function multiplier(value: number | null | undefined): string {
  return value === null || value === undefined ? MISSING : `${value.toFixed(2)}×`;
}

export function whole(value: number | null | undefined): string {
  return value === null || value === undefined ? MISSING : String(Math.round(value));
}

/** "PG/SG", or an em dash for a player we hold no positions for. */
export function positions(values: string[] | null | undefined): string {
  return values && values.length > 0 ? values.join("/") : MISSING;
}

/** 2027-08-30 -> "Aug 30, 2027". Parsed as UTC so it doesn't slide a day westward. */
export function isoDate(value: string): string {
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
