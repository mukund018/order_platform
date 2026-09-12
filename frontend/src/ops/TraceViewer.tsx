/**
 * One request id, everywhere it was logged, in time order.
 *
 * This is the screen the whole request-id middleware exists for. The column that matters
 * is the gap: the time between two consecutive log lines. A request that took two
 * seconds did not spend them evenly — it spent them in one gap, and that gap names the
 * service and the step that owns the problem.
 */

import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { useAsync } from "../api/hooks";
import type { TraceStep } from "../api/types";
import { Bar, Card, CopyId, Empty, ErrorBox, Loading, Pill, Stat } from "../components/ui";
import { duration, utcStamp } from "../lib/format";
import { logTone, statusTone } from "../lib/status";

// A gap past this is worth pointing at rather than leaving the reader to scan for it.
const NOTABLE_GAP_MS = 250;

export default function TraceViewer() {
  const { requestId = "" } = useParams();
  const navigate = useNavigate();
  const [input, setInput] = useState(requestId);

  useEffect(() => setInput(requestId), [requestId]);

  const trace = useAsync(
    () => (requestId ? api.trace(requestId) : Promise.resolve(undefined)),
    [requestId],
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = input.trim();
    if (value) navigate(`/ops/trace/${value}`);
  }

  const steps = trace.data?.steps ?? [];
  const worst = steps.reduce((max, step) => Math.max(max, step.gap_ms ?? 0), 0);
  const slowest = steps.find((step) => (step.gap_ms ?? 0) === worst && worst > 0);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Trace</h1>
          <p>
            Every log line carrying one <code>X-Request-ID</code>, across orders,
            inventory, payments and the Celery worker — stitched together by
            support-service from the JSON logs.
          </p>
        </div>
      </div>

      <Card>
        <form className="row" onSubmit={submit}>
          <input
            className="mono"
            style={{ flex: 1, minWidth: 260 }}
            placeholder="Paste a request id from an error response or the errors panel"
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <button className="primary" type="submit" disabled={!input.trim()}>
            Trace
          </button>
        </form>
      </Card>

      {!requestId && (
        <Card>
          <Empty>
            Every non-2xx response from this platform carries a request id in its body and
            in the <code>X-Request-ID</code> header. Paste one above.
          </Empty>
        </Card>
      )}

      {trace.initial && requestId && (
        <Card>
          <Loading rows={6} />
        </Card>
      )}
      {trace.error != null && <ErrorBox error={trace.error} />}

      {trace.data && (
        <>
          <div className="grid cols-4">
            <Card>
              <Stat label="Total span" value={duration(trace.data.span_ms)} note="first to last line" />
            </Card>
            <Card>
              <Stat label="Log lines" value={steps.length} />
            </Card>
            <Card>
              <Stat
                label="Services touched"
                value={trace.data.services.length}
                note={trace.data.services.join(" → ")}
              />
            </Card>
            <Card>
              <Stat
                label="Longest gap"
                value={duration(worst)}
                tone={worst > 1000 ? "bad" : worst > NOTABLE_GAP_MS ? "warn" : undefined}
                note={slowest ? `before ${slowest.service}/${slowest.event}` : "evenly spread"}
              />
            </Card>
          </div>

          {trace.data.order_ids.length > 0 && (
            <Card title="Orders touched by this request">
              <div className="row tight">
                {trace.data.order_ids.map((id) => (
                  <Link key={id} className="btn small" to={`/ops/orders/${id}`}>
                    <span className="mono">{id.slice(0, 8)}</span>
                  </Link>
                ))}
              </div>
            </Card>
          )}

          <Card flush title={<h2>Timeline</h2>} actions={<CopyId value={trace.data.request_id} length={20} />}>
            <div className="table-scroll">
              {steps.map((step, index) => (
                <Step key={index} step={step} worst={worst} />
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function Step({ step, worst }: { step: TraceStep; worst: number }) {
  const gap = step.gap_ms ?? 0;
  const notable = gap >= NOTABLE_GAP_MS;

  return (
    <div className={notable ? "trace-step slow" : "trace-step"}>
      <span className="when">{utcStamp(step.timestamp).slice(11, 23)}</span>
      <span className="row tight">
        <Pill tone={logTone(step.level)} plain>
          {step.service}
        </Pill>
      </span>
      <div>
        <div className="row tight">
          <span className="event">{step.event}</span>
          {step.status_code !== null && (
            <Pill tone={statusTone(step.status_code)} plain>
              {step.status_code}
            </Pill>
          )}
          {step.duration_ms !== null && (
            <span className="small faint num">took {duration(step.duration_ms)}</span>
          )}
          {step.gap_ms !== null && step.gap_ms > 0 && (
            <span className={notable ? "gap small num" : "small faint num"}>
              +{duration(step.gap_ms)}
            </span>
          )}
        </div>
        {worst > 0 && step.gap_ms !== null && step.gap_ms > 0 && (
          <div style={{ maxWidth: 260, margin: "4px 0" }}>
            <Bar value={gap} max={worst} tone={gap > 1000 ? "bad" : notable ? "warn" : undefined} />
          </div>
        )}
        {Object.keys(step.fields).length > 0 && (
          <div className="fields">
            {Object.entries(step.fields)
              .map(([key, value]) => `${key}=${render(value)}`)
              .join("  ")}
          </div>
        )}
      </div>
    </div>
  );
}

function render(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
