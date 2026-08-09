/**
 * One date format for the whole app, and one answer for a date that is not one.
 *
 * `new Date(undefined)` renders as "Invalid Date", which is how a missing due
 * date ends up looking like a bug. Everything here returns the caller's
 * fallback instead.
 */
const FORMAT = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
});

export function formatDate(value: string | null | undefined, fallback: string): string {
  if (!value) {
    return fallback;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? fallback : FORMAT.format(parsed);
}
