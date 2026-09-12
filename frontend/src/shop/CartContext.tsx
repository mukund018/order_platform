/**
 * The cart, plus the idempotency key that belongs to it.
 *
 * The key is minted when the cart becomes non-empty and kept until the order is
 * actually placed, so a double-clicked Place Order button, a refresh mid-request or a
 * retry after a dropped connection all carry the same key and produce one order. That
 * is the client half of the contract orders-service enforces on the server half.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { CartLine, Product } from "../api/types";

interface CartApi {
  lines: CartLine[];
  count: number;
  totalPaise: number;
  idempotencyKey: string;
  add: (product: Product, qty?: number) => void;
  setQty: (sku: string, qty: number) => void;
  remove: (sku: string) => void;
  clear: () => void;
  /** Called once an order is placed, so the next cart gets a fresh key. */
  newAttempt: () => void;
}

const CartContext = createContext<CartApi | null>(null);

const newKey = () =>
  // A hex string inside the 64-character limit orders-service enforces on the header.
  (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/-/g, "");

export function CartProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<CartLine[]>([]);
  const [idempotencyKey, setKey] = useState(newKey);

  const add = useCallback((product: Product, qty = 1) => {
    setLines((current) => {
      const existing = current.find((line) => line.sku === product.sku);
      if (existing) {
        // orders-service rejects a body naming the same sku twice, so adding always
        // merges into the existing line rather than appending a second one.
        return current.map((line) =>
          line.sku === product.sku
            ? { ...line, qty: Math.min(line.qty + qty, Math.max(1, product.stock)) }
            : line,
        );
      }
      return [
        ...current,
        {
          sku: product.sku,
          name: product.name,
          unitPricePaise: product.price_paise,
          qty: Math.min(qty, Math.max(1, product.stock)),
          stock: product.stock,
        },
      ];
    });
  }, []);

  const setQty = useCallback((sku: string, qty: number) => {
    setLines((current) =>
      qty <= 0
        ? current.filter((line) => line.sku !== sku)
        : current.map((line) => (line.sku === sku ? { ...line, qty } : line)),
    );
  }, []);

  const remove = useCallback(
    (sku: string) => setLines((current) => current.filter((line) => line.sku !== sku)),
    [],
  );

  const clear = useCallback(() => setLines([]), []);
  const newAttempt = useCallback(() => setKey(newKey()), []);

  const value = useMemo<CartApi>(
    () => ({
      lines,
      count: lines.reduce((total, line) => total + line.qty, 0),
      totalPaise: lines.reduce((total, line) => total + line.qty * line.unitPricePaise, 0),
      idempotencyKey,
      add,
      setQty,
      remove,
      clear,
      newAttempt,
    }),
    [lines, idempotencyKey, add, setQty, remove, clear, newAttempt],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartApi {
  const context = useContext(CartContext);
  if (!context) throw new Error("useCart must be used inside a CartProvider");
  return context;
}
