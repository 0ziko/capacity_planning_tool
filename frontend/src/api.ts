const TOKEN_KEY = "kp_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    setToken(null);
    window.location.href = "/login";
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const t = getToken();
  return { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...extra };
}

export function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  get: <T>(url: string) => fetch(url, { headers: headers() }).then((r) => handle<T>(r)),
  post: <T>(url: string, body?: unknown) =>
    fetch(url, { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r)),
  put: <T>(url: string, body: unknown) =>
    fetch(url, { method: "PUT", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  patch: <T>(url: string, body?: unknown) =>
    fetch(url, { method: "PATCH", headers: headers({ "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T>(url: string) => fetch(url, { method: "DELETE", headers: headers() }).then((r) => handle<T>(r)),
  upload: <T>(url: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(url, { method: "POST", headers: headers(), body: fd }).then((r) => handle<T>(r));
  },
  async login(username: string, password: string): Promise<string> {
    const fd = new URLSearchParams({ username, password });
    const res = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: fd });
    const data = await handle<{ access_token: string }>(res);
    return data.access_token;
  },
  async download(url: string, fallbackName = "rapor.xlsx") {
    const res = await fetch(url, { headers: headers() });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="?([^";]+)"?/.exec(cd);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? decodeURIComponent(m[1]) : fallbackName;
    a.click();
    URL.revokeObjectURL(a.href);
  },
};

// ---- Types ----
export type Role = "admin" | "poweruser" | "user";
export interface User { id: number; username: string; full_name: string; role: Role; is_active: boolean }
export interface Shift { id?: number; work_center_id?: number; name: string; weekdays: string; start_time: string; end_time: string; headcount: number; efficient_hours_per_person: number | null }
export type CapacitySource = "work_center" | "machines";
export interface Machine { id?: number; work_center_id?: number; code: string; name: string; description: string; is_active: boolean; employee_count?: number }
export interface WorkCenter {
  id: number; code: string; name: string; description: string; is_active: boolean; is_planned: boolean;
  capacity_unit_hours: number; default_efficient_hours: number;
  area_code: string; area_name: string; capacity_source: CapacitySource;
  shifts: Shift[]; machines: Machine[];
  employee_count: number; machine_employee_count: number; capacity_headcount: number;
}
export interface Employee { id: number; code: string; name: string; work_center_id: number | null; machine_id: number | null; is_active: boolean; machine_code?: string }
export interface Item { id: number; code: string; name: string; product_group: string; unit: string }
export interface ItemDetail extends Item { bom_lines: { id: number; component_code: string; component_name: string; quantity: number; unit: string }[]; operations: { id: number; seq: number; operation_name: string; work_center_id: number; cycle_time_sec: number; setup_time_min: number; semi_finished_code: string }[] }
export interface Order { id: number; order_no: string; customer: string; due_date: string; item_id: number; item_code: string; item_name: string; quantity: number; unit_price: number; revenue: number; status: string; merged_into_id: number | null; note: string }
export interface OrderIn { order_no: string; customer: string; due_date: string; item_code: string; quantity: number; unit_price: number; note: string }
export type PlanMode = "due_date" | "revenue";
export interface PeriodRevenue { period: string; completed_revenue: number; completed_orders: number; earned_revenue: number; cumulative_completed: number; cumulative_earned: number }
export interface RevenueReport { start: string; end: string; total_open_revenue: number; planned_revenue: number; partial_revenue: number; unplanned_revenue: number; no_price_orders: number; weeks: PeriodRevenue[]; months: PeriodRevenue[] }
export interface PlanScenario { mode: PlanMode; label: string; created_lines: number; planned_revenue: number; on_time: number; late: number; partial: number; unplanned: number; total_lateness_days: number; utilization_pct: number; orders: OrderSchedule[]; revenue: RevenueReport }
export type CompareDiff = "same" | "rev_misses_due" | "rev_drops" | "due_drops" | "rev_earlier" | "rev_later" | "other";
export interface CompareRow { order_id: number; order_no: string; customer: string; item_code: string; quantity: number; revenue: number; due_date: string; due_status: OrderSchedule["plan_status"]; due_end: string | null; due_lateness: number | null; rev_status: OrderSchedule["plan_status"]; rev_end: string | null; rev_lateness: number | null; diff: CompareDiff }
export interface PlanCompare { due: PlanScenario; revenue: PlanScenario; rows: CompareRow[]; rev_misses_due: string[]; rev_drops: string[]; due_drops: string[] }
export interface OrderSchedule {
  order_id: number; order_no: string; customer: string; item_code: string; item_name: string; quantity: number; unit_price: number; revenue: number; due_date: string;
  required_hours: number; planned_hours: number; coverage_pct: number; planned_start: string | null; planned_end_week: string | null; planned_end: string | null;
  last_work_center_code: string; lateness_days: number | null; plan_status: "unplanned" | "partial" | "late" | "on_time" | "no_ops";
}
export interface OrderProgressOp { operation_seq: number; operation_name: string; work_center_code: string; required_hours: number; planned_hours: number; produced_qty: number; earned_hours: number; pct: number }
export interface OrderProgress {
  order_id: number; order_no: string; customer: string; item_code: string; quantity: number; due_date: string; required_hours: number; earned_hours: number; produced_qty: number; pct: number;
  status: "not_started" | "in_progress" | "completed"; first_prod_date: string | null; last_prod_date: string | null; ops: OrderProgressOp[];
}
export interface MergeGroup { item_id: number; item_code: string; item_name: string; order_count: number; total_qty: number; earliest_due: string; latest_due: string; customers: string[]; has_progress: boolean; orders: Order[] }
export interface Capacity { work_center_id: number; work_center_code: string; start: string; end: string; capacity_hours: number; capacity_units: number; unit_hours: number; days: { day: string; hours: number }[] }
export interface WeekLoad { week_start: string; capacity_hours: number; planned_hours: number; utilization: number; capacity_units: number; planned_units: number }
export interface WorkCenterLoad { work_center_id: number; work_center_code: string; weeks: WeekLoad[] }
export interface PlanLine { id: number; order_id: number; order_no: string; customer: string; due_date: string; item_code: string; operation_id: number; operation_seq: number; work_center_id: number; work_center_code: string; week_start: string; planned_hours: number; planned_qty: number; mode: string; strategy: string }
export interface Progress { work_center_id: number; work_center_code: string; week_start: string; planned_hours: number; expected_hours_to_date: number; actual_hours_to_date: number; remaining_hours: number; remaining_days: number; working_days: number; elapsed_days: number; status: string }
export interface ImportKind { kind: string; title: string; columns: string[]; required: string[] }

// ---- Haftalık iş gücü ----
export interface WcWeek {
  work_center_id: number; week_start: string;
  headcount: number; efficient_hours_per_person: number; working_days: number; capacity_hours: number;
  default_headcount: number; default_efficient_hours: number; default_working_days: number;
  has_override: boolean; ov_headcount: number | null; ov_efficient_hours_per_person: number | null; ov_working_days: number | null; note: string;
}

// ---- Senaryo matrisi ----
export type RuleKind = "finish" | "cycles";
export interface Rule { rule: RuleKind; lag_cycles: number; wait_minutes: number; source: "default" | "group" | "item"; id: number | null; note: string; description: string }
export interface FlowNode { name: string; work_centers: string[]; item_count: number; cycle_time_sec: number | null; semi_finished_codes: string[] }
export interface FlowTransition { from_op: string; to_op: string; from_wip_code: string; to_wip_code: string; effective: Rule; group_rule: Rule | null; item_rule: Rule | null }
export interface FlowItem { code: string; name: string; operations: string[]; semi_finished_codes: string[]; differs: boolean; item_rules: number }
export interface Flow { product_group: string; item_code: string | null; item_name: string | null; nodes: FlowNode[]; transitions: FlowTransition[]; items: FlowItem[] }
export interface ScenarioGroup { product_group: string; item_count: number; operations: string[]; rule_count: number }
export interface RuleRow { id: number; scope: "group" | "item"; product_group: string; item_code: string | null; from_op: string; to_op: string; from_wip_code: string; to_wip_code: string; rule: RuleKind; lag_cycles: number; wait_minutes: number; note: string; description: string }

// ---- Stok & rezervasyon ----
export interface StockRow { item_id: number; item_code: string; item_name: string; product_group: string; on_hand: number; reserved: number; free: number; shipped: number; open_demand: number; open_orders: number }
export interface OrderStockRow { order_id: number; order_no: string; customer: string; due_date: string; item_id: number; item_code: string; item_name: string; quantity: number; reserved: number; shipped: number; remaining: number; status: string; planned_end: string | null }
export interface Receipt { id: number; item_id: number; item_code: string; item_name: string; receipt_date: string; quantity: number; lot: string; note: string; source: string; created_by: string }
export interface Reservation { id: number; item_id: number; item_code: string; item_name: string; order_id: number; order_no: string; customer: string; due_date: string; order_qty: number; quantity: number; source: "auto" | "manual"; note: string; created_by: string; created_at: string | null }
export interface Shipment { id: number; item_id: number; item_code: string; order_id: number; order_no: string; customer: string; ship_date: string; quantity: number; note: string; created_by: string }
export interface AutoReserveResult { created: number; reserved_qty: number; items: number; message: string }
export interface ImportResult { kind: string; inserted: number; updated: number; errors: string[] }

export function mondayOf(d: Date): string {
  const x = new Date(d);
  const day = (x.getDay() + 6) % 7;
  x.setDate(x.getDate() - day);
  return x.toISOString().slice(0, 10);
}
export function addDays(iso: string, n: number): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
export const fmt = (n: number | null | undefined, digits = 1) => (n === null || n === undefined ? "-" : n.toLocaleString("tr-TR", { maximumFractionDigits: digits }));

/** ISO hafta numarası (Pzt başlangıçlı; yılın ilk Perşembesini içeren hafta = 1). */
export function isoWeek(iso: string): { year: number; week: number } {
  const d = new Date(Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day); // haftanın Perşembesi
  const year = d.getUTCFullYear();
  const yearStart = Date.UTC(year, 0, 1);
  const week = Math.ceil(((d.getTime() - yearStart) / 86400000 + 1) / 7);
  return { year, week };
}
/** "H37" — hafta numarası etiketi (yıl farklıysa "H37/26"). */
export function weekLabel(iso: string, withYear = false): string {
  const { year, week } = isoWeek(iso);
  return withYear || year !== new Date().getFullYear() ? `H${week}/${String(year).slice(2)}` : `H${week}`;
}
/** "07.09" — kısa tarih (gün.ay). */
export const shortDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
/** "H37 · 07.09" — hafta numarası + Pazartesi tarihi. */
export const weekLong = (iso: string) => `${weekLabel(iso)} · ${shortDate(iso)}`;
