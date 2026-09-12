/**
 * The wire format, mirrored from the Pydantic models in services/<service>/app/schemas.py.
 *
 * These are hand-maintained rather than generated, and the API tests are what keep the
 * real contract honest - so if a field here ever disagrees with a service, the service
 * is right.
 */

export type OrderStatus =
  | "PENDING"
  | "RESERVED"
  | "CONFIRMED"
  | "FAILED"
  | "EXPIRED"
  | "CANCELLED";

export interface Product {
  id: string;
  sku: string;
  name: string;
  price_paise: number;
  stock: number;
  updated_at: string;
}

export interface OrderItem {
  product_id: string;
  sku: string;
  qty: number;
  unit_price_paise: number;
}

export interface OrderEvent {
  from_status: OrderStatus | null;
  to_status: OrderStatus;
  reason: string | null;
  request_id: string | null;
  created_at: string;
}

export interface Order {
  id: string;
  customer_email: string;
  status: OrderStatus;
  total_paise: number;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrderDetail extends Order {
  items: OrderItem[];
  events: OrderEvent[];
}

export interface DailyReport {
  date: string;
  timezone: string;
  window_start: string;
  window_end: string;
  orders: number;
  confirmed_orders: number;
  revenue_paise: number;
}

/** The one error body every service in the platform returns for a non-2xx. */
export interface ErrorEnvelope {
  error: { code: string; message: string; request_id: string | null; details?: unknown };
}

// --------------------------------------------------------------------- support API

export interface LogRecord {
  timestamp: string | null;
  service: string;
  level: string;
  event: string;
  request_id: string | null;
  duration_ms: number | null;
  status_code: number | null;
  order_id: string | null;
  fields: Record<string, unknown>;
}

export interface TraceStep extends LogRecord {
  /** Milliseconds since the previous step. Null on the first one. */
  gap_ms: number | null;
}

export interface Trace {
  request_id: string;
  steps: TraceStep[];
  services: string[];
  span_ms: number | null;
  order_ids: string[];
}

export interface ErrorGroup {
  service: string;
  error_code: string;
  count: number;
  first_seen: string | null;
  last_seen: string | null;
  example: LogRecord;
}

export interface ErrorsReport {
  since: string;
  cutoff: string;
  total: number;
  groups: ErrorGroup[];
}

export interface SlowReport {
  since: string;
  cutoff: string;
  records: LogRecord[];
}

export interface OrderStory {
  order_id: string;
  records: LogRecord[];
  request_ids: string[];
}

export interface ServiceHealth {
  name: string;
  url: string;
  live: boolean;
  ready: boolean;
  detail: string | null;
  latency_ms: number | null;
  checks: Record<string, string>;
}

export interface Overview {
  generated_at: string;
  services: ServiceHealth[];
  errors_last_15m: number;
  log_lines_read: number;
  malformed_lines: number;
}

export interface Incident {
  id: string;
  title: string;
  category: string;
  difficulty: string;
  status: string;
  severity: string | null;
  tta_min: number | null;
  ttm_min: number | null;
  hints: number | null;
  rca_score: string | null;
}

export interface IncidentBoard {
  incidents: Incident[];
  closed: number;
  total: number;
}

// --------------------------------------------------------------------- cart

export interface CartLine {
  sku: string;
  name: string;
  unitPricePaise: number;
  qty: number;
  stock: number;
}
