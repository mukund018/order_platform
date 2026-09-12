import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { api } from "./api/client";
import { usePolling } from "./api/hooks";
import Incidents from "./ops/Incidents";
import OpsOrderDetail from "./ops/OrderDetail";
import Orders from "./ops/Orders";
import Overview from "./ops/Overview";
import TraceViewer from "./ops/TraceViewer";
import Catalogue from "./shop/Catalogue";
import { CartProvider, useCart } from "./shop/CartContext";
import Checkout from "./shop/Checkout";
import OrderTracker from "./shop/OrderTracker";

const THEME_KEY = "order-platform.theme";

export default function App() {
  return (
    <CartProvider>
      <div className="app">
        <TopBar />
        <main className="page wide">
          <Routes>
            <Route path="/" element={<Catalogue />} />
            <Route path="/checkout" element={<Checkout />} />
            <Route path="/orders/:orderId" element={<OrderTracker />} />

            <Route path="/ops" element={<Overview />} />
            <Route path="/ops/orders" element={<Orders />} />
            <Route path="/ops/orders/:orderId" element={<OpsOrderDetail />} />
            <Route path="/ops/trace" element={<TraceViewer />} />
            <Route path="/ops/trace/:requestId" element={<TraceViewer />} />
            <Route path="/ops/incidents" element={<Incidents />} />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
        <footer className="footer">
          Order Platform — FastAPI · Celery · PostgreSQL · Redis · Prometheus · Grafana ·{" "}
          <a href="/api/orders/docs" target="_blank" rel="noreferrer">
            orders API
          </a>{" "}
          ·{" "}
          <a href="http://localhost:3000" target="_blank" rel="noreferrer">
            Grafana
          </a>{" "}
          ·{" "}
          <a href="http://localhost:9090/alerts" target="_blank" rel="noreferrer">
            alerts
          </a>
        </footer>
      </div>
    </CartProvider>
  );
}

function TopBar() {
  const location = useLocation();
  const cart = useCart();
  const inOps = location.pathname.startsWith("/ops");
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (localStorage.getItem(THEME_KEY) as "dark" | "light") ?? "dark",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  return (
    <header className="topbar">
      <Link className="brand" to="/">
        <span className="dot" />
        Order Platform
      </Link>

      <div className="mode-switch">
        <NavLink to="/" className={!inOps ? "active" : ""}>
          Shop
        </NavLink>
        <NavLink to="/ops" className={inOps ? "active" : ""}>
          Ops
        </NavLink>
      </div>

      <nav className="nav">
        {inOps ? (
          <>
            <NavLink to="/ops" end>
              Overview
            </NavLink>
            <NavLink to="/ops/orders">Orders</NavLink>
            <NavLink to="/ops/trace">Trace</NavLink>
            <NavLink to="/ops/incidents">Incidents</NavLink>
            <HealthDot />
          </>
        ) : (
          <>
            <NavLink to="/" end>
              Catalogue
            </NavLink>
            <NavLink to="/checkout">Cart{cart.count > 0 ? ` · ${cart.count}` : ""}</NavLink>
          </>
        )}
        <button
          className="ghost small"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          title="Switch theme"
        >
          {theme === "dark" ? "☾" : "☀"}
        </button>
      </nav>
    </header>
  );
}

/** A single dot in the nav: green when everything is ready, red when anything is not. */
function HealthDot() {
  const overview = usePolling(() => api.overview(), 10_000, []);
  const services = overview.data?.services ?? [];
  if (services.length === 0) return null;

  const bad = services.filter((service) => !service.ready);
  const tone = bad.length === 0 ? "good" : bad.some((s) => !s.live) ? "bad" : "pending";

  return (
    <Link
      to="/ops"
      className={`pill ${tone}`}
      style={{ alignSelf: "center" }}
      title={
        bad.length === 0
          ? "All services ready"
          : bad.map((s) => `${s.name}: ${s.live ? "not ready" : "unreachable"}`).join("\n")
      }
    >
      {bad.length === 0 ? "all healthy" : `${bad.length} down`}
    </Link>
  );
}
