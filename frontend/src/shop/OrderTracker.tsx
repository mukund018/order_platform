/**
 * The customer's view of one order.
 *
 * It polls while the order is still moving and stops once it reaches a terminal state -
 * there is no point asking the server about an order that can no longer change, and a
 * shop that polls forever is a shop that shows up in the ops console as load.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { OrderTimeline } from "../components/OrderTimeline";
import { Card, CopyId, ErrorBox, Loading, Pill } from "../components/ui";
import { money, stamp } from "../lib/format";
import { STATUS, isTerminal } from "../lib/status";

export default function OrderTracker() {
  const { orderId = "" } = useParams();
  // An interval of zero is how usePolling is told to stop, which is what happens the
  // moment the order reaches a state it can never leave.
  const [settled, setSettled] = useState(false);
  const order = usePolling(() => api.order(orderId), settled ? 0 : 2000, [orderId]);
  const detail = order.data;

  useEffect(() => setSettled(false), [orderId]);
  useEffect(() => {
    if (detail && isTerminal(detail.status)) setSettled(true);
  }, [detail]);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Your order</h1>
          <p className="mono small">{orderId}</p>
        </div>
        <Link className="btn" to="/">
          Back to the shop
        </Link>
      </div>

      {order.initial && (
        <Card>
          <Loading rows={4} />
        </Card>
      )}
      {order.error != null && <ErrorBox error={order.error} />}

      {detail && (
        <div className="split">
          <Card title="Progress">
            <div className="stack tight">
              <div className="row">
                <Pill tone={STATUS[detail.status].tone}>{STATUS[detail.status].label}</Pill>
                <span className="muted">{STATUS[detail.status].blurb}</span>
                {!settled && <span className="small faint">· refreshing every 2s</span>}
              </div>
              {detail.failure_reason && (
                <div className="notice bad">
                  <div>
                    <div className="title">What went wrong</div>
                    <div className="body">{detail.failure_reason}</div>
                  </div>
                </div>
              )}
              <div style={{ marginTop: 10 }}>
                <OrderTimeline events={detail.events} current={detail.status} />
              </div>
            </div>
          </Card>

          <div className="stack">
            <Card title="Items">
              <table>
                <tbody>
                  {detail.items.map((item) => (
                    <tr key={item.sku}>
                      <td>
                        <div className="mono small faint">{item.sku}</div>
                        <div>{item.qty} ×</div>
                      </td>
                      <td className="num right">{money(item.qty * item.unit_price_paise)}</td>
                    </tr>
                  ))}
                  <tr>
                    <td>
                      <strong>Total</strong>
                    </td>
                    <td className="num right">
                      <strong>{money(detail.total_paise)}</strong>
                    </td>
                  </tr>
                </tbody>
              </table>
            </Card>

            <Card title="Details">
              <dl className="kv">
                <dt>Placed</dt>
                <dd>{stamp(detail.created_at)}</dd>
                <dt>Updated</dt>
                <dd>{stamp(detail.updated_at)}</dd>
                <dt>Email</dt>
                <dd>{detail.customer_email}</dd>
                <dt>Order id</dt>
                <dd>
                  <CopyId value={detail.id} length={16} />
                </dd>
              </dl>
              <div className="small faint" style={{ marginTop: 12 }}>
                Support can see everything that happened to this order under{" "}
                <Link to={`/ops/orders/${detail.id}`}>Ops → Orders</Link>.
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
