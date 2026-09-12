/**
 * The first screen an on-call engineer should look at.
 *
 * Three questions, in the order you ask them during an incident: is everything up, what
 * is erroring, and what is slow. Each panel answers one, and each links to the thing you
 * would do next with that answer.
 */

import { Link } from "react-router-dom";

import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import type { ServiceHealth } from "../api/types";
import { Bar, Card, CopyId, Empty, ErrorBox, LiveBadge, Loading, Pill, Stat } from "../components/ui";
import { ago, duration, stamp } from "../lib/format";
import { logTone } from "../lib/status";

const REFRESH_MS = 5000;

export default function Overview() {
  const overview = usePolling(() => api.overview(), REFRESH_MS, []);
  const errors = usePolling(() => api.errors("15m"), REFRESH_MS * 3, []);
  const slow = usePolling(() => api.slow("15m", 8), REFRESH_MS * 3, []);

  const services = overview.data?.services ?? [];
  const down = services.filter((service) => !service.live);
  const degraded = services.filter((service) => service.live && !service.ready);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Operations</h1>
          <p>
            Health straight from each service's own <code>/ready</code>, and errors and
            latency read out of the JSON logs by support-service — the same code{" "}
            <code>tools/logtool.py</code> runs on the command line.
          </p>
        </div>
        <LiveBadge
          paused={overview.paused}
          loading={overview.loading && !overview.initial}
          onToggle={() => overview.setPaused(!overview.paused)}
        />
      </div>

      {overview.error != null && <ErrorBox error={overview.error} />}

      {(down.length > 0 || degraded.length > 0) && (
        <div className={down.length > 0 ? "notice bad" : "notice pending"}>
          <div>
            <div className="title">
              {down.length > 0
                ? `${down.length} service${down.length > 1 ? "s" : ""} unreachable`
                : `${degraded.length} service${degraded.length > 1 ? "s" : ""} not ready`}
            </div>
            <div className="body">
              {down.length > 0
                ? `${down.map((s) => s.name).join(", ")} did not answer. Alerts fire after 60s.`
                : `${degraded
                    .map((s) => `${s.name} (${s.detail ?? "dependency down"})`)
                    .join(", ")} — the process is up but refusing traffic.`}
            </div>
          </div>
        </div>
      )}

      <div className="grid cols-4">
        <Card>
          <Stat
            label="Services up"
            value={`${services.filter((s) => s.ready).length}/${services.length || "—"}`}
            tone={down.length || degraded.length ? "bad" : "good"}
            note={overview.data ? `checked ${ago(overview.data.generated_at)}` : undefined}
          />
        </Card>
        <Card>
          <Stat
            label="Errors, last 15m"
            value={overview.data?.errors_last_15m ?? "—"}
            tone={(overview.data?.errors_last_15m ?? 0) > 0 ? "warn" : "good"}
            note="warning level and above"
          />
        </Card>
        <Card>
          <Stat
            label="Log lines read"
            value={(overview.data?.log_lines_read ?? 0).toLocaleString()}
            note={
              overview.data?.malformed_lines
                ? `${overview.data.malformed_lines} unparseable`
                : "all parsed"
            }
          />
        </Card>
        <Card>
          <Stat
            label="Slowest request"
            value={duration(slow.data?.records[0]?.duration_ms ?? null)}
            note="in the last 15 minutes"
          />
        </Card>
      </div>

      <div className="grid cols-2">
        <Card flush title={<h2>Services</h2>}>
          <div style={{ padding: 8 }}>
            {overview.initial && <Loading />}
            {services.map((service) => (
              <ServiceRow key={service.name} service={service} />
            ))}
          </div>
        </Card>

        <Card
          flush
          title={<h2>Errors by service and code</h2>}
          actions={<span className="small faint">last 15 min</span>}
        >
          <div className="table-scroll">
            {errors.initial && <div style={{ padding: 16 }}><Loading /></div>}
            {errors.error != null && <div style={{ padding: 16 }}><ErrorBox error={errors.error} /></div>}
            {errors.data && errors.data.groups.length === 0 && (
              <Empty>No warnings or errors in the last 15 minutes.</Empty>
            )}
            {errors.data && errors.data.groups.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Code</th>
                    <th className="num">Count</th>
                    <th>Last</th>
                    <th>Example</th>
                  </tr>
                </thead>
                <tbody>
                  {errors.data.groups.map((group) => (
                    <tr key={`${group.service}-${group.error_code}`}>
                      <td>
                        <Pill tone={logTone(group.example.level)}>{group.service}</Pill>
                      </td>
                      <td className="mono">{group.error_code}</td>
                      <td className="num">{group.count}</td>
                      <td className="nowrap faint small">{ago(group.last_seen)}</td>
                      <td className="small muted">
                        {group.example.event}
                        {group.example.request_id && (
                          <>
                            {" · "}
                            <Link to={`/ops/trace/${group.example.request_id}`}>trace</Link>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      </div>

      <Card
        flush
        title={<h2>Slowest requests</h2>}
        actions={<span className="small faint">last 15 min</span>}
      >
        <div className="table-scroll">
          {slow.initial && <div style={{ padding: 16 }}><Loading /></div>}
          {slow.data && slow.data.records.length === 0 && (
            <Empty>No completed requests logged in that window.</Empty>
          )}
          {slow.data && slow.data.records.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Service</th>
                  <th>Request</th>
                  <th style={{ width: "34%" }}>Duration</th>
                  <th className="num">ms</th>
                </tr>
              </thead>
              <tbody>
                {slow.data.records.map((record, index) => {
                  const max = slow.data!.records[0]?.duration_ms ?? 1;
                  const ms = record.duration_ms ?? 0;
                  return (
                    <tr key={`${record.request_id}-${index}`}>
                      <td className="faint small nowrap">{stamp(record.timestamp)}</td>
                      <td>
                        <span className="tag">{record.service}</span>
                      </td>
                      <td>
                        {record.request_id ? (
                          <Link to={`/ops/trace/${record.request_id}`}>
                            <CopyId value={record.request_id} />
                          </Link>
                        ) : (
                          <span className="faint">—</span>
                        )}
                      </td>
                      <td>
                        <Bar
                          value={ms}
                          max={max}
                          tone={ms > 2000 ? "bad" : ms > 1000 ? "warn" : undefined}
                        />
                      </td>
                      <td className="num">{duration(ms)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}

function ServiceRow({ service }: { service: ServiceHealth }) {
  const tone = !service.live ? "bad" : service.ready ? "good" : "pending";
  const label = !service.live ? "unreachable" : service.ready ? "ready" : "not ready";

  return (
    <div
      className="row between"
      style={{ padding: "9px 8px", borderBottom: "1px solid var(--line)" }}
    >
      <div className="row tight">
        <Pill tone={tone}>{label}</Pill>
        <strong>{service.name}</strong>
        <span className="small faint mono">{service.url}</span>
      </div>
      <div className="row tight small faint">
        {Object.entries(service.checks).map(([name, state]) => (
          <span key={name} className="tag" title={state}>
            {name}: {state.startsWith("error") ? "✕" : "✓"}
          </span>
        ))}
        {service.detail && <span className="tag">{service.detail}</span>}
        <span className="num">{duration(service.latency_ms)}</span>
      </div>
    </div>
  );
}
