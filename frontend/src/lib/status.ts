/**
 * The order state machine, as the UI needs to know it.
 *
 * This mirrors `services/orders/app/state.py`. It is presentation only - the server
 * decides every transition and rejects illegal ones - but showing a customer where
 * their order sits on the real path, including the branch it took when it failed, is
 * the difference between "Failed" and an explanation.
 */

import type { OrderStatus } from "../api/types";

export type Tone = "pending" | "progress" | "good" | "bad" | "muted";

interface StatusMeta {
  label: string;
  tone: Tone;
  /** What this status means to a customer, in their words rather than ours. */
  blurb: string;
  terminal: boolean;
}

export const STATUS: Record<OrderStatus, StatusMeta> = {
  PENDING: {
    label: "Pending",
    tone: "pending",
    blurb: "We have your order and are checking stock.",
    terminal: false,
  },
  RESERVED: {
    label: "Reserved",
    tone: "progress",
    blurb: "Stock is held for you. Taking payment now.",
    terminal: false,
  },
  CONFIRMED: {
    label: "Confirmed",
    tone: "good",
    blurb: "Paid and confirmed. A confirmation email is on its way.",
    terminal: true,
  },
  FAILED: {
    label: "Failed",
    tone: "bad",
    blurb: "The order could not be completed. Nothing has been charged.",
    terminal: true,
  },
  EXPIRED: {
    label: "Expired",
    tone: "muted",
    blurb: "The order was not completed in time and the stock was released.",
    terminal: true,
  },
  CANCELLED: {
    label: "Cancelled",
    tone: "muted",
    blurb: "Cancelled. The stock has gone back to the catalogue.",
    terminal: true,
  },
};

/** The route an order takes when everything works. Drawn as the spine of the timeline. */
export const HAPPY_PATH: OrderStatus[] = ["PENDING", "RESERVED", "CONFIRMED"];

export const ALL_STATUSES: OrderStatus[] = [
  "PENDING",
  "RESERVED",
  "CONFIRMED",
  "FAILED",
  "EXPIRED",
  "CANCELLED",
];

export const isTerminal = (status: OrderStatus): boolean => STATUS[status].terminal;

/** Statuses that mean an order is still in flight - what the stuck-order queue watches. */
export const UNFINISHED: OrderStatus[] = ["PENDING", "RESERVED"];

export function logTone(level: string): Tone {
  const value = level.toLowerCase();
  if (value === "error" || value === "critical") return "bad";
  if (value === "warning") return "pending";
  if (value === "debug") return "muted";
  return "progress";
}

export function statusTone(code: number | null): Tone {
  if (code === null) return "muted";
  if (code >= 500) return "bad";
  if (code >= 400) return "pending";
  return "good";
}
