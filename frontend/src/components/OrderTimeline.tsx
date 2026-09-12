/**
 * An order's `order_events` rows drawn as the state machine that produced them.
 *
 * The audit trail is the single most useful thing in this system during an incident:
 * every transition, why it happened, and the request id that caused it. Drawing it
 * against the happy path makes a wrong turn - an order that went PENDING → EXPIRED in
 * four seconds, say - visible at a glance instead of needing to be read.
 */

import type { OrderEvent, OrderStatus } from "../api/types";
import { clock, duration } from "../lib/format";
import { HAPPY_PATH, STATUS } from "../lib/status";
import { CopyId, Pill } from "./ui";

export function OrderTimeline({
  events,
  current,
  showRequestIds = false,
}: {
  events: OrderEvent[];
  current: OrderStatus;
  showRequestIds?: boolean;
}) {
  const ordered = [...events].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
  const reached = new Set(ordered.map((event) => event.to_status));

  // Steps on the happy path the order never got to, so the customer can see what is
  // still ahead of it rather than only what has happened.
  const remaining = STATUS[current].terminal
    ? []
    : HAPPY_PATH.slice(HAPPY_PATH.indexOf(current) + 1).filter((step) => !reached.has(step));

  const first = ordered[0] ? new Date(ordered[0].created_at).getTime() : 0;

  return (
    <div className="timeline">
      {ordered.map((event, index) => {
        const meta = STATUS[event.to_status];
        const isLast = index === ordered.length - 1 && remaining.length === 0;
        const elapsed = new Date(event.created_at).getTime() - first;
        return (
          <div className="node" key={`${event.to_status}-${event.created_at}-${index}`}>
            <div className="rail">
              <span
                className={`bead ${
                  meta.tone === "bad" ? "bad" : isLast ? "current" : "done"
                }`}
              />
              {!isLast && <span className="wire done" />}
            </div>
            <div className="body">
              <div className="row tight">
                <Pill tone={meta.tone}>{meta.label}</Pill>
                <span className="when">
                  {clock(event.created_at)}
                  {index > 0 && elapsed > 0 && ` · +${duration(elapsed)}`}
                </span>
              </div>
              {event.reason && <div className="why">{event.reason}</div>}
              {showRequestIds && event.request_id && (
                <div className="why small">
                  request <CopyId value={event.request_id} />
                </div>
              )}
            </div>
          </div>
        );
      })}

      {remaining.map((step, index) => (
        <div className="node" key={step}>
          <div className="rail">
            <span className="bead" />
            {index < remaining.length - 1 && <span className="wire" />}
          </div>
          <div className="body">
            <div className="row tight">
              <Pill tone="muted">{STATUS[step].label}</Pill>
              <span className="when">pending</span>
            </div>
            <div className="why">{STATUS[step].blurb}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
