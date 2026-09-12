/**
 * Checkout.
 *
 * Worth knowing while reading this: orders-service answers a declined card with 201 and
 * a FAILED order, not with a 4xx. A business outcome is not a transport error. So the
 * success path here has to inspect `status` rather than assume 201 means "bought".
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, api, describeError } from "../api/client";
import type { OrderDetail } from "../api/types";
import { Card, CopyId, Empty, Pill } from "../components/ui";
import { money } from "../lib/format";
import { STATUS } from "../lib/status";
import { useCart } from "./CartContext";

export default function Checkout() {
  const cart = useCart();
  const navigate = useNavigate();
  const [email, setEmail] = useState("buyer@example.com");
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState<unknown>();
  const [placed, setPlaced] = useState<OrderDetail>();

  async function placeOrder() {
    setPlacing(true);
    setError(undefined);
    try {
      const order = await api.placeOrder(
        {
          customer_email: email.trim(),
          items: cart.lines.map((line) => ({ sku: line.sku, qty: line.qty })),
        },
        // The same key for every retry of this cart. Retrying is safe by construction.
        cart.idempotencyKey,
      );
      setPlaced(order);
      cart.clear();
      cart.newAttempt();
    } catch (cause) {
      setError(cause);
    } finally {
      setPlacing(false);
    }
  }

  if (placed) return <Placed order={placed} />;

  if (cart.lines.length === 0) {
    return (
      <div className="stack">
        <div className="page-head">
          <h1>Your cart</h1>
        </div>
        <Card>
          <Empty>
            Nothing in the cart yet. <Link to="/">Browse the catalogue</Link>.
          </Empty>
        </Card>
      </div>
    );
  }

  const inProgress = error instanceof ApiError && error.code === "ORDER_IN_PROGRESS";

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Your cart</h1>
          <p>
            Placing this order runs the whole flow: price the lines against inventory,
            reserve stock all-or-nothing, charge the card, then commit the reservation —
            compensating at every step that can fail.
          </p>
        </div>
      </div>

      <div className="split">
        <Card flush title={<h2>{cart.count} items</h2>}>
          <div style={{ padding: "4px 16px 16px" }}>
            {cart.lines.map((line) => (
              <div className="cart-line" key={line.sku}>
                <div>
                  <div style={{ fontWeight: 560 }}>{line.name}</div>
                  <div className="small faint mono">{line.sku}</div>
                </div>
                <div className="row tight" style={{ justifySelf: "end" }}>
                  <div className="qty">
                    <button onClick={() => cart.setQty(line.sku, line.qty - 1)}>−</button>
                    <span>{line.qty}</span>
                    <button
                      onClick={() => cart.setQty(line.sku, line.qty + 1)}
                      disabled={line.qty >= line.stock}
                      title={line.qty >= line.stock ? "That is all the stock there is" : ""}
                    >
                      +
                    </button>
                  </div>
                  <button className="ghost small" onClick={() => cart.remove(line.sku)}>
                    remove
                  </button>
                </div>
                <div className="small faint">{money(line.unitPricePaise)} each</div>
                <div className="num right" style={{ fontWeight: 580 }}>
                  {money(line.qty * line.unitPricePaise)}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Summary">
          <div className="stack tight">
            <label className="field">
              Email
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
              />
            </label>

            <div className="row between" style={{ marginTop: 8 }}>
              <span className="muted">Total</span>
              <strong style={{ fontSize: "1.3rem" }} className="num">
                {money(cart.totalPaise)}
              </strong>
            </div>

            <button
              className="primary"
              style={{ justifyContent: "center", marginTop: 6 }}
              disabled={placing || !email.includes("@")}
              onClick={() => void placeOrder()}
            >
              {placing && <span className="spinner" />}
              {placing ? "Placing…" : "Place order"}
            </button>

            <div className="small faint">
              Idempotency-Key <CopyId value={cart.idempotencyKey} /> — sent with the request
              so a retry can never buy this twice.
            </div>

            {error != null && (
              <div className={inProgress ? "notice pending" : "notice bad"}>
                <div>
                  <div className="title">
                    {inProgress ? "That order is already being placed" : describeError(error).title}
                  </div>
                  <div className="body">
                    {inProgress
                      ? "An identical request is still in flight. It will land on its own — nothing has been double-charged."
                      : describeError(error).detail}
                  </div>
                  {describeError(error).requestId && (
                    <div className="body small">
                      request id <CopyId value={describeError(error).requestId!} />
                    </div>
                  )}
                </div>
              </div>
            )}

            <button className="ghost small" onClick={() => navigate("/")}>
              ← keep shopping
            </button>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Placed({ order }: { order: OrderDetail }) {
  const meta = STATUS[order.status];
  const good = order.status === "CONFIRMED";

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{good ? "Order placed" : "We could not complete that order"}</h1>
      </div>
      <Card>
        <div className="stack tight">
          <div className="row">
            <Pill tone={meta.tone}>{meta.label}</Pill>
            <span className="muted">{meta.blurb}</span>
          </div>
          {order.failure_reason && (
            <div className="notice bad">
              <div>
                <div className="title">Why it failed</div>
                <div className="body">{order.failure_reason}</div>
              </div>
            </div>
          )}
          <dl className="kv" style={{ marginTop: 6 }}>
            <dt>Order</dt>
            <dd className="mono">{order.id}</dd>
            <dt>Total</dt>
            <dd className="num">{money(order.total_paise)}</dd>
            <dt>Items</dt>
            <dd>{order.items.map((item) => `${item.qty} × ${item.sku}`).join(", ")}</dd>
          </dl>
          <div className="row" style={{ marginTop: 10 }}>
            <Link className="btn" to={`/orders/${order.id}`}>
              Track this order
            </Link>
            <Link className="btn" to="/">
              Back to the shop
            </Link>
          </div>
        </div>
      </Card>
    </div>
  );
}
