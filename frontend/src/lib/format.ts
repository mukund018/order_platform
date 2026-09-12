/**
 * Formatting helpers.
 *
 * Money is integer paise everywhere in this platform - in the database, on the wire and
 * here - and is only ever divided by 100 at the moment it is drawn on screen. Doing it
 * any earlier is how a rounding error gets into a total.
 */

const RUPEES = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

export const money = (paise: number): string => RUPEES.format(paise / 100);

/** Compact rupees for dense panels: ₹41.4L rather than ₹41,41,127.00. */
export function moneyShort(paise: number): string {
  const rupees = paise / 100;
  if (rupees >= 1e7) return `₹${(rupees / 1e7).toFixed(2)}Cr`;
  if (rupees >= 1e5) return `₹${(rupees / 1e5).toFixed(2)}L`;
  if (rupees >= 1e3) return `₹${(rupees / 1e3).toFixed(1)}k`;
  return RUPEES.format(rupees);
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

const TIME = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export const clock = (iso: string | null): string => (iso ? TIME.format(new Date(iso)) : "—");
export const stamp = (iso: string | null): string => (iso ? DATE_TIME.format(new Date(iso)) : "—");

/** UTC, because that is the timezone every log line and every database row is in. */
export function utcStamp(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toISOString().replace("T", " ").replace("Z", "Z");
}

export function ago(iso: string | null): string {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86_400)}d ago`;
}

export const shortId = (id: string | null | undefined, length = 8): string =>
  id ? id.replace(/-/g, "").slice(0, length) : "—";

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard access is refused outside a secure context, which includes plain http
    // on anything but localhost. Not worth an error dialog.
    return false;
  }
}
