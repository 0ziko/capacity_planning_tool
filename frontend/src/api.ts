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

/** Uzun süren hesaplar (plan revizyonu hesabı gibi) için istemci zaman aşımı. */
const API_LONG_TIMEOUT_MS = 10 * 60 * 1000;

async function request(url: string, init: RequestInit = {}, timeoutMs: number = API_TIMEOUT_MS): Promise<Response> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), timeoutMs);
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
  /** Dakikalar sürebilen hesaplar için (ör. plan revizyonu yeniden hesabı): 10 dk zaman aşımı. */
  postLong: <T>(url: string, body?: unknown) =>
    request(url, { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: body === undefined ? undefined : JSON.stringify(body) }, API_LONG_TIMEOUT_MS).then((r) => handle<T>(r)),
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
  /** Dakikalar sürebilen dosya işlemleri (ERP Excel eşitleme): 10 dk zaman aşımı. */
  uploadLong: <T>(url: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request(url, { method: "POST", headers: headers(), body: fd }, API_LONG_TIMEOUT_MS).then((r) => handle<T>(r));
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
  async download(url: string, fallbackName = "rapor.xlsx", body?: unknown) {
    const res = await fetch(url, body === undefined ? { headers: headers() } : {
      method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
    });
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
export interface Machine { required_crew_size?: number | null; id?: number; work_center_id?: number; code: string; name: string; description: string; is_active: boolean; employee_count?: number }
export interface WorkCenter {
  planning_mode: "labor" | "line"; required_crew_size: number;
  id: number; code: string; name: string; description: string; is_active: boolean; is_planned: boolean;
  capacity_unit_hours: number; default_efficient_hours: number;
  area_code: string; area_name: string; capacity_source: CapacitySource;
  planning_reserve_pct: number;
  shifts: Shift[]; machines: Machine[];
  employee_count: number; machine_employee_count: number;
  /** @deprecated Eski personel özeti; haftalık kapasite değildir. */
  capacity_headcount: number;
}
export interface Employee { id: number; code: string; name: string; work_center_id: number | null; machine_id: number | null; is_active: boolean; machine_code?: string }
export interface Item { id: number; code: string; name: string; main_group: string; sub_group: string; product_group: string; unit: string; max_wip_qty?: number | null; max_wip_days?: number | null }
/** Atıl haftaya öne çekilebilir iş (sunucu hesaplar; seçilenler iş taşıma taslağına girer). */
export interface IdleSuggestionRow {
  order_id: number; order_no: string; position_no: string; customer: string; item_code: string;
  operation_seq: number; operation_name: string; semi_finished_code: string; from_week: string;
  hours: number; qty: number; due_date: string; plan_line_id: number; eligible: boolean; fits: boolean; blocker: string;
}
export interface IdleSuggestion {
  work_center_id: number; work_center_code: string; week_start: string; capacity_hours: number; planned_hours: number;
  idle_hours: number; eligible_count: number; fits_hours: number; rows: IdleSuggestionRow[]; notes: string[];
}
export type TimeBasis = "labor_seconds_per_unit" | "machine_seconds_per_cycle" | "legacy_unspecified";
export interface OperationOut {
  line_interval_sec: number | null; planning_mode: "labor" | "line";
  id: number; seq: number; operation_name: string; work_center_id: number;
  cycle_time_sec: number; setup_time_min: number;
  time_basis: TimeBasis; crew_size: number | null; machine_cycle_time_sec: number | null;
  setup_labor_minutes: number | null; setup_machine_minutes: number | null; units_per_cycle: number;
  missing_resource_definition: boolean;
  semi_finished_code: string; wip_code?: string; primary_machine_code?: string;
  stations?: { machine_code: string; machine_name?: string; is_primary: boolean }[];
}
export interface ResourceModelStats {
  total_operations: number; legacy_unspecified: number; labor_seconds_per_unit: number;
  machine_seconds_per_cycle: number; missing_detailed_schedule_definition: number;
}
export interface BomLineOut {
  id: number;
  component_code: string;
  component_name: string;
  quantity: number;
  unit: string;
  source_wip?: string;
  branch_listing_sira?: number;
  recipe_seq?: number;
}
export interface ItemDetail extends Item {
  bom_lines: BomLineOut[];
  operations: OperationOut[];
  child_wips?: { code: string; name: string; operation_count: number }[];
}
export interface Order {
  id: number;
  order_no: string;
  position_no: string;
  customer: string;
  order_date: string | null;
  due_date: string;
  revised_due_date: string | null;
  effective_due_date: string;
  planned_end: string | null;
  market: string;
  item_id: number;
  item_code: string;
  item_name: string;
  quantity: number;
  unit_price: number;
  revenue: number;
  status: string;
  merged_into_id: number | null;
  note: string;
  plan_status: string;
  reservation_status: string;
  reserved_qty: number;
  material_status: MaterialStatus;
  material_ready_date: string | null;
  material_note: string;
  material_updated_by?: string;
  material_updated_at?: string | null;
}
export type MaterialStatus = "ready" | "expected" | "unknown";
export type MaterialPolicy = "conditional" | "strict";

export interface OrderIn {
  order_no: string;
  position_no: string;
  customer: string;
  order_date?: string | null;
  due_date: string;
  revised_due_date?: string | null;
  market?: string;
  item_code: string;
  quantity: number;
  unit_price: number;
  note: string;
  material_status?: MaterialStatus;
  material_ready_date?: string | null;
  material_note?: string;
}
export interface OrderAnalysisRow { customer: string; market: string; due_date: string; order_count: number; revenue: number }
export interface OrderAnalysisParetoRow { customer: string; revenue: number; pct: number; cum_pct: number; order_count: number }
export interface OrderAnalysis { rows: OrderAnalysisRow[]; total_revenue: number; domestic_revenue: number; export_revenue: number; by_customer: { customer: string; domestic: number; export: number; total: number }[]; pareto: OrderAnalysisParetoRow[]; period: string | null; due_from: string | null; due_to: string | null }
export type PlanMode = "due_date" | "revenue";
export type PlanningGranularity = "weekly" | "daily_detailed";
export type Placement = "asap" | "jit" | "flow";
export interface PullForwardMove { order_id: number; order_no: string; position_no: string; customer: string; item_code: string; operation_seq: number; work_center_code: string; from_week: string; to_week: string; hours: number; due_date: string }
export interface PullForwardPlan { start_week: string; weeks: number; idle_hours_total: number; idle_cells: number; moves: PullForwardMove[]; moved_hours: number }
export interface PlacementNote { kind: "jit_moved" | "wip_cap_moved" | "wip_cap_violation" | "jit_skipped" | "slip" | string; label: string; detail: string; hours: number; qty: number; order_id?: number; target_qty?: number; overtime_qty?: number; remaining_qty?: number; bottleneck?: string | null; est_finish_week?: string | null; overdue?: boolean; slip_mode?: string }

export interface CoShipmentSelection { order_no: string; position_nos: string[] | null }
export interface CoShipmentOptions { enabled: boolean; ready_before_delivery_days: number; selections: CoShipmentSelection[] }
export interface CoShipmentException { code: string; order_no: string; position_nos: string[]; target_ready_date: string; planned_ready_date: string | null; deviation_days: number | null; reason: string; suggestion: string | null }
export interface CoShipmentResult { order_no: string; position_nos: string[]; due_date: string; target_ready_date: string; planned_ready_date: string | null; completion_week: string | null; same_week_ok: boolean; on_target: boolean }

export interface AutoPlanRequest {
  missing_headcount_ack?: string | null;
  start_week: string;
  weeks: number;
  work_center_ids: number[] | null;
  replace_existing?: boolean;
  mode?: PlanMode;
  planning_granularity?: PlanningGranularity;
  material_policy?: MaterialPolicy;
  /** asap: en erken uygun hafta (varsayılan) · jit: termin − tampon gün haftasına doğru geriye kaydır */
  placement?: Placement;
  jit_buffer_days?: number;
  /** chain: hedef tarihe sığmayan adet sonraya dengeli yerleşir (zincir etkisi görünür) · defer: plana yazılmaz, raporlanır */
  slip_mode?: SlipMode;
  /** hedefi kurtarmak için fazla mesai önerilsin (onay bekleyen kapasite olarak yazılır) */
  use_overtime?: boolean;
  /** termin işleri yerleştikten sonra kalan normal kapasiteye hazırlık yarımamülü (son operasyon hariç) */
  prep_fill?: boolean;
  co_shipment?: CoShipmentOptions | null;
}
export type SlipMode = "chain" | "defer";
export interface OvertimeProposal {
  work_center_id: number; work_center_code: string; week_start: string; hours: number; added_capacity_hours: number; headcount: number;
  weekday_persons: number; weekday_days: number; weekday_hours_per_person: number; weekend_persons: number; weekend_days: number; weekend_hours_per_person: number;
  person_hours: number; efficiency_ratio: number; approved_existing_hours: number; explanation: string;
}
export interface PreflightNoRouting { item_code: string; item_name: string; order_count: number; order_nos: string[] }
export interface PreflightNoCapacity { work_center_id: number; work_center_code: string; work_center_name: string; needed_hours: number; capacity_hours: number; headcount: number; detail: string; week_start: string | null }
export interface PreflightLaborWeek { work_center_id: number; work_center_code: string; week_start: string; missing_fields: string[]; capacity_hours: number }
export interface PreflightWipIssue { kind: string; item_code: string; operation_seq: number | null; operation_name: string; wip_code: string; detail: string }
export interface DataFreshnessCheckpoint { key: string; label: string; import_kind: string; status: "ok" | "stale" | "missing"; update_source: "import" | "manual_ack" | null; last_import_at: string | null; last_import_by: string; last_data_date: string | null; confirmed_no_change_today: boolean; confirmed_no_change_at: string | null; confirmed_no_change_by: string; can_confirm_no_change: boolean; detail: string }
export interface PlanPreflightScope { action_label: string; horizon_start: string; horizon_end_inclusive: string; work_center_codes: string[]; replace_modes: string[]; lines_to_replace: number; replace_existing: boolean }
export interface PlanPreflight { missing_headcount_token: string | null; can_plan: boolean; order_count: number; no_routing: PreflightNoRouting[]; no_capacity: PreflightNoCapacity[]; missing_labor_weeks: PreflightLaborWeek[]; daily_data: DataFreshnessCheckpoint[]; today: string; needs_capacity_ack: boolean; needs_daily_data_ack: boolean; replace_scope: PlanPreflightScope }
export interface LineDizilimPreview {
  missing_sheets: string[]; bad_rows: string[]; bad_row_count: number;
  file: { washing_items: number; annealing_items: number; washing_combos: { machines: string; items: number }[] };
  operations: { washing: number; annealing: number; station_links_changed: number; units_changed: number; primary_changed: number };
  not_found: string[]; not_found_count: number; no_ops: string[]; no_ops_count: number; conflicts: string[]; conflict_count: number; missing_machines: string[];
  open_order_hours: { before: Record<string, number>; after: Record<string, number> };
}
export interface LineDizilimResult { operations: number; washing: number; annealing: number; conflict_count: number; not_found_count: number; no_ops_count: number }
export interface ErpExcelPreview {
  missing_sheets: string[];
  orders: { file_rows: number; valid_rows: number; skipped: string[]; skipped_count: number; new: number; existing: number; unknown_items: string[]; unknown_item_count: number; no_routing: string[]; no_routing_count: number; to_close: string[]; to_close_count: number; would_close_count: number };
  stock: { file_rows: number; warehouses: { code: string; rows: number; selected: boolean }[]; items_in_file: number; unknown_items: string[]; unknown_item_count: number; adjustments: number; increase: number; decrease: number; examples: string[] };
}
export interface ErpExcelResult {
  missing_sheets: string[];
  orders: { inserted: number; updated: number; closed: number; reserved: number; errors: string[]; error_count: number; skipped_count: number } | null;
  stock: { adjustments: number; increase: number; decrease: number; unknown_item_count: number; warehouses: string } | null;
}
export interface DataFreshness { today: string; needs_attention: boolean; open_order_count: number; checkpoints: DataFreshnessCheckpoint[] }
export interface PeriodRevenue { period: string; completed_revenue: number; completed_orders: number; earned_revenue: number; proportional_plan_revenue?: number; planned_shipment_revenue?: number; actual_shipment_revenue?: number; cumulative_completed: number; cumulative_earned: number }
export interface RevenueReport { conditional_orders: number; unknown_material_orders: number; start: string; end: string; total_open_revenue: number; planned_revenue: number; partial_revenue: number; unplanned_revenue: number; no_price_orders: number; weeks: PeriodRevenue[]; months: PeriodRevenue[] }
export interface PlanScenario { mode: PlanMode; label: string; created_lines: number; planned_revenue: number; on_time: number; late: number; partial: number; unplanned: number; total_lateness_days: number; utilization_pct: number; orders: OrderSchedule[]; revenue: RevenueReport }
export type CompareDiff = "same" | "rev_misses_due" | "rev_drops" | "due_drops" | "rev_earlier" | "rev_later" | "other";
export interface CompareRow { order_id: number; order_no: string; position_no: string; customer: string; item_code: string; quantity: number; revenue: number; due_date: string; due_status: OrderSchedule["plan_status"]; due_end: string | null; due_lateness: number | null; rev_status: OrderSchedule["plan_status"]; rev_end: string | null; rev_lateness: number | null; diff: CompareDiff }
export interface PlanCompare { due: PlanScenario; revenue: PlanScenario; rows: CompareRow[]; rev_misses_due: string[]; rev_drops: string[]; due_drops: string[] }
export interface PlanEvaluationKpi { name: string; numerator: number; denominator: number; value: number | null; unit: string; period_start: string; period_end: string; sample_count: number; notes: string }
export interface PlanEvaluationReport {
  benchmark_kind: "live" | "synthetic_benchmark" | "snapshot";
  input_fingerprint: string;
  revenue_heuristic_not_optimal: boolean;
  notes: string[];
  mode_comparison: Record<string, unknown>;
  delivery_kpis: PlanEvaluationKpi[];
  revenue_kpis: { planned_ship_value: number; actual_ship_value: number; partial_revenue: number; no_price_orders: number };
  reserve_pct_scenarios: { planning_reserve_pct: number; planned_hours: number; utilization_pct: number; label: string }[];
  revision_kpis: Record<string, unknown>;
  wip_summary: Record<string, unknown>;
  bottleneck_unplanned_hours: number;
  data_gaps: string[];
  backtest_data_requirements: string[];
}
export interface OrderSchedule {
  material_note: string; conditional_line_count: number; unknown_material_line_count: number;
  order_id: number; order_no: string; position_no: string; customer: string; item_code: string; item_name: string; quantity: number; unit_price: number; revenue: number; due_date: string;
  required_hours: number; planned_hours: number; coverage_pct: number; planned_start: string | null; planned_end_week: string | null; planned_end: string | null;
  completion_weeks?: { week_start: string; planned_end: string; quantity: number }[];
  last_work_center_code: string; lateness_days: number | null; slack_days?: number | null; target_date?: string | null; buffer_ok?: boolean | null; plan_status: "unplanned" | "partial" | "late" | "on_time" | "no_ops" | "covered" | "finish_unknown";
}
export interface OrderProgressOp { operation_id?: number; item_code?: string; required_qty?: number; remaining_qty?: number; operation_seq: number; operation_name: string; work_center_code: string; required_hours: number; planned_hours: number; produced_qty: number; earned_hours: number; pct: number }
export interface OrderProgress {
  production_source: "legacy" | "mes"; reserved_qty: number; shipped_qty: number; unfulfilled_qty: number; planned_share_qty: number; remaining_hours: number;
  order_id: number; order_no: string; position_no: string; customer: string; item_code: string; quantity: number; due_date: string; required_hours: number; earned_hours: number; produced_qty: number; pct: number;
  status: "not_started" | "in_progress" | "completed"; first_prod_date: string | null; last_prod_date: string | null; ops: OrderProgressOp[];
}
export interface MergeGroup { item_id: number; item_code: string; item_name: string; order_count: number; total_qty: number; earliest_due: string; latest_due: string; customers: string[]; has_progress: boolean; recommended: boolean; due_spread_days: number; tolerance_days: number; cluster_key: string; orders: Order[] }
export interface ProductionBatchOrder { order_id: number; order_no: string; position_no: string; customer: string; due_date: string; quantity: number }
export interface ProductionBatch { id: number; batch_no: string; item_id: number; item_code: string; item_name: string; due_date: string; quantity: number; note: string; status: string; orders: ProductionBatchOrder[] }
export interface Capacity { work_center_id: number; work_center_code: string; start: string; end: string; capacity_hours: number; capacity_units: number; unit_hours: number; days: { day: string; hours: number }[] }
export interface ForecastLoadDetail { order_no: string; item_code: string; hours: number }
export interface WeekLoad { week_start: string; capacity_hours: number; planning_capacity_hours: number; planned_hours: number; forecast_hours: number; firm_planned_hours: number; forecast_details: ForecastLoadDetail[]; utilization: number; actual_hours: number; standard_hour_equivalent_output?: number; actual_utilization: number; remaining_hours: number; plan_adherence_remaining_hours?: number; plan_matched_output_hours?: number; remaining_days: number; idle_hours: number; overtime_hours?: number; capacity_units: number; planned_units: number; actual_units: number }
export interface MachineWeekLoad { week_start: string; capacity_hours: number; planned_hours: number; idle_hours: number; utilization: number }
export interface MachineLoad { machine_id: number; machine_code: string; machine_name: string; weeks: MachineWeekLoad[] }
export interface WorkCenterLoad { machines?: MachineLoad[]; conditional_line_count?: number; unknown_material_line_count?: number; work_center_id: number; work_center_code: string; weeks: WeekLoad[] }
export interface MergeLoadDelta { work_center_id: number; work_center_code: string; week_start: string; before_hours: number; after_hours: number; delta_hours: number }
export interface MergeDelayRow { order_id: number; order_no: string; position_no: string; customer: string; item_code: string; due_date: string; before_end: string | null; after_end: string | null; delay_days: number; before_lateness: number | null; after_lateness: number | null }
export interface MergeImpact { merge_count: number; order_count: number; delayed_count: number; delayed_orders: MergeDelayRow[]; load_deltas: MergeLoadDelta[]; batches: { batch_no: string; item_code: string; quantity: number; order_count: number; order_nos: string[]; due_date: string }[]; note: string }
export interface GanttBar {
  material_unverified?: boolean | null; material_note?: string;
  plan_line_id: number; order_id: number; order_no: string; position_no: string;
  production_batch_id: number | null; batch_no: string; batch_order_nos: string[];
  item_code: string; semi_finished_code: string; semi_finished_name: string;
  operation_seq: number; operation_name: string; planned_start: string; planned_end: string; week_start: string;
  planned_qty: number; produced_qty: number; remaining_qty: number; planned_hours: number; earned_hours: number;
  due_date: string; status: string; last_prod_date: string | null;
}
export interface GanttData {
  work_center_id: number; work_center_code: string; range_start: string; range_end: string; as_of: string;
  timeline_days: string[]; bars: GanttBar[];
  distribution_note?: string;
}
export interface PlanBatchMember { order_id: number; order_no: string; position_no: string; customer: string; quantity: number; due_date: string }
export interface PlanLine { tag?: string; machine_id?: number | null; machine_code?: string; material_unverified?: boolean | null; material_note?: string; id: number; order_id: number; order_no: string; position_no: string; production_batch_id: number | null; batch_no: string; batch_order_nos: string[]; batch_members?: PlanBatchMember[]; customer: string; due_date: string; item_code: string; operation_id: number; operation_seq: number; operation_name?: string; semi_finished_code?: string; semi_finished_name?: string; work_center_id: number; work_center_code: string; week_start: string; planned_hours: number; planned_qty: number; mode: string; strategy: string; revision_id?: number | null }

export const REVISION_REASONS: { id: string; label: string }[] = [
  { id: "material_issue", label: "Hammadde yok / eksik / hatalı / hurda" },
  { id: "machine_down", label: "Makine arızası" },
  { id: "absenteeism", label: "Devamsızlık" },
  { id: "overtime_labor", label: "İş gücü artışı / fazla mesai" },
  { id: "vip_pull_in", label: "Hatırlı müşteri — öne çek" },
  { id: "customer_postpone", label: "Müşteri kaynaklı öteleme" },
  { id: "other", label: "Diğer" },
];

export interface PlanRevisionKpis {
  planned_hours: number;
  line_count: number;
  late: number;
  on_time: number;
  unplanned: number;
  partial: number;
}
export interface PlanRevisionChange {
  id: number;
  entity_type: string;
  entity_id: number;
  extra_key: string;
  field: string;
  old_value: string;
  new_value: string;
}
export interface PlanRevisionEvent {
  id: number;
  action: string;
  username: string;
  detail: string;
  created_at: string;
}
export interface PlanRevision {
  material_policy: MaterialPolicy;
  placement?: Placement;
  jit_buffer_days?: number;
  slip_mode?: SlipMode;
  use_overtime?: boolean;
  prep_fill?: boolean;
  id: number;
  revision_no: string;
  status: string;
  reason_codes: string[];
  note: string;
  start_week: string;
  weeks: number;
  mode: string;
  work_center_ids: number[];
  replace_manual: boolean;
  created_by: string;
  created_at: string;
  calculated_at: string | null;
  approved_by: string;
  approved_at: string | null;
  applied_at: string | null;
  rejected_by: string;
  reject_note: string;
  input_fingerprint: string;
  changes: PlanRevisionChange[];
  events: PlanRevisionEvent[];
  compare: PlanRevisionCompare | null;
  apply_message: string;
}
/** Canlı plan ile önerilen plan arasında sipariş bazlı önce/sonra satırı (sunucu hesaplar). */
export interface PlanRevisionOrderDiff {
  order_id: number;
  order_no: string;
  position_no: string;
  customer: string;
  item_code: string;
  quantity: number;
  due_date: string | null;
  due_before: string | null;
  due_after: string | null;
  end_before: string | null;
  end_after: string | null;
  delta_days: number | null;
  status_before: string;
  status_after: string;
  lateness_before: number | null;
  lateness_after: number | null;
  change_kind: string;
  change_id: number | null;
  requested: boolean;
  met: boolean | null;
  pushed: boolean;
  pulled_forward: boolean;
  newly_late: boolean;
  bumped: boolean;
}
export interface PlanRevisionDiffSummary {
  requested: number;
  met: number;
  unmet: number;
  pushed: number;
  newly_late: number;
  pulled_forward: number;
  unchanged: number;
  bumped: number;
}
/** Darboğaz haftası için fazla mesai önerisi (18:00-21:00, kişi başı ≤ 2,5 sa); sunucu hesaplar, planlamacı taslağa ekler. */
export interface OvertimeSuggestionRow {
  work_center_id: number; work_center_code: string; week_start: string;
  headcount: number; current_overtime_headcount: number; suggested_overtime_headcount: number;
  overtime_days: number; overtime_hours_per_person: number; efficiency_ratio: number;
  added_capacity_hours: number; utilization: number; shortfall_hours_before: number; shortfall_hours_after: number;
  order_nos: string[]; explanation: string;
}
export interface OvertimeSuggestion {
  revision_id: number; total_shortfall_hours: number; covered_hours: number; uncovered_hours: number;
  suggestions: OvertimeSuggestionRow[]; notes: string[]; window: string; max_hours_per_person: number;
}
export interface PlanRevisionCompare {
  baseline: PlanRevisionKpis;
  proposed: PlanRevisionKpis;
  schedule_rows: Record<string, unknown>[];
  bumped_orders?: string[];
  insert_notes?: string[];
  unplanned?: { order_no?: string; work_center_code?: string; hours?: number }[];
  order_diffs?: PlanRevisionOrderDiff[];
  diff_summary?: PlanRevisionDiffSummary;
}
export interface JobMoveOp {
  operation_id: number;
  operation_seq: number;
  operation_name: string;
  work_center_id: number;
  work_center_code: string;
  semi_finished_code: string;
  produced_qty: number;
  remaining_qty: number;
  locked: boolean;
  status: "completed" | "current" | "remaining" | string;
}
export interface JobMovePreview {
  order_id: number;
  order_no: string;
  item_code: string;
  quantity: number;
  movable_qty: number;
  current_start: string | null;
  status: string;
  ops: JobMoveOp[];
  consumed_work_centers: string[];
}
export interface LoadDetailRow { plan_line_id: number; item_code: string; item_name: string; semi_finished_code: string; operation_name: string; order_no: string; position_no: string; customer: string; batch_no: string; batch_order_nos: string[]; operation_seq: number; planned_hours: number; planned_qty: number; planned_start: string | null; planned_end: string | null; mode: string }
export interface LoadDetailParetoRow { item_code: string; hours: number; pct: number; cum_pct: number }
export interface LoadDetail { work_center_id: number; work_center_code: string; week_start: string; total_hours: number; total_qty: number; rows: LoadDetailRow[]; pareto: LoadDetailParetoRow[] }
export interface WeeklyOutputWc { work_center_id: number; work_center_code: string; total_qty: number; rows: LoadDetailRow[] }
export interface WeeklyOutput { week_start: string; week_end: string; work_centers: WeeklyOutputWc[]; total_jobs: number; total_qty: number }
export interface LeadTimeStep {
  operation_seq: number; operation_name: string; work_center_code: string; hours: number;
  start: string | null; end: string | null; start_rule: string;
  scheduled_hours: number; remaining_hours: number; status: string; reason: string;
}
export interface LeadTime {
  material_status: "ready" | "expected" | "unknown"; material_ready_date: string | null;
  material_policy: "conditional" | "strict"; material_unverified: boolean; material_note: string;
  item_code: string; quantity: number; total_hours: number;
  start: string | null; end: string | null; steps: LeadTimeStep[];
  status: string; remaining_hours: number; failure_reason: string; planning_note: string;
}
export interface ForecastSummary { material_status: string; material_ready_date: string | null; material_note: string; order_id: number; order_no: string; item_code: string; item_name: string; quantity: number; due_date: string; total_hours: number; line_count: number; week_from: string | null; week_to: string | null; created_at: string | null }
export interface Progress { work_center_id: number; work_center_code: string; week_start: string; planned_hours: number; expected_hours_to_date: number; actual_hours_to_date: number; standard_hour_equivalent_output?: number; remaining_hours: number; plan_adherence_remaining_hours?: number; remaining_days: number; working_days: number; elapsed_days: number; status: string; quality_unverified?: boolean }
export interface ImportKind { kind: string; title: string; columns: string[]; required: string[] }

// ---- Haftalık iş gücü ----
export interface WcWeek {
  planning_mode: "labor" | "line"; required_crew_size: number; line_hours_per_day: number | null; required_labor_hours: number;
  work_center_id: number; week_start: string;
  headcount: number; efficient_hours_per_person: number; working_days: number; capacity_hours: number;
  default_headcount: number; default_efficient_hours: number; default_working_days: number;
  has_override: boolean; ov_headcount: number | null; ov_efficient_hours_per_person: number | null; ov_working_days: number | null; note: string;
  overtime_headcount?: number; overtime_days?: number; overtime_hours_per_person?: number; overtime_efficiency_ratio?: number; overtime_capacity_hours?: number; base_capacity_hours?: number;
  ov_overtime_headcount?: number | null; ov_overtime_days?: number | null; ov_overtime_hours_per_person?: number | null;
  weekend_overtime_headcount?: number; weekend_overtime_days?: number; weekend_overtime_hours_per_person?: number; weekend_overtime_capacity_hours?: number;
  ov_weekend_overtime_headcount?: number | null; ov_weekend_overtime_days?: number | null; ov_weekend_overtime_hours_per_person?: number | null;
  overtime_person_hours_week?: number; overtime_person_hours_ytd?: number; overtime_legal_yearly_hours?: number; overtime_proposed?: boolean; overtime_extra_headcount?: number;
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
export interface OrderImportRowPreview { order_id: number | null; order_no: string; position_no: string; item_code: string; customer: string; order_date?: string | null; due_date: string | null; revised_due_date?: string | null; market?: string; quantity: number | null; unit_price: number | null; excel_row: number | null }
export interface OrderImportChangePreview extends OrderImportRowPreview { changes: string[] }
export interface OrderImportPreview { parse_errors: string[]; error_rows: OrderImportErrorRow[]; missing_item_codes: string[]; no_routing_item_codes?: string[]; only_in_system: OrderImportRowPreview[]; only_in_file: OrderImportRowPreview[]; updated: OrderImportChangePreview[]; unchanged_count: number; file_row_count: number; system_open_count: number }
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


export interface DailyScheduleResult {
  version_id: number;
  segments_created: number;
  skipped: { order_no: string; operation_seq: number; operation_id?: number; item_code?: string; reason: string }[];
  remaining_qty: { order_no: string; operation_seq: number; operation_id?: number; item_code?: string; remaining_qty: number; reason?: string; wip_codes?: string[] }[];
}


export const AUTO_PLAN_JOB_KEY = "kp_auto_plan_job";
export const MERGE_IMPACT_JOB_KEY = "kp_merge_impact_job";
export async function trackedMergeImpact<T>(req: object, onPhase: (phase: string) => void, active: () => boolean): Promise<T> {
  type Job = {id: string; status: string; phase: string; elapsed_seconds: number; result: T; error: string | null};
  const payload = JSON.stringify(req);
  let saved: {id: string; payload: string} | null = null;
  try { saved = JSON.parse(sessionStorage.getItem(MERGE_IMPACT_JOB_KEY) || "null"); } catch { /* stale storage */ }
  let id = saved?.payload === payload ? saved.id : "";
  if (!id) {
    id = crypto.randomUUID().replace(/-/g, "");
    sessionStorage.setItem(MERGE_IMPACT_JOB_KEY, JSON.stringify({id, payload}));
    try {
      const job = await api.post<Job>(`/api/plan/merge/impact-jobs?request_id=${id}`, req);
      id = job.id;
      sessionStorage.setItem(MERGE_IMPACT_JOB_KEY, JSON.stringify({id, payload}));
    } catch (error) {
      if (error instanceof ApiError && error.status > 0 && error.status < 500) {
        sessionStorage.removeItem(MERGE_IMPACT_JOB_KEY); throw error;
      }
    }
  }
  while (active()) {
    let job: Job;
    try { job = await api.get<Job>(`/api/plan/merge/impact-jobs/${id}`); }
    catch (error) {
      if (error instanceof ApiError && error.status === 404) sessionStorage.removeItem(MERGE_IMPACT_JOB_KEY);
      throw new Error(`${(error as Error).message} Aynı seçimle Etki analizi düğmesine yeniden basarak devam edebilirsiniz. Üretim partisi oluşturulmadı.`);
    }
    if (!active()) break;
    onPhase(`${job.phase} · ${Math.floor(job.elapsed_seconds ?? 0)} saniye`);
    if (job.status === "done" || job.status === "failed") {
      sessionStorage.removeItem(MERGE_IMPACT_JOB_KEY);
      if (job.status === "failed") throw new Error(job.error || "Analiz tamamlanamadı.");
      return job.result;
    }
    await new Promise(resolve => window.setTimeout(resolve, 1500));
  }
  throw new Error("Analiz takibi duraklatıldı; aynı seçimle devam edebilirsiniz.");
}

export const MES_IMPORT_JOB_KEY = "kp_mes_import_job";
export async function trackedMesImport<T>(file?: File, token?: string, onPhase?: (phase: string) => void): Promise<T> {
  type Job = {id: string; status: string; phase: string; elapsed_seconds: number; result: T; error: string | null};
  let id = sessionStorage.getItem(MES_IMPORT_JOB_KEY);
  if (!id) {
    if (!file || !token) throw new Error("Takip edilecek MES aktarımı yok.");
    id = crypto.randomUUID().replace(/-/g, "");
    sessionStorage.setItem(MES_IMPORT_JOB_KEY, id);
    onPhase?.("Onaylanan MES dosyası gönderiliyor…");
    try {
      await api.upload<Job>("/api/mes/import/jobs", file, {token, request_id: id});
    } catch (error) {
      if (error instanceof ApiError && error.status > 0 && error.status < 500) {
        sessionStorage.removeItem(MES_IMPORT_JOB_KEY);
        throw error;
      }
      onPhase?.("Başlatma yanıtı alınamadı; aktarım sonucu kontrol ediliyor");
    }
  }
  while (true) {
    let job: Job;
    try { job = await api.get<Job>(`/api/mes/import/jobs/${id}`); }
    catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        sessionStorage.removeItem(MES_IMPORT_JOB_KEY);
        throw new Error("MES aktarım takibi bulunamadı. Yeniden onaylamadan önce kayıtlı MES verisini kontrol edip önizlemeyi yenileyin. Aktarım otomatik tekrarlanmadı.");
      }
      throw new Error(`${(error as Error).message} Aktarım sonucu doğrulanamadı. Sayfayı yenileyerek aynı işlemin takibine dönebilirsiniz.`);
    }
    onPhase?.(`${job.phase} · ${Math.floor(job.elapsed_seconds ?? 0)} saniye`);
    if (job.status === "done" || job.status === "failed") {
      sessionStorage.removeItem(MES_IMPORT_JOB_KEY);
      if (job.status === "failed") throw new Error(job.error || "MES aktarımı tamamlanamadı.");
      return job.result;
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }
}

export async function trackedMesPreview<T>(file: File, onPhase: (phase: string) => void): Promise<T> {
  type Job = {id: string; status: string; phase: string; elapsed_seconds: number; result: T; error: string | null};
  onPhase("MES dosyası yükleniyor…");
  let job: Job;
  try {
    job = await api.upload<Job>("/api/mes/preview/jobs", file);
    while (job.status === "queued" || job.status === "running") {
      onPhase(`${job.phase} · ${Math.floor(job.elapsed_seconds)} saniye`);
      await new Promise(resolve => window.setTimeout(resolve, 1000));
      job = await api.get<Job>(`/api/mes/preview/jobs/${job.id}`);
    }
  } catch (error) {
    throw new Error(`${(error as Error).message} Önizlemeye devam etmek için aynı dosyayı tekrar seçebilirsiniz. Bu adım üretim aktarmaz.`);
  }
  if (job.status === "failed") throw new Error(job.error || "MES önizlemesi hazırlanamadı.");
  return job.result;
}

/** Plan revizyonu yeniden hesabını arka plan işi olarak başlatır ve bitene kadar durumunu izler. */
export async function trackedRevisionCalculate(revisionId: number, onPhase?: (phase: string) => void): Promise<PlanRevision> {
  type Job = { id: string; status: string; phase: string; result: PlanRevision; error: string | null; elapsed_seconds: number };
  const id = crypto.randomUUID().replace(/-/g, "");
  let job = await api.post<Job>(`/api/plan/revisions/${revisionId}/calculate/jobs?request_id=${id}`);
  let pollErrors = 0;
  while (job.status !== "done" && job.status !== "failed") {
    onPhase?.(`${job.phase} · ${Math.floor(job.elapsed_seconds ?? 0)} saniye`);
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    try {
      job = await api.get<Job>(`/api/plan/revisions/${revisionId}/calculate/jobs/${job.id}`);
      pollErrors = 0;
    } catch (error) {
      // Geçici ağ/proxy hatası (ör. sunucu yeniden başlarken) işi düşürmez; iş sunucuda sürer.
      if (error instanceof ApiError && error.status === 404) throw error;
      if (++pollErrors >= 8) throw new ApiError(0, `${(error as Error).message} İş sunucuda sürüyor olabilir; revizyonu yeniden seçerek sonucu görebilirsiniz.`);
      onPhase?.(`Bağlantı bekleniyor (${pollErrors}) · iş sunucuda sürüyor`);
    }
  }
  onPhase?.("");
  if (job.status === "failed") throw new ApiError(400, job.error || "Revizyon hesaplanamadı.");
  return job.result;
}

export async function trackedAutoPlan<T>(req?: AutoPlanRequest, onPhase?: (phase: string) => void): Promise<T> {
  type Job = { id: string; status: string; phase: string; result: T; error: string | null; elapsed_seconds: number };
  let id = sessionStorage.getItem(AUTO_PLAN_JOB_KEY);
  if (!id) {
    if (!req) throw new Error("Takip edilecek planlama yok.");
    id = crypto.randomUUID().replace(/-/g, "");
    sessionStorage.setItem(AUTO_PLAN_JOB_KEY, id);
    try {
      const job = await api.post<Job>(`/api/plan/auto/jobs?request_id=${id}`, req);
      id = job.id;
      sessionStorage.setItem(AUTO_PLAN_JOB_KEY, id);
    } catch (error) {
      if (error instanceof ApiError && error.status > 0 && error.status < 500) {
        sessionStorage.removeItem(AUTO_PLAN_JOB_KEY);
        throw error;
      }
      onPhase?.("Başlatma yanıtı alınamadı; aynı işlemin durumu kontrol ediliyor");
    }
  }
  while (true) {
    let job: Job;
    try { job = await api.get<Job>(`/api/plan/auto/jobs/${id}`); }
    catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        sessionStorage.removeItem(AUTO_PLAN_JOB_KEY);
        throw new Error("Kalıcı işlem kaydı bulunamadı. İstek sunucuya ulaşmamış veya eski sürümde başlatılmış olabilir. Yeni planlama başlatmadan önce kayıtlı planı kontrol edin; işlem otomatik tekrarlanmadı.");
      }
      throw new Error(`${(error as Error).message} Bağlantı nedeniyle sonuç henüz alınamadı; tekrar bastığınızda mevcut işin takibi sürdürülür.`);
    }
    onPhase?.(`${job.phase} · ${Math.floor(job.elapsed_seconds ?? 0)} saniye`);
    if (job.status === "done" || job.status === "failed") {
      sessionStorage.removeItem(AUTO_PLAN_JOB_KEY);
      if (job.status === "failed") throw new Error(job.error || "Planlama tamamlanamadı.");
      return job.result;
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }
}

// ---- Plan raporu (otomatik plan / revizyon onayı sonrası) ----
export interface PlanReportListItem {
  id: number; kind: "auto" | "revision" | string; revision_id: number | null; username: string; start_week: string; weeks: number;
  summary: string; created_at: string | null; planned_hours: number | null; unplanned_hours: number | null; idle_hours: number | null;
  utilization: number | null; target_met: number | null; open_orders: number | null; findings: number;
  overtime_hours?: number | null; slip_orders?: number | null; orphan_hours?: number | null;
}
export interface PlanReportWc {
  code: string; name: string; planning_mode: string; capacity_hours: number; planned_hours: number; idle_hours: number; utilization: number;
  unplanned_hours: number; unplanned_reasons: Record<string, number>; bottleneck: boolean; zero_capacity: boolean;
}
export interface PlanReport {
  id: number; created_at: string | null; generated_at: string; kind: string; username: string; revision_id: number | null;
  horizon: { start_week: string; weeks: number; work_center_count: number };
  summary: { planned_hours: number; capacity_hours: number; idle_hours: number; utilization: number; unplanned_hours: number; unplanned_orders: number; line_count: number; created: number | null; placement_notes: Record<string, number>;
    overtime_hours?: number; overtime_cells?: number; overtime_line_hours?: number; slip_line_hours?: number; prep_line_hours?: number; orphan_hours?: number; orphan_share?: number; slip_orders?: number; slip_overdue_orders?: number; overtime_orders?: number; slip_mode?: string | null };
  work_centers: PlanReportWc[];
  unplanned_reasons: { reason: string; label: string; help?: string; hours: number }[];
  top_unplanned_orders: { order_no: string; hours: number }[];
  orders: { open_orders: number; status: Record<string, number>; with_finish_estimate: number; target_met: number; overdue_before_horizon: number; avg_slack_days: number | null; delivery_buffer_days: number };
  weekly: { week_start: string; capacity_hours: number; planned_hours: number; idle_hours: number; utilization: number }[];
  transitions: { count: number; no_wait: number; avg_wait_weeks: number };
  findings: { level: "critical" | "warn" | "info" | string; code: string; title?: string; text: string }[];
  slips?: PlanReportSlip[];
  overtime?: PlanReportOvertime[];
  orphan_by_wc?: Record<string, number>;
  machines?: { work_center: string; machine_code: string; machine_name: string; capacity_hours: number; planned_hours: number; idle_hours: number; utilization: number; weekly: { week_start: string; capacity_hours: number; planned_hours: number; utilization: number }[] }[];
}
export interface PlanReportSlip {
  order_no: string; order_id: number | null; customer: string; item_code: string; due_date: string | null; target_date: string | null;
  remaining_qty: number; target_qty: number; overtime_qty: number; slipped_qty: number; slipped_hours: number; bottleneck: string | null;
  est_finish_week: string | null; planned_end: string | null; days_late: number; plan_status: string; overdue: boolean; slip_mode: string | null; detail: string | null;
  overtime_saved_weeks?: number; overtime_extra_qty?: number;
}
export interface PlanReportOvertime extends OvertimeProposal { approved: boolean; pending: boolean; person_hours_ytd: number; legal_yearly_hours: number }
export interface OvertimePendingCell { work_center_id: number; work_center_code: string; week_start: string; overtime_headcount: number; overtime_days: number | null; weekend_overtime_headcount: number; weekend_overtime_days: number | null; overtime_capacity_hours: number; person_hours_week: number }
