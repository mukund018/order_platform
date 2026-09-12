/** The shop: browse the catalogue, build a cart, place the order. */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { useAsync } from "../api/hooks";
import type { Product } from "../api/types";
import { Card, Empty, ErrorBox, Loading, Pill, Stat, Swatch } from "../components/ui";
import { money } from "../lib/format";
import { useCart } from "./CartContext";

type Sort = "name" | "price-asc" | "price-desc" | "stock";

export default function Catalogue() {
  const products = useAsync(() => api.products(), []);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>("name");
  const [inStockOnly, setInStockOnly] = useState(false);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = (products.data ?? []).filter((product) => {
      if (inStockOnly && product.stock <= 0) return false;
      if (!needle) return true;
      return (
        product.name.toLowerCase().includes(needle) ||
        product.sku.toLowerCase().includes(needle)
      );
    });
    const order: Record<Sort, (a: Product, b: Product) => number> = {
      name: (a, b) => a.name.localeCompare(b.name),
      "price-asc": (a, b) => a.price_paise - b.price_paise,
      "price-desc": (a, b) => b.price_paise - a.price_paise,
      stock: (a, b) => b.stock - a.stock,
    };
    return [...rows].sort(order[sort]);
  }, [products.data, search, sort, inStockOnly]);

  const catalogue = products.data ?? [];
  const soldOut = catalogue.filter((product) => product.stock === 0).length;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Catalogue</h1>
          <p>
            Live from inventory-service, through the Redis product cache. Stock here can be
            a few seconds behind the database — that is the cache doing its job, and it is
            also what one of the Phase 3 incidents is about.
          </p>
        </div>
      </div>

      <div className="grid cols-4">
        <Card>
          <Stat label="Products" value={catalogue.length} />
        </Card>
        <Card>
          <Stat
            label="Sold out"
            value={soldOut}
            tone={soldOut > 0 ? "warn" : undefined}
            note="cannot be ordered"
          />
        </Card>
        <Card>
          <Stat
            label="Units on hand"
            value={catalogue.reduce((total, product) => total + product.stock, 0)}
          />
        </Card>
        <Card>
          <Stat
            label="Catalogue value"
            value={money(
              catalogue.reduce((total, p) => total + p.stock * p.price_paise, 0),
            )}
            note="stock × price"
          />
        </Card>
      </div>

      <Card
        flush
        title={<h2>{visible.length} products</h2>}
        actions={
          <>
            <input
              placeholder="Search name or SKU"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              style={{ width: 200 }}
            />
            <select value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
              <option value="name">Name</option>
              <option value="price-asc">Price, low to high</option>
              <option value="price-desc">Price, high to low</option>
              <option value="stock">Most stock</option>
            </select>
            <label className="row tight small muted" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={inStockOnly}
                onChange={(event) => setInStockOnly(event.target.checked)}
              />
              In stock
            </label>
          </>
        }
      >
        <div style={{ padding: 16 }}>
          {products.initial && <Loading rows={4} />}
          {products.error != null && <ErrorBox error={products.error} />}
          {!products.initial && products.error == null && visible.length === 0 && (
            <Empty>Nothing matches that search.</Empty>
          )}
          {visible.length > 0 && (
            <div className="grid products">
              {visible.map((product) => (
                <ProductCard key={product.sku} product={product} />
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

function ProductCard({ product }: { product: Product }) {
  const cart = useCart();
  const navigate = useNavigate();
  const soldOut = product.stock <= 0;
  const low = !soldOut && product.stock <= 5;

  return (
    <article className="product">
      <Swatch seed={product.sku} />
      <span className="sku">{product.sku}</span>
      <span className="name">{product.name}</span>
      <div className="row between">
        <span className="price">{money(product.price_paise)}</span>
        {soldOut ? (
          <Pill tone="muted">Sold out</Pill>
        ) : low ? (
          <Pill tone="pending">{product.stock} left</Pill>
        ) : (
          <Pill tone="good">{product.stock} in stock</Pill>
        )}
      </div>
      <button
        className={soldOut ? "" : "primary"}
        disabled={soldOut}
        onClick={() => {
          cart.add(product);
          navigate("/checkout");
        }}
      >
        {soldOut ? "Unavailable" : "Add to cart"}
      </button>
    </article>
  );
}
