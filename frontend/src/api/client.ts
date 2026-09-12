/**
 * One fetch wrapper for every call the app makes.
 *
 * The important part is `ApiError`: every service answers a failure with the same
 * envelope, and that envelope carries a `request_id`. Surfacing it in the UI is what
 * lets a customer-facing error be pasted straight into the trace viewer - which is the
 * whole point of having propagated the id through four services in the first place.
 */

import type {
  AssistantAnswer,
  DailyReport,
  ErrorEnvelope,
  ErrorsReport,
  IncidentBoard,
  Order,
  OrderDetail,
  OrderStatus,
  OrderStory,
  Overview,
  Product,
  SlowReport,
  Trace,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** A dead service and a refused request are different problems; keep them distinct. */
export class NetworkError extends Error {
  constructor(readonly url: string, cause: unknown) {
    super(`could not reach ${url}`);
    this.name = "NetworkError";
    this.cause = cause;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (cause) {
    throw new NetworkError(path, cause);
  }

  // The request id comes back on every response, success or not, so an operator can
  // trace a request that was merely slow as easily as one that failed.
  const requestId = response.headers.get("X-Request-ID");
  const body = await readJson(response);

  if (!response.ok) {
    const envelope = body as Partial<ErrorEnvelope> | null;
    const error = envelope?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN",
      error?.message ?? `${response.status} ${response.statusText}`,
      error?.request_id ?? requestId,
      error?.details,
    );
  }
  return body as T;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

const query = (params: Record<string, string | number | undefined>): string => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const api = {
  // ------------------------------------------------------------------ catalogue
  products: () => request<Product[]>("/api/inventory/products"),
  product: (sku: string) => request<Product>(`/api/inventory/products/${sku}`),
  adjustStock: (sku: string, delta: number) =>
    request<Product>(`/api/inventory/products/${sku}/stock`, {
      method: "PATCH",
      body: JSON.stringify({ delta }),
    }),

  // ------------------------------------------------------------------ orders
  placeOrder: (
    payload: { customer_email: string; items: { sku: string; qty: number }[] },
    idempotencyKey: string,
  ) =>
    request<OrderDetail>("/api/orders/orders", {
      method: "POST",
      // Generated once per checkout attempt and reused on retry, which is what makes
      // a double-click or a flaky connection produce one order instead of two.
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),
  order: (id: string) => request<OrderDetail>(`/api/orders/orders/${id}`),
  orders: (params: { status?: OrderStatus | ""; limit?: number; offset?: number } = {}) =>
    request<Order[]>(`/api/orders/orders${query(params)}`),
  cancelOrder: (id: string) =>
    request<OrderDetail>(`/api/orders/orders/${id}/cancel`, { method: "POST" }),
  dailyReport: (date: string) =>
    request<DailyReport>(`/api/orders/reports/daily${query({ date })}`),

  // ------------------------------------------------------------------ support
  overview: () => request<Overview>("/api/support/overview"),
  trace: (requestId: string) => request<Trace>(`/api/support/trace/${requestId}`),
  errors: (since: string) => request<ErrorsReport>(`/api/support/errors${query({ since })}`),
  slow: (since: string, top = 10) =>
    request<SlowReport>(`/api/support/slow${query({ since, top })}`),
  orderStory: (orderId: string) => request<OrderStory>(`/api/support/order/${orderId}`),
  incidents: () => request<IncidentBoard>("/api/support/incidents"),

  // ------------------------------------------------------------------ assistant
  ask: (query: string) =>
    request<AssistantAnswer>("/api/assistant/ask", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),
};

export function describeError(error: unknown): { title: string; detail?: string; requestId?: string } {
  if (error instanceof ApiError) {
    return { title: error.message, detail: error.code, requestId: error.requestId ?? undefined };
  }
  if (error instanceof NetworkError) {
    return {
      title: "Could not reach the service",
      detail: "It may be starting up, or stopped. Check the ops overview.",
    };
  }
  return { title: error instanceof Error ? error.message : "Something went wrong" };
}
