/**
 * The order queue, with the two questions support actually gets asked:
 * "where is my order" and "why is this one stuck".
 *
 * Orders older than the expiry window that are still PENDING or RESERVED are called out
 * separately, because that is the shape of nearly every incident that touches checkout -
 * a reservation that timed out, a payment that never answered, an expiry job that is not
 * running.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import type { Order, OrderStatus } from "../api/types";
import { Card, Empty, ErrorBox, LiveBadge, Loading, Pill, Stat } from "../components/ui";
import { ago, money, moneyShort, stamp, today } from "../lib/format";
import { ALL_STATUSES, STATUS, UNFINISHED } from "../lib/status";

const PAGE = 50;
// Matches ORDER_EXPIRY_MINUTES. An order still unfinished past this should have been
// swept by the beat job, so if it is here, the job is the next thing to look at.
const STUCK_AFTER_MINUTES = 15;

export default function Orders() {
  const [status, setStatus] = useState<OrderStatus | "">("");
  const [page, setPage] = useState(0);
  const orders = usePolling(
    () => api.orders({ status: status || undefined, limit: PAGE, offset: page * PAGE }),
    5000,
    [status, page],
  );
  const report = usePolling(() => api.dailyReport(today()), 30_000, []);

  const rows = orders.data ?? [];
  const stuck = useMemo(
    () =>
      rows.filter(
        (order) =>
          UNFINISHED.includes(order.status) &&
          Date.now() - new Date(order.created_at).getTime() > STUCK_AFTER_MINUTES * 60_000,
      ),
    [rows],
  );

  const counts = useMemo(() => {
    const tally = new Map<OrderStatus, number>();
    for (const order of rows) tally.set(order.status, (tally.get(order.status) ?? 0) + 1);
    return tally;
  }, [rows]);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Orders</h1>
          <p>
            Newest first, paged on <code>(created_at, id)</code> so a burst of orders
            sharing a timestamp cannot make a row appear twice or not at all.
          </p>
        </div>
        <LiveBadge
          paused={orders.paused}
          loading={orders.loading && !orders.initial}
          onToggle={() => orders.setPaused(!orders.paused)}
        />
      </div>

      {stuck.length > 0 && (
        <div className="notice pending">
          <div>
            <div className="title">
              {stuck.length} order{stuck.length > 1 ? "s" : ""} unfinished for more than{" "}
              {STUCK_AFTER_MINUTES} minutes
            </div>
            <div className="body">
              These are past the expiry window and still holding stock. Either the beat
              job is not running or the release is failing —{" "}
              <Link to={`/ops/orders/${stuck[0]!.id}`}>start with the oldest</Link>.
            </div>
          </div>
        </div>
      )}

      <div className="grid cols-4">
        <Card>
          <Stat
            label="Today, orders"
            value={report.data?.orders ?? "—"}
            note={report.data ? `${report.data.timezone} business day` : undefined}
          />
        </Card>
        <Card>
          <Stat
            label="Today, confirmed"
            value={report.data?.confirmed_orders ?? "—"}
            tone="good"
            note={
              report.data && report.data.orders > 0
                ? `${Math.round((report.data.confirmed_orders / report.data.orders) * 100)}% of orders`
                : undefined
            }
          />
        </Card>
        <Card>
          <Stat
            label="Today, revenue"
            value={report.data ? moneyShort(report.data.revenue_paise) : "—"}
            note="confirmed orders only"
          />
        </Card>
        <Card>
          <Stat
            label="In flight, this page"
            value={UNFINISHED.reduce((total, s) => total + (counts.get(s) ?? 0), 0)}
            tone={stuck.length > 0 ? "warn" : undefined}
            note={`${stuck.length} past the expiry window`}
          />
        </Card>
      </div>

      <Card
        flush
        title={<h2>{rows.length} orders</h2>}
        actions={
          <>
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as OrderStatus | "");
                setPage(0);
              }}
            >
              <option value="">All statuses</option>
              {ALL_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {STATUS[value].label}
                </option>
              ))}
            </select>
            <button className="small" disabled={page === 0} onClick={() => setPage(page - 1)}>
              ← newer
            </button>
            <button
              className="small"
              disabled={rows.length < PAGE}
              onClick={() => setPage(page + 1)}
            >
              older →
            </button>
          </>
        }
      >
        <div className="table-scroll">
          {orders.initial && <div style={{ padding: 16 }}><Loading rows={5} /></div>}
          {orders.error != null && <div style={{ padding: 16 }}><ErrorBox error={orders.error} /></div>}
          {orders.data && rows.length === 0 && <Empty>No orders match that filter.</Empty>}
          {rows.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Order</th>
                  <th>Customer</th>
                  <th className="num">Total</th>
                  <th>Placed</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((order) => (
                  <OrderRow key={order.id} order={order} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}

function OrderRow({ order }: { order: Order }) {
  const meta = STATUS[order.status];
  const age = Date.now() - new Date(order.created_at).getTime();
  const isStuck = UNFINISHED.includes(order.status) && age > STUCK_AFTER_MINUTES * 60_000;

  return (
    <tr>
      <td>
        <Pill tone={isStuck ? "bad" : meta.tone}>{meta.label}</Pill>
      </td>
      <td>
        <Link to={`/ops/orders/${order.id}`}>
          <span className="mono">{order.id.slice(0, 8)}</span>
        </Link>
      </td>
      <td className="small muted">{order.customer_email}</td>
      <td className="num right">{money(order.total_paise)}</td>
      <td className="nowrap small faint" title={stamp(order.created_at)}>
        {ago(order.created_at)}
      </td>
      <td className="small muted">{order.failure_reason ?? "—"}</td>
    </tr>
  );
}

