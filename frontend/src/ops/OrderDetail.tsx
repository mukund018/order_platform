/**
 * Everything the platform knows about one order, in one place.
 *
 * Two sources, side by side: the order's own audit trail from orders_db, and every log
 * line across every service that mentions its id. When those two disagree - an order
 * that says FAILED next to a payment line that says approved - that disagreement is
 * usually the incident.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, describeError } from "../api/client";
import { useAsync } from "../api/hooks";
import type { LogRecord } from "../api/types";
import { OrderTimeline } from "../components/OrderTimeline";
import { Card, CopyId, Empty, ErrorBox, Loading, Pill, Stat } from "../components/ui";
import { duration, money, stamp, utcStamp } from "../lib/format";
import { STATUS, logTone } from "../lib/status";

export default function OrderDetail() {
  const { orderId = "" } = useParams();
  const order = useAsync(() => api.order(orderId), [orderId]);
  const story = useAsync(() => api.orderStory(orderId), [orderId]);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<unknown>();

  const detail = order.data;

  async function cancel() {
    setCancelling(true);
    setCancelError(undefined);
    try {
      await api.cancelOrder(orderId);
      order.refresh();
      story.refresh();
    } catch (cause) {
      setCancelError(cause);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Order</h1>
          <p className="mono small">{orderId}</p>
        </div>
        <div className="row tight">
          <Link className="btn" to="/ops/orders">
            ← all orders
          </Link>
          {detail?.status === "CONFIRMED" && (
            <button className="danger" disabled={cancelling} onClick={() => void cancel()}>
              {cancelling && <span className="spinner" />}
              Cancel and return stock
            </button>
          )}
        </div>
      </div>

      {order.initial && (
        <Card>
          <Loading rows={4} />
        </Card>
      )}
      {order.error != null && <ErrorBox error={order.error} />}
      {cancelError != null && <ErrorBox error={cancelError} />}

      {detail && (
        <>
          <div className="grid cols-4">
            <Card>
              <Stat
                label="Status"
                value={<Pill tone={STATUS[detail.status].tone}>{STATUS[detail.status].label}</Pill>}
                note={detail.failure_reason ?? STATUS[detail.status].blurb}
              />
            </Card>
            <Card>
              <Stat label="Total" value={money(detail.total_paise)} note={`${detail.items.length} lines`} />
            </Card>
            <Card>
              <Stat
                label="Time to settle"
                value={duration(
                  new Date(detail.updated_at).getTime() - new Date(detail.created_at).getTime(),
                )}
                note="created to last transition"
              />
            </Card>
            <Card>
              <Stat label="Transitions" value={detail.events.length} note="rows in order_events" />
            </Card>
          </div>

          <div className="split">
            <div className="stack">
              <Card title="State machine">
                <OrderTimeline events={detail.events} current={detail.status} showRequestIds />
              </Card>

              <Card flush title={<h2>Log lines mentioning this order</h2>}>
                {story.initial && <div style={{ padding: 16 }}><Loading /></div>}
                {story.error != null && (
                  <Empty>
                    {describeError(story.error).title}. The log volume may have rotated, or
                    this order predates the current logs.
                  </Empty>
                )}
                {story.data && (
                  <div className="table-scroll">
                    {story.data.records.map((record, index) => (
                      <LogLine key={index} record={record} />
                    ))}
                  </div>
                )}
              </Card>
            </div>

            <div className="stack">
              <Card title="Items">
                <table>
                  <tbody>
                    {detail.items.map((item) => (
                      <tr key={item.sku}>
                        <td>
                          <div className="mono small">{item.sku}</div>
                          <div className="small faint">
                            {item.qty} × {money(item.unit_price_paise)}
                          </div>
                        </td>
                        <td className="num right">{money(item.qty * item.unit_price_paise)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>

              <Card title="Facts">
                <dl className="kv">
                  <dt>Customer</dt>
                  <dd>{detail.customer_email}</dd>
                  <dt>Created</dt>
                  <dd>{stamp(detail.created_at)}</dd>
                  <dt>Updated</dt>
                  <dd>{stamp(detail.updated_at)}</dd>
                  <dt>Order id</dt>
                  <dd>
                    <CopyId value={detail.id} length={16} />
                  </dd>
                </dl>
              </Card>

              {story.data && story.data.request_ids.length > 0 && (
                <Card title="Requests involved">
                  <div className="stack tight">
                    {story.data.request_ids.map((id) => (
                      <Link key={id} className="btn small" to={`/ops/trace/${id}`}>
                        <span className="mono">{id.slice(0, 16)}</span>
                      </Link>
                    ))}
                  </div>
                  <div className="small faint" style={{ marginTop: 10 }}>
                    An order touched by more than one request usually means a retry, or the
                    expiry job arriving while the order was still in flight.
                  </div>
                </Card>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function LogLine({ record }: { record: LogRecord }) {
  return (
    <div className="trace-step">
      <span className="when">{utcStamp(record.timestamp).slice(11, 23)}</span>
      <span>
        <Pill tone={logTone(record.level)} plain>
          {record.service}
        </Pill>
      </span>
      <div>
        <div className="row tight">
          <span className="event">{record.event}</span>
          {record.duration_ms !== null && (
            <span className="small faint num">{duration(record.duration_ms)}</span>
          )}
          {record.request_id && (
            <Link className="small mono" to={`/ops/trace/${record.request_id}`}>
              {record.request_id.slice(0, 10)}
            </Link>
          )}
        </div>
        {Object.keys(record.fields).length > 0 && (
          <div className="fields">
            {Object.entries(record.fields)
              .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`)
              .join("  ")}
          </div>
        )}
      </div>
    </div>
  );
}
