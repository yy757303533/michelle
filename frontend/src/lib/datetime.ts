/** Backend serialises datetimes from SQLite as naive ISO strings (no tz
 * suffix), but the values themselves ARE UTC (we always store via
 * `datetime.now(UTC)` on the Python side). JavaScript's `new Date(iso)`
 * defaults to LOCAL time when the string lacks a timezone marker, which
 * was producing displays 7-8 hours off in CST.
 *
 * Workaround: append `Z` before parsing if the string lacks any tz
 * indicator. This is purely a frontend correction; no backend change
 * needed unless we ever store non-UTC timestamps (which we don't). */

const TZ_RE = /Z|[+-]\d{2}:?\d{2}$/i;

function ensureUtc(s: string): string {
  return TZ_RE.test(s) ? s : s + "Z";
}

/** Format an ISO timestamp string in the user's local time zone. */
export function fmtDateTime(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(ensureUtc(s)).toLocaleString();
}

/** Just the time portion (HH:MM:SS local) — useful for compact tables. */
export function fmtTime(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(ensureUtc(s)).toLocaleTimeString();
}

/** Duration in milliseconds → human-friendly. */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m${s}s`;
}
