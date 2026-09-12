/**
 * The Phase 3 incident board.
 *
 * It reads the public half of each fault definition and any row already written into
 * incidents/INDEX.md. It deliberately shows nothing about *what* an incident is - no
 * root cause, no hint, no injected change - because the whole point is that this screen
 * is useful while an investigation is still open.
 */

import { useMemo, useState } from "react";

import { api } from "../api/client";
import { useAsync } from "../api/hooks";
import type { Incident } from "../api/types";
import { Card, Empty, ErrorBox, Loading, Pill, Stat } from "../components/ui";
import type { Tone } from "../lib/status";

const SEVERITY_TONE: Record<string, Tone> = {
  SEV1: "bad",
  SEV2: "bad",
  SEV3: "pending",
  SEV4: "muted",
};

const DIFFICULTY_NOTE: Record<string, string> = {
  guided: "hints unlimited, worked through step by step",
  hinted: "hints unlimited",
  capped: "two hint tiers, then you are on your own",
  unguided: "no hints; may have two interacting causes",
};

export default function Incidents() {
  const board = useAsync(() => api.incidents(), []);
  const [showClosed, setShowClosed] = useState(true);

  const incidents = board.data?.incidents ?? [];
  const visible = showClosed ? incidents : incidents.filter((item) => item.status !== "closed");

  const stats = useMemo(() => {
    const closed = incidents.filter((item) => item.status === "closed");
    const ttms = closed.map((item) => item.ttm_min).filter((v): v is number => v !== null);
    const scores = closed
      .map((item) => Number.parseInt(item.rca_score ?? "", 10))
      .filter((value) => Number.isFinite(value));
    return {
      closed: closed.length,
      medianTtm: median(ttms),
      meanScore: scores.length
        ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1)
        : null,
      categories: new Set(incidents.map((item) => item.category)).size,
    };
  }, [incidents]);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
          <p>
            Twelve injected production faults, each triaged, investigated, mitigated and
            written up. Run one with{" "}
            <code>python tools/chaos.py start INC-00X</code> — the root cause stays sealed
            until an RCA is submitted.
          </p>
        </div>
        <label className="row tight small muted" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showClosed}
            onChange={(event) => setShowClosed(event.target.checked)}
          />
          show closed
        </label>
      </div>

      {board.initial && (
        <Card>
          <Loading rows={5} />
        </Card>
      )}
      {board.error != null && <ErrorBox error={board.error} />}

      {board.data && (
        <>
          <div className="grid cols-4">
            <Card>
              <Stat
                label="Closed"
                value={`${stats.closed}/${board.data.total}`}
                tone={stats.closed === board.data.total ? "good" : undefined}
              />
            </Card>
            <Card>
              <Stat
                label="Median time to mitigate"
                value={stats.medianTtm === null ? "—" : `${stats.medianTtm}m`}
                note="across closed incidents"
              />
            </Card>
            <Card>
              <Stat
                label="Mean RCA score"
                value={stats.meanScore ? `${stats.meanScore}/10` : "—"}
                note="scored against the rubric"
              />
            </Card>
            <Card>
              <Stat label="Categories covered" value={stats.categories} note="target is 9 of 12" />
            </Card>
          </div>

          <Card flush title={<h2>{visible.length} incidents</h2>}>
            <div className="table-scroll">
              {visible.length === 0 && <Empty>Nothing open.</Empty>}
              {visible.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Title</th>
                      <th>Category</th>
                      <th>Difficulty</th>
                      <th>State</th>
                      <th>Sev</th>
                      <th className="num">TTA</th>
                      <th className="num">TTM</th>
                      <th className="num">Hints</th>
                      <th className="num">RCA</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((incident) => (
                      <Row key={incident.id} incident={incident} />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function Row({ incident }: { incident: Incident }) {
  const state: Tone =
    incident.status === "closed" ? "good" : incident.status === "in progress" ? "progress" : "muted";

  return (
    <tr>
      <td className="mono nowrap">{incident.id}</td>
      <td>{incident.title}</td>
      <td className="small muted nowrap">{incident.category}</td>
      <td className="small">
        <span className="tag" title={DIFFICULTY_NOTE[incident.difficulty]}>
          {incident.difficulty}
        </span>
      </td>
      <td>
        <Pill tone={state}>{incident.status}</Pill>
      </td>
      <td>
        {incident.severity ? (
          <Pill tone={SEVERITY_TONE[incident.severity] ?? "muted"}>{incident.severity}</Pill>
        ) : (
          <span className="faint">—</span>
        )}
      </td>
      <td className="num">{incident.tta_min ?? "—"}</td>
      <td className="num">{incident.ttm_min ?? "—"}</td>
      <td className="num">{incident.hints ?? "—"}</td>
      <td className="num">{incident.rca_score ?? "—"}</td>
    </tr>
  );
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? Math.round(((sorted[middle - 1] ?? 0) + (sorted[middle] ?? 0)) / 2)
    : (sorted[middle] ?? null);
}
