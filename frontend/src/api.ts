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

const API_TIMEOUT_MS = 20000;

async function request(url: string, init: RequestInit = {}): Promise<Response> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), API_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, "Sunucu yanıt vermedi (zaman aşımı). Backend çalışıyor mu?");
    }
    throw new ApiError(0, "Bağlantı kurulamadı. Backend (8000) ve frontend (5173) sunucularını kontrol edin.");
  } finally {
    window.clearTimeout(timer);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    setToken(null);
    if (!window.location.pathname.startsWith("/login")) {
      window.location.replace("/login");
    }
    throw new ApiError(401, "Oturum geçersiz veya süresi dolmuş");
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
  get: <T>(url: string) => request(url, { headers: headers() }).then((r) => handle<T>(r)),
  post: <T>(url: string, body?: unknown) =>
    request(url, { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r)),
  put: <T>(url: string, body: unknown) =>
    request(url, { method: "PUT", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  patch: <T>(url: string, body?: unknown) =>
    request(url, { method: "PATCH", headers: headers({ "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T>(url: string) => request(url, { method: "DELETE", headers: headers() }).then((r) => handle<T>(r)),
  upload: <T>(url: string, file: File, params?: Record<string, string | number | boolean | null | undefined>) => {
    const fd = new FormData();
    fd.append("file", file);
    const q = params ? qs(params) : "";
    return request(`${url}${q}`, { method: "POST", headers: headers(), body: fd }).then((r) => handle<T>(r));
  },
  async uploadDownload(url: string, file: File, fallbackName = "rapor.xlsx") {
    const fd = new FormData();
    fd.append("file", file);
    const res = await request(url, { method: "POST", headers: headers(), body: fd });
    if (res.status === 401) {
      setToken(null);
      window.location.href = "/login";
    }
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
export type Role = "owner" | "admin" | "poweruser" | "user";
export interface User { id: number; username: string; full_name: string; role: Role; is_active: boolean }
export interface Shift { id?: number; work_center_id?: number; name: string; weekdays: string; start_time: string; end_time: string; headcount: number; efficient_hours_per_person: number | null }
export type CapacitySource = "work_center" | "machines";
export interface Machine { id?: number; work_center_id?: number; code: string; name: string; description: string; is_active: boolean; employee_count?: number }
export interface WorkCenter {
  id: number; code: string; name: string; description: string; is_active: boolean; is_planned: boolean;
  capacity_unit_hours: number; default_efficient_hours: number;
  area_code: string; area_name: string; capacity_source: CapacitySource;
  planning_reserve_pct: number;
  shifts: Shift[]; machines: Machine[];
  employee_count: number; machine_employee_count: number; capacity_headcount: number;
}
export interface Employee { id: number; code: string; name: string; work_center_id: number | null; machine_id: number | null; is_active: boolean; machine_code?: string }
export interface Item { id: number; code: string; name: string; main_group: string; sub_group: string; product_group: string; unit: string }
export interface ItemDetail extends Item { bom_lines: { id: number; component_code: string; component_name: string; quantity: number; unit: string }[]; operations: { id: number; seq: number; operation_name: string; work_center_id: number; cycle_time_sec: number; setup_time_min: number; semi_finished_code: string }[] }
export interface Order { id: number; order_no: string; position_no: string; customer: string; due_date: string; revised_due_date: string | null; effective_due_date: string; market: string; item_id: number; item_code: string; item_name: string; quantity: number; unit_price: number; revenue: number; status: string; merged_into_id: number | null; note: string; plan_status: string; reservation_status: string; reserved_qty: number }
export interface OrderIn { order_no: string; position_no: string; customer: string; due_date: string; revised_due_date?: string | null; market?: string; item_code: string; quantity: number; unit_price: number; note: string }
export interface OrderAnalysisRow { customer: string; market: string; due_date: string; order_count: number; revenue: number }
export interface OrderAnalysisParetoRow { customer: string; revenue: number; pct: number; cum_pct: number; order_count: number }
export interface OrderAnalysis { rows: OrderAnalysisRow[]; total_revenue: number; domestic_revenue: number; export_revenue: number; by_customer: { customer: string; domestic: number; export: number; total: number }[]; pareto: OrderAnalysisParetoRow[]; period: string | null; due_from: string | null; due_to: string | null }
export type PlanMode = "due_date" | "revenue";

export interface CoShipmentSelection { order_no: string; position_nos: string[] | null }
export interface CoShipmentOptions { enabled: boolean; ready_before_delivery_days: number; selections: CoShipmentSelection[] }
export interface CoShipmentException { code: string; order_no: string; position_nos: string[]; target_ready_date: string; planned_ready_date: string | null; deviation_days: number | null; reason: string; suggestion: string | null }
export interface CoShipmentResult { order_no: string; position_nos: string[]; due_date: string; target_ready_date: string; planned_ready_date: string | null; completion_week: string | null; same_week_ok: boolean; on_target: boolean }

export interface AutoPlanRequest { start_week: string; weeks: number; work_center_ids: number[] | null; replace_existing?: boolean; mode?: PlanMode; co_shipment?: CoShipmentOptions | null }
export interface PreflightNoRouting { item_code: string; item_name: string; order_count: number; order_nos: string[] }
export interface PreflightNoCapacity { work_center_id: number; work_center_code: string; work_center_name: string; needed_hours: number; capacity_hours: number; headcount: number; detail: string }
export interface PreflightWipIssue { kind: string; item_code: string; operation_seq: number | null; operation_name: string; wip_code: string; detail: string }
export interface DataFreshnessCheckpoint { key: string; label: string; import_kind: string; status: "ok" | "stale" | "missing"; last_import_at: string | null; last_import_by: string; last_data_date: string | null; detail: string }
export interface PlanPreflight { can_plan: boolean; order_count: number; no_routing: PreflightNoRouting[]; no_capacity: PreflightNoCapacity[]; daily_data: DataFreshnessCheckpoint[]; today: string; needs_capacity_ack: boolean; needs_daily_data_ack: boolean }
export interface DataFreshness { today: string; needs_attention: boolean; open_order_count: number; checkpoints: DataFreshnessCheckpoint[] }
export interface PeriodRevenue { period: string; completed_revenue: number; completed_orders: number; earned_revenue: number; cumulative_completed: number; cumulative_earned: number }
export interface RevenueReport { start: string; end: string; total_open_revenue: number; planned_revenue: number; partial_revenue: number; unplanned_revenue: number; no_price_orders: number; weeks: PeriodRevenue[]; months: PeriodRevenue[] }
export interface PlanScenario { mode: PlanMode; label: string; created_lines: number; planned_revenue: number; on_time: number; late: number; partial: number; unplanned: number; total_lateness_days: number; utilization_pct: number; orders: OrderSchedule[]; revenue: RevenueReport }
export type CompareDiff = "same" | "rev_misses_due" | "rev_drops" | "due_drops" | "rev_earlier" | "rev_later" | "other";
export interface CompareRow { order_id: number; order_no: string; position_no: string; customer: string; item_code: string; quantity: number; revenue: number; due_date: string; due_status: OrderSchedule["plan_status"]; due_end: string | null; due_lateness: number | null; rev_status: OrderSchedule["plan_status"]; rev_end: string | null; rev_lateness: number | null; diff: CompareDiff }
export interface PlanCompare { due: PlanScenario; revenue: PlanScenario; rows: CompareRow[]; rev_misses_due: string[]; rev_drops: string[]; due_drops: string[] }
export interface OrderSchedule {
  order_id: number; order_no: string; position_no: string; customer: string; item_code: string; item_name: string; quantity: number; unit_price: number; revenue: number; due_date: string;
  required_hours: number; planned_hours: number; coverage_pct: number; planned_start: string | null; planned_end_week: string | null; planned_end: string | null;
  last_work_center_code: string; lateness_days: number | null; plan_status: "unplanned" | "partial" | "late" | "on_time" | "no_ops";
}
export interface OrderProgressOp { operation_seq: number; operation_name: string; work_center_code: string; required_hours: number; planned_hours: number; produced_qty: number; earned_hours: number; pct: number }
export interface OrderProgress {
  order_id: number; order_no: string; position_no: string; customer: string; item_code: string; quantity: number; due_date: string; required_hours: number; earned_hours: number; produced_qty: number; pct: number;
  status: "not_started" | "in_progress" | "completed"; first_prod_date: string | null; last_prod_date: string | null; ops: OrderProgressOp[];
}
export interface MergeGroup { item_id: number; item_code: string; item_name: string; order_count: number; total_qty: number; earliest_due: string; latest_due: string; customers: string[]; has_progress: boolean; recommended: boolean; due_spread_days: number; tolerance_days: number; cluster_key: string; orders: Order[] }
export interface ProductionBatchOrder { order_id: number; order_no: string; position_no: string; customer: string; due_date: string; quantity: number }
export interface ProductionBatch { id: number; batch_no: string; item_id: number; item_code: string; item_name: string; due_date: string; quantity: number; note: string; status: string; orders: ProductionBatchOrder[] }
export interface Capacity { work_center_id: number; work_center_code: string; start: string; end: string; capacity_hours: number; capacity_units: number; unit_hours: number; days: { day: string; hours: number }[] }
export interface ForecastLoadDetail { order_no: string; item_code: string; hours: number }
export interface WeekLoad { week_start: string; capacity_hours: number; planning_capacity_hours: number; planned_hours: number; forecast_hours: number; firm_planned_hours: number; forecast_details: ForecastLoadDetail[]; utilization: number; actual_hours: number; actual_utilization: number; remaining_hours: number; remaining_days: number; idle_hours: number; capacity_units: number; planned_units: number; actual_units: number }
export interface WorkCenterLoad { work_center_id: number; work_center_code: string; weeks: WeekLoad[] }
export interface MergeLoadDelta { work_center_id: number; work_center_code: string; week_start: string; before_hours: number; after_hours: number; delta_hours: number }
export interface MergeDelayRow { order_id: number; order_no: string; position_no: string; customer: string; item_code: string; due_date: string; before_end: string | null; after_end: string | null; delay_days: number; before_lateness: number | null; after_lateness: number | null }
export interface MergeImpact { merge_count: number; order_count: number; delayed_count: number; delayed_orders: MergeDelayRow[]; load_deltas: MergeLoadDelta[]; batches: { batch_no: string; item_code: string; quantity: number; order_count: number; order_nos: string[]; due_date: string }[]; note: string }
export interface GanttBar {
  plan_line_id: number; order_id: number; order_no: string; position_no: string;
  production_batch_id: number | null; batch_no: string; batch_order_nos: string[];
  item_code: string; semi_finished_code: string;
  operation_seq: number; operation_name: string; planned_start: string; planned_end: string; week_start: string;
  planned_qty: number; produced_qty: number; remaining_qty: number; planned_hours: number; earned_hours: number;
  due_date: string; status: string; last_prod_date: string | null;
}
export interface GanttData {
  work_center_id: number; work_center_code: string; range_start: string; range_end: string; as_of: string;
  timeline_days: string[]; bars: GanttBar[];
}
export interface PlanLine { id: number; order_id: number; order_no: string; position_no: string; production_batch_id: number | null; batch_no: string; batch_order_nos: string[]; customer: string; due_date: string; item_code: string; operation_id: number; operation_seq: number; work_center_id: number; work_center_code: string; week_start: string; planned_hours: number; planned_qty: number; mode: string; strategy: string }
export interface LoadDetailRow { plan_line_id: number; item_code: string; item_name: string; semi_finished_code: string; operation_name: string; order_no: string; position_no: string; customer: string; batch_no: string; batch_order_nos: string[]; operation_seq: number; planned_hours: number; planned_qty: number; planned_start: string | null; planned_end: string | null; mode: string }
export interface LoadDetailParetoRow { item_code: string; hours: number; pct: number; cum_pct: number }
export interface LoadDetail { work_center_id: number; work_center_code: string; week_start: string; total_hours: number; total_qty: number; rows: LoadDetailRow[]; pareto: LoadDetailParetoRow[] }
export interface WeeklyOutputWc { work_center_id: number; work_center_code: string; total_qty: number; rows: LoadDetailRow[] }
export interface WeeklyOutput { week_start: string; week_end: string; work_centers: WeeklyOutputWc[]; total_jobs: number; total_qty: number }
export interface LeadTimeStep { operation_seq: number; operation_name: string; work_center_code: string; hours: number; start: string; end: string; start_rule: string }
export interface LeadTime { item_code: string; quantity: number; total_hours: number; start: string; end: string; steps: LeadTimeStep[] }
export interface ForecastSummary { order_id: number; order_no: string; item_code: string; item_name: string; quantity: number; due_date: string; total_hours: number; line_count: number; week_from: string | null; week_to: string | null; created_at: string | null }
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
export interface OrderStockRow { order_id: number; order_no: string; position_no: string; customer: string; due_date: string; item_id: number; item_code: string; item_name: string; quantity: number; reserved: number; shipped: number; remaining: number; status: string; planned_end: string | null }
export interface Receipt { id: number; item_id: number; item_code: string; item_name: string; receipt_date: string; quantity: number; lot: string; note: string; source: string; created_by: string }
export interface Reservation { id: number; item_id: number; item_code: string; item_name: string; order_id: number; order_no: string; position_no: string; customer: string; due_date: string; order_qty: number; quantity: number; source: "auto" | "manual"; note: string; created_by: string; created_at: string | null }
export interface Shipment { id: number; item_id: number; item_code: string; order_id: number; order_no: string; position_no: string; customer: string; ship_date: string; quantity: number; note: string; created_by: string }
export interface AutoReserveResult { created: number; reserved_qty: number; items: number; message: string }
export interface ImportResult { kind: string; inserted: number; updated: number; removed?: number; errors: string[] }
export interface OrderImportRowPreview { order_id: number | null; order_no: string; position_no: string; item_code: string; customer: string; due_date: string | null; revised_due_date?: string | null; market?: string; quantity: number | null; unit_price: number | null; excel_row: number | null }
export interface OrderImportChangePreview extends OrderImportRowPreview { changes: string[] }
export interface OrderImportPreview { parse_errors: string[]; error_rows: OrderImportErrorRow[]; missing_item_codes: string[]; only_in_system: OrderImportRowPreview[]; only_in_file: OrderImportRowPreview[]; updated: OrderImportChangePreview[]; unchanged_count: number; file_row_count: number; system_open_count: number }
export interface OrderImportErrorRow extends OrderImportRowPreview { error: string }

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

/** ISO hafta input değeri: "2026-W37" */
export function isoWeekInputValue(mondayIso: string): string {
  const { year, week } = isoWeek(mondayIso);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

/** ISO hafta input → o haftanın Pazartesi tarihi (YYYY-MM-DD). */
export function mondayFromIsoWeek(value: string): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(value);
  if (!m) return mondayOf(new Date());
  const year = Number(m[1]);
  const week = Number(m[2]);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const dow = jan4.getUTCDay() || 7;
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - dow + 1 + (week - 1) * 7);
  return monday.toISOString().slice(0, 10);
}
