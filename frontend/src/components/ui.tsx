/** The small vocabulary both halves of the app are built from. */

import { useEffect, useState, type ReactNode } from "react";

import { describeError } from "../api/client";
import { copy, shortId } from "../lib/format";
import type { Tone } from "../lib/status";

export function Card({
  title,
  actions,
  flush,
  children,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={flush ? "card flush" : "card"}>
      {(title || actions) && (
        <header className="card-head">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {actions && <div className="row tight">{actions}</div>}
        </header>
      )}
      {flush ? children : <div>{children}</div>}
    </section>
  );
}

export function Pill({
  tone,
  children,
  plain,
}: {
  tone: Tone;
  children: ReactNode;
  plain?: boolean;
}) {
  return <span className={`pill ${tone}${plain ? " plain" : ""}`}>{children}</span>;
}

export function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "good" | "bad" | "warn";
}) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <span className={tone ? `value ${tone}` : "value"}>{value}</span>
      {note && <span className="note">{note}</span>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack tight" style={{ padding: 4 }}>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton" style={{ width: `${100 - index * 12}%` }} />
      ))}
    </div>
  );
}

/**
 * Errors always show the request id when there is one. That string is the bridge from
 * "something went wrong" to the trace viewer, and hiding it would waste the entire
 * request-id chain the services maintain.
 */
export function ErrorBox({ error }: { error: unknown }) {
  const { title, detail, requestId } = describeError(error);
  return (
    <div className="notice bad">
      <div>
        <div className="title">{title}</div>
        {detail && <div className="body mono">{detail}</div>}
        {requestId && (
          <div className="body small">
            request id <CopyId value={requestId} /> — look it up under Ops → Trace
          </div>
        )}
      </div>
    </div>
  );
}

export function CopyId({ value, length = 12 }: { value: string; length?: number }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const handle = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(handle);
  }, [copied]);

  return (
    <span
      className="mono copyable"
      role="button"
      tabIndex={0}
      title={`${value} (click to copy)`}
      onClick={() => void copy(value).then(setCopied)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") void copy(value).then(setCopied);
      }}
    >
      {copied ? "copied" : shortId(value, length)}
    </span>
  );
}

export function LiveBadge({
  paused,
  onToggle,
  loading,
}: {
  paused: boolean;
  onToggle: () => void;
  loading?: boolean;
}) {
  return (
    <button className="ghost small" onClick={onToggle} title={paused ? "Resume" : "Pause"}>
      {loading ? <span className="spinner" /> : <span className={paused ? "" : "live-dot"} />}
      {paused ? "paused" : "live"}
    </button>
  );
}

/** A horizontal bar for the slow-request and trace views. */
export function Bar({ value, max, tone }: { value: number; max: number; tone?: "bad" | "warn" }) {
  const width = max > 0 ? Math.max(2, Math.min(100, (value / max) * 100)) : 2;
  return (
    <div className="bar-track">
      <div className={tone ? `bar ${tone}` : "bar"} style={{ width: `${width}%` }} />
    </div>
  );
}

/**
 * A deterministic colour per SKU, so a product keeps the same swatch between renders and
 * between sessions without anyone having to store an image.
 */
export function Swatch({ seed }: { seed: string }) {
  let hash = 0;
  for (const char of seed) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return (
    <div
      className="swatch"
      style={{
        background: `linear-gradient(135deg,
          oklch(0.55 0.11 ${hash}), oklch(0.42 0.09 ${(hash + 48) % 360}))`,
      }}
    />
  );
}
