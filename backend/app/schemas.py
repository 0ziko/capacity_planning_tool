from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Auth / users ----
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORM):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    full_name: str = ""
    password: str = Field(min_length=6)
    role: str = "user"


class UserUpdate(BaseModel):
    full_name: str | None = None
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None


# ---- Work centers ----
class ShiftIn(BaseModel):
    name: str = "Gunduz"
    weekdays: str = "0,1,2,3,4"
    start_time: time = time(8, 0)
    end_time: time = time(18, 0)
    headcount: int = 0
    efficient_hours_per_person: float | None = None


class ShiftOut(ShiftIn, ORM):
    id: int
    work_center_id: int


CapacitySource = Literal["work_center", "machines"]


class MachineIn(BaseModel):
    required_crew_size: int | None = Field(default=None, ge=1, strict=True)
    code: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    is_active: bool = True


class MachineOut(MachineIn, ORM):
    id: int
    work_center_id: int
    employee_count: int = 0  # atanmis aktif personel


class MachineWeekIn(BaseModel):
    working_hours: float | None = Field(default=None, ge=0, le=168, allow_inf_nan=False)


class WorkCenterIn(BaseModel):
    planning_mode: Literal["labor", "line"] = "labor"
    required_crew_size: int = Field(default=1, ge=1, strict=True)
    code: str
    name: str
    description: str = ""
    is_active: bool = True
    is_planned: bool = False
    capacity_unit_hours: float = 10.0
    default_efficient_hours: float = 4.0
    area_code: str = ""
    area_name: str = ""
    capacity_source: CapacitySource = "work_center"
    planning_reserve_pct: float = Field(default=0.0, ge=0, le=99)


class WorkCenterOut(WorkCenterIn, ORM):
    id: int
    shifts: list[ShiftOut] = []
    machines: list[MachineOut] = []
    employee_count: int = 0  # is merkezine bagli aktif personel
    machine_employee_count: int = 0  # aktif makinelere atanmis aktif personel
    capacity_headcount: int = Field(default=0, deprecated=True, description="Eski personel kaydı özeti; kapasite için haftalık iş gücü API alanlarını kullanın.")


# ---- Employees ----
class EmployeeIn(BaseModel):
    code: str
    name: str
    work_center_id: int | None = None
    machine_id: int | None = None
    is_active: bool = True


class EmployeeOut(EmployeeIn, ORM):
    id: int
    machine_code: str = ""


# ---- Items / BOM / routing ----
class BomLineOut(ORM):
    id: int
    component_code: str
    component_name: str
    quantity: float
    unit: str
    source_wip: str = ""
    branch_listing_sira: int = 0
    recipe_seq: int = 0


class OperationStationOut(ORM):
    machine_code: str
    machine_name: str = ""
    is_primary: bool = False
    units_per_cycle: int | None = None  # istasyon konveyör dizilimi (saha tablosu)


TimeBasisLiteral = Literal["labor_seconds_per_unit", "machine_seconds_per_cycle", "legacy_unspecified"]


class OperationOut(ORM):
    line_interval_sec: float | None = None
    planning_mode: Literal["labor", "line"] = "labor"
    id: int
    seq: int
    operation_name: str
    work_center_id: int
    cycle_time_sec: float
    setup_time_min: float
    time_basis: TimeBasisLiteral = "legacy_unspecified"
    crew_size: int | None = None
    machine_cycle_time_sec: float | None = None
    setup_labor_minutes: float | None = None
    setup_machine_minutes: float | None = None
    units_per_cycle: int = 1
    missing_resource_definition: bool = False
    semi_finished_code: str = ""
    wip_code: str = ""
    primary_machine_code: str = ""
    stations: list[OperationStationOut] = []


class ConveyorRecipeIn(BaseModel):
    annealing_units: int = Field(default=1, ge=1, strict=True)
    washing_units: int = Field(default=1, ge=1, strict=True)
    apply_to_group: bool = False


class OperationPatchIn(BaseModel):
    line_interval_sec: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    time_basis: TimeBasisLiteral | None = None
    crew_size: int | None = None
    machine_cycle_time_sec: float | None = None
    setup_labor_minutes: float | None = None
    setup_machine_minutes: float | None = None
    units_per_cycle: int = Field(default=1, ge=1, strict=True)
    cycle_time_sec: float | None = None
    setup_time_min: float | None = None


class ResourceModelStatsOut(BaseModel):
    total_operations: int
    legacy_unspecified: int
    labor_seconds_per_unit: int
    machine_seconds_per_cycle: int
    missing_detailed_schedule_definition: int


class CalendarExceptionIn(BaseModel):
    resource_type: Literal["work_center", "machine"]
    resource_id: int
    cal_date: date
    start_time: time | None = None
    end_time: time | None = None
    exception_kind: Literal["holiday", "maintenance", "closed"] = "holiday"
    note: str = ""


class CalendarExceptionOut(CalendarExceptionIn, ORM):
    id: int


class ChildWipOut(BaseModel):
    code: str
    name: str
    operation_count: int = 0


class WipLimitsIn(BaseModel):
    """Yarımamül ara stok sınırı: azami adet ve/veya azami gün (boş = sınır yok)."""

    max_wip_qty: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_wip_days: int | None = Field(default=None, ge=0, le=365)


class ItemOut(ORM):
    id: int
    code: str
    name: str
    main_group: str = ""
    sub_group: str = ""
    product_group: str
    unit: str
    max_wip_qty: float | None = None
    max_wip_days: int | None = None


class ItemDetail(ItemOut):
    bom_lines: list[BomLineOut] = []
    operations: list[OperationOut] = []
    child_wips: list[ChildWipOut] = []


# ---- Orders ----
MaterialStatus = Literal["ready", "expected", "unknown"]
MaterialPolicy = Literal["conditional", "strict"]


class OrderIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=64)
    position_no: str = ""
    customer: str = ""
    order_date: date | None = None
    due_date: date
    revised_due_date: date | None = None
    market: str = "domestic"  # domestic / export
    item_code: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit_price: float = Field(default=0.0, ge=0)
    note: str = ""
    material_status: MaterialStatus = "unknown"
    material_ready_date: date | None = None
    material_note: str = ""


class OrderOut(ORM):
    id: int
    order_no: str
    position_no: str = ""
    customer: str
    order_date: date | None = None
    due_date: date
    revised_due_date: date | None = None
    effective_due_date: date | None = None
    planned_end: date | None = None  # kapasite planindan son operasyon bitisi
    market: str = "domestic"
    item_id: int
    item_code: str = ""
    item_name: str = ""
    quantity: float
    unit_price: float = 0.0
    revenue: float = 0.0  # miktar x birim fiyat
    status: str
    merged_into_id: int | None = None
    note: str = ""
    plan_status: str = ""  # unplanned / partial / late / on_time / no_ops / finish_unknown / closed
    reservation_status: str = ""  # none / partial / full
    reserved_qty: float = 0.0
    material_status: str = "unknown"
    material_ready_date: date | None = None
    material_note: str = ""
    material_updated_by: str = ""
    material_updated_at: datetime | None = None


class OrderAnalysisRow(BaseModel):
    customer: str
    market: str
    due_date: date
    order_count: int
    revenue: float


class OrderAnalysisParetoRow(BaseModel):
    customer: str
    revenue: float
    pct: float
    cum_pct: float
    order_count: int = 0


class OrderAnalysisOut(BaseModel):
    rows: list[OrderAnalysisRow]
    total_revenue: float
    domestic_revenue: float
    export_revenue: float
    by_customer: list[dict]  # {customer, domestic, export, total}
    pareto: list[OrderAnalysisParetoRow] = []
    period: str | None = None
    due_from: date | None = None
    due_to: date | None = None


class CompletionWeek(BaseModel):
    week_start: date
    planned_end: date
    quantity: float


class OrderScheduleOut(BaseModel):
    """Plan sonucuna gore siparisin tahmini uretim bitisi."""

    order_id: int
    material_note: str = ""
    conditional_line_count: int = 0
    unknown_material_line_count: int = 0
    order_no: str
    position_no: str = ""
    customer: str
    item_code: str
    item_name: str = ""
    quantity: float
    unit_price: float = 0.0
    revenue: float = 0.0
    due_date: date
    required_hours: float  # secili/planlanan is merkezlerindeki toplam ihtiyac
    planned_hours: float
    coverage_pct: float
    planned_start: date | None = None  # ilk plan haftasi (Pazartesi)
    planned_end_week: date | None = None  # son plan haftasi (Pazartesi)
    planned_end: date | None = None  # tahmini bitis gunu
    completion_weeks: list[CompletionWeek] = []
    last_work_center_code: str = ""
    lateness_days: int | None = None  # + gec, - erken
    slack_days: int | None = None  # termine kalan gun = etkin termin - tahmini bitis (+ erken, - gec)
    target_date: date | None = None  # hedef bitis = etkin termin - teslim tamponu (2 gun)
    buffer_ok: bool | None = None  # tahmini bitis hedef tarihi gecmiyor
    plan_status: str  # unplanned / partial / late / on_time / no_ops / finish_unknown


class OrderProgressOp(BaseModel):
    operation_id: int | None = None
    item_code: str = ""
    required_qty: float | None = None
    remaining_qty: float | None = None
    operation_seq: int
    operation_name: str
    work_center_code: str
    semi_finished_code: str = ""
    required_hours: float
    planned_hours: float
    produced_qty: float
    earned_hours: float
    pct: float


class OrderProgressOut(BaseModel):
    production_source: str = "legacy"
    reserved_qty: float = 0.0
    shipped_qty: float = 0.0
    unfulfilled_qty: float = 0.0
    planned_share_qty: float = 0.0
    remaining_hours: float = 0.0
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    item_code: str
    quantity: float
    due_date: date
    required_hours: float
    earned_hours: float
    produced_qty: float  # son operasyondan cikan miktar
    pct: float
    status: str  # not_started / in_progress / completed
    first_prod_date: date | None = None
    last_prod_date: date | None = None
    ops: list[OrderProgressOp] = []


class MergeGroup(BaseModel):
    """Geriye uyumluluk: uretim partisi birlestirme onerisi."""

    item_id: int
    item_code: str
    item_name: str
    order_count: int
    total_qty: float
    earliest_due: date
    latest_due: date
    customers: list[str]
    has_progress: bool
    recommended: bool = True
    due_spread_days: int = 0
    tolerance_days: int = 0
    cluster_key: str = ""
    orders: list[OrderOut]


ProductionBatchSuggestion = MergeGroup  # alias


class MergePreviewGroup(BaseModel):
    order_ids: list[int]


class MergeDelayRow(BaseModel):
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    item_code: str
    due_date: date
    before_end: date | None = None
    after_end: date | None = None
    delay_days: int = 0
    before_lateness: int | None = None
    after_lateness: int | None = None


class MergeLoadDelta(BaseModel):
    work_center_id: int
    work_center_code: str
    week_start: date
    before_hours: float
    after_hours: float
    delta_hours: float


class MergeImpactRequest(BaseModel):
    merge_groups: list[MergePreviewGroup]
    start_week: date
    weeks: int = 12
    work_center_ids: list[int] | None = None
    mode: Literal["due_date", "revenue"] = "due_date"


class MergeImpactOut(BaseModel):
    merge_count: int
    order_count: int
    delayed_count: int
    delayed_orders: list[MergeDelayRow]
    load_deltas: list[MergeLoadDelta]
    batches: list[dict]
    note: str = ""


class ProductionBatchOrderOut(BaseModel):
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    due_date: date
    quantity: float


class ProductionBatchOut(BaseModel):
    id: int
    batch_no: str
    item_id: int
    item_code: str
    item_name: str
    due_date: date
    quantity: float
    note: str
    status: str
    orders: list[ProductionBatchOrderOut]


class ProductionBatchCreate(BaseModel):
    order_ids: list[int] = Field(min_length=2)
    batch_no: str | None = None
    due_date: date | None = None
    note: str | None = None


class MergeRequest(BaseModel):
    """Geriye uyumluluk: uretim partisi olusturma istegi."""

    order_ids: list[int] = Field(min_length=2)
    order_no: str | None = None  # batch_no olarak kullanilir
    due_date: date | None = None
    customer: str | None = None  # yoksayilir (siparisler ayri kalir)
    note: str | None = None


# ---- Capacity ----
class CapacityDay(BaseModel):
    day: date
    hours: float


class CapacityOut(BaseModel):
    work_center_id: int
    work_center_code: str
    start: date
    end: date
    capacity_hours: float
    capacity_units: float
    unit_hours: float
    days: list[CapacityDay]


# ---- Requirements ----
class RequirementLine(BaseModel):
    work_center_id: int
    work_center_code: str
    item_code: str
    operation_seq: int
    operation_name: str
    quantity: float
    hours: float  # brut is gucu ihtiyaci (saat)
    remaining_hours: float = 0.0  # kalan planlanacak is (saat)


class RequirementQuery(BaseModel):
    item_codes: list[str] | None = None
    work_center_ids: list[int] | None = None
    quantities: dict[str, float] | None = None  # item_code -> qty (verilmezse siparislerden)
    due_from: date | None = None
    due_to: date | None = None


# ---- Planning ----
PlanMode = Literal["due_date", "revenue"]
SlipMode = Literal["chain", "defer"]
PlanningGranularity = Literal["weekly", "daily_detailed"]
# asap: en erken · jit: termine yakın · flow: akış (bitiş en erken = maksimum çıktı, öncüller ardıla yaslanır = minimum ara stok)
Placement = Literal["asap", "jit", "flow"]


class CoShipmentSelection(BaseModel):
    order_no: str
    position_nos: list[str] | None = None  # None => siparisin tum pozlari


class CoShipmentOptions(BaseModel):
    enabled: bool = False
    ready_before_delivery_days: int = Field(default=3, ge=0, le=365)
    selections: list[CoShipmentSelection] = []


class CoShipmentExceptionOut(BaseModel):
    code: str
    order_no: str
    position_nos: list[str]
    target_ready_date: date
    planned_ready_date: date | None = None
    deviation_days: int | None = None
    reason: str
    suggestion: str | None = None


class CoShipmentResultOut(BaseModel):
    order_no: str
    position_nos: list[str]
    due_date: date
    target_ready_date: date
    planned_ready_date: date | None = None
    completion_week: date | None = None
    same_week_ok: bool
    on_target: bool


class AutoPlanRequest(BaseModel):
    missing_headcount_ack: str | None = None
    start_week: date  # herhangi bir gun; pazartesiye yuvarlanir
    weeks: int = 12
    work_center_ids: list[int] | None = None  # None => is_planned olanlar
    replace_existing: bool = True
    mode: PlanMode = "due_date"  # due_date: termine gore; revenue: ufuk icinde maksimum ciro
    planning_granularity: PlanningGranularity = "weekly"
    material_policy: MaterialPolicy = "conditional"
    # asap: en erken uygun hafta (mevcut davranis); jit: son operasyon "termin - tampon gun" haftasina,
    # onculler ardila dogru geriye kaydirilir (kapasite izin verdigi olcude).
    placement: Placement = "flow"
    jit_buffer_days: int = Field(default=2, ge=0, le=60)
    # Kayan adet: chain = hedef tarihe sığmayan adet sonraya dengeli yerleşir (zincir etkisi görünür);
    # defer = plana yazılmaz, tahmini bitişle raporlanır. use_overtime: hedefi kurtarmak için fazla mesai önerilsin.
    slip_mode: SlipMode = "chain"
    use_overtime: bool = True
    # Termin işleri yerleştikten sonra kalan normal kapasiteye, bitmiş ürüne dönüşemeyen siparişlerin yarımamülleri (son operasyon hariç) 'hazırlık' olarak yazılır.
    prep_fill: bool = True
    co_shipment: CoShipmentOptions | None = None  # null / enabled=false => mevcut akis


class PreflightNoRouting(BaseModel):
    item_code: str
    item_name: str
    order_count: int
    order_nos: list[str]


class PreflightNoCapacity(BaseModel):
    work_center_id: int
    work_center_code: str
    work_center_name: str
    needed_hours: float
    capacity_hours: float
    headcount: int
    detail: str
    week_start: date | None = None


class PreflightLaborWeek(BaseModel):
    work_center_id: int
    work_center_code: str
    week_start: date
    missing_fields: list[str]
    capacity_hours: float


class PreflightWipIssue(BaseModel):
    kind: str  # legacy
    item_code: str
    operation_seq: int | None = None
    operation_name: str = ""
    wip_code: str = ""
    detail: str


class DataFreshnessCheckpoint(BaseModel):
    key: str
    label: str
    import_kind: str
    status: str  # ok | stale | missing
    update_source: str | None = None  # import | manual_ack
    last_import_at: str | None = None
    last_import_by: str = ""
    last_data_date: str | None = None
    confirmed_no_change_today: bool = False
    confirmed_no_change_at: str | None = None
    confirmed_no_change_by: str = ""
    can_confirm_no_change: bool = False
    detail: str


class PlanPreflightScopeOut(BaseModel):
    action_label: str = "Seçili ufku yeniden planla"
    horizon_start: date
    horizon_end_inclusive: date
    work_center_codes: list[str]
    replace_modes: list[str]
    lines_to_replace: int
    replace_existing: bool


class PlanPreflightOut(BaseModel):
    missing_headcount_token: str | None = None
    can_plan: bool
    order_count: int
    no_routing: list[PreflightNoRouting]
    no_capacity: list[PreflightNoCapacity]
    missing_labor_weeks: list[PreflightLaborWeek] = Field(default_factory=list)
    daily_data: list[DataFreshnessCheckpoint]
    today: str
    needs_capacity_ack: bool
    needs_daily_data_ack: bool
    replace_scope: PlanPreflightScopeOut


class DataFreshnessOut(BaseModel):
    today: str
    needs_attention: bool
    open_order_count: int
    checkpoints: list[DataFreshnessCheckpoint]


class PlanCompareRequest(BaseModel):
    material_policy: MaterialPolicy = "conditional"
    start_week: date
    weeks: int = 12
    work_center_ids: list[int] | None = None
    slip_mode: SlipMode = "chain"
    use_overtime: bool = True
    prep_fill: bool = True


class PlanEvaluationRequest(AutoPlanRequest):
    benchmark_kind: Literal["live", "synthetic_benchmark", "snapshot"] = "live"
    production_as_of: date | None = None
    include_reserve_benchmark: bool = False
    snapshot_payload: dict | None = None


class PeriodRevenue(BaseModel):
    period: str  # hafta: Pazartesi tarihi (YYYY-MM-DD); ay: YYYY-MM
    completed_revenue: float  # o donemde tamamlanan (son operasyonu biten) siparislerin cirosu
    completed_orders: int
    earned_revenue: float  # planlanan saat payina gore oransal ciro (oransal plan degeri)
    proportional_plan_revenue: float = 0.0  # earned_revenue ile ayni; ayri etiket
    planned_shipment_revenue: float = 0.0  # planlanan sevk (termin haftasi bazli acik siparis ciro tahmini)
    actual_shipment_revenue: float = 0.0  # gerceklesen sevk ciro
    cumulative_completed: float
    cumulative_earned: float


class RevenueOut(BaseModel):
    conditional_orders: int = 0
    unknown_material_orders: int = 0
    start: date
    end: date
    total_open_revenue: float  # tum acik siparisler
    planned_revenue: float  # ufuk icinde tamamlanan siparisler
    partial_revenue: float  # kismen planlanan (ufka sigmayan) siparisler
    unplanned_revenue: float  # hic planlanmayan
    no_price_orders: int  # fiyati 0 olan siparis sayisi
    weeks: list[PeriodRevenue]
    months: list[PeriodRevenue]


class PlanScenario(BaseModel):
    mode: PlanMode
    label: str
    created_lines: int
    planned_revenue: float
    on_time: int
    late: int
    partial: int
    unplanned: int
    total_lateness_days: int
    utilization_pct: float  # ufuk icinde planlanan saat / kapasite
    orders: list[OrderScheduleOut]
    revenue: RevenueOut


class CompareOrderRow(BaseModel):
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    item_code: str
    quantity: float
    revenue: float
    due_date: date
    due_status: str
    due_end: date | None
    due_lateness: int | None
    rev_status: str
    rev_end: date | None
    rev_lateness: int | None
    diff: str  # same / rev_misses_due / rev_drops / due_drops / rev_earlier / rev_later / other


class PlanCompareOut(BaseModel):
    due: PlanScenario
    revenue: PlanScenario
    rows: list[CompareOrderRow]
    rev_misses_due: list[str]  # ciro planinda termini kacan siparisler (termin planinda uygun)
    rev_drops: list[str]  # ciro planinin ufuk disina attigi / disarida biraktigi siparisler
    due_drops: list[str]  # termin planinin ufka sigdiramadigi (ciro planinda sigan) siparisler


class ManualPlanLineIn(BaseModel):
    machine_id: int | None = None
    order_id: int
    operation_id: int
    week_start: date
    planned_hours: float | None = None  # None => tum operasyon saati
    planned_qty: float | None = None


class PlanBatchMember(BaseModel):
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str = ""
    quantity: float
    due_date: date


class PlanLineOut(ORM):
    tag: str = ""  # overtime | slip | prep
    machine_code: str = ""
    machine_id: int | None = None
    material_unverified: bool | None = None
    material_note: str = ""
    id: int
    order_id: int
    order_no: str = ""
    position_no: str = ""
    production_batch_id: int | None = None
    batch_no: str = ""
    batch_order_nos: list[str] = []
    batch_members: list[PlanBatchMember] = []
    customer: str = ""
    due_date: date | None = None
    item_code: str = ""
    operation_id: int
    operation_seq: int = 0
    operation_name: str = ""
    work_center_id: int
    work_center_code: str = ""
    week_start: date
    planned_hours: float
    planned_qty: float
    semi_finished_code: str = ""
    semi_finished_name: str = ""
    mode: str
    strategy: str = ""
    revision_id: int | None = None


class WeekLoad(BaseModel):
    week_start: date
    capacity_hours: float
    planning_capacity_hours: float = 0.0
    planned_hours: float
    forecast_hours: float = 0.0
    firm_planned_hours: float = 0.0
    forecast_details: list["ForecastLoadDetail"] = []
    utilization: float  # plan / planlanabilir kapasite
    actual_hours: float = 0.0  # geriye uyumluluk: standard_hour_equivalent_output
    standard_hour_equivalent_output: float = 0.0
    actual_utilization: float = 0.0  # standart saat ciktisi / kapasite
    remaining_hours: float = 0.0  # geriye uyumluluk: plan_adherence_remaining_hours
    plan_adherence_remaining_hours: float = 0.0
    plan_matched_output_hours: float = 0.0
    remaining_days: float = 0.0  # kalan saat / gunluk verimli kapasite
    idle_hours: float = 0.0  # planlanabilir kapasite - plan (atil)
    overtime_hours: float = 0.0  # kapasitenin fazla mesaiden gelen kismi (verimli saat)
    capacity_units: float
    planned_units: float
    actual_units: float = 0.0


class ForecastLoadDetail(BaseModel):
    order_no: str
    item_code: str
    hours: float


class MachineWeekLoad(BaseModel):
    week_start: date
    capacity_hours: float
    planned_hours: float
    idle_hours: float
    utilization: float


class MachineLoad(BaseModel):
    machine_id: int
    machine_code: str
    machine_name: str = ""
    weeks: list[MachineWeekLoad]


class WorkCenterLoad(BaseModel):
    machines: list[MachineLoad] = []  # hat (line) merkezlerinde istasyon bazlı doluluk
    conditional_line_count: int = 0
    unknown_material_line_count: int = 0
    work_center_id: int
    work_center_code: str
    weeks: list[WeekLoad]


class GanttBar(BaseModel):
    material_unverified: bool | None = None
    material_note: str = ""
    plan_line_id: int
    order_id: int
    order_no: str
    position_no: str = ""
    production_batch_id: int | None = None
    batch_no: str = ""
    batch_order_nos: list[str] = []
    item_code: str
    semi_finished_code: str = ""
    semi_finished_name: str = ""
    operation_seq: int
    operation_name: str
    planned_start: date
    planned_end: date
    week_start: date
    planned_qty: float
    produced_qty: float
    remaining_qty: float
    planned_hours: float
    earned_hours: float
    due_date: date
    status: str  # planned / in_progress / completed
    last_prod_date: date | None = None


class PlanSegmentOut(ORM):
    id: int
    order_id: int
    operation_id: int
    production_batch_id: int | None = None
    work_center_id: int
    machine_id: int
    machine_code: str = ""
    segment_kind: str
    start_at: datetime
    end_at: datetime
    good_qty: float
    crew_size: int
    is_locked: bool = False


class GanttOut(BaseModel):
    work_center_id: int
    work_center_code: str
    range_start: date
    range_end: date
    as_of: date
    timeline_days: list[date]
    bars: list[GanttBar]
    segments: list[PlanSegmentOut] = []
    distribution_note: str = "Haftalik plandan yaklasik gun dagilimi; kesin gunluk cizelge degildir."


class MaterialPlanningInputs(BaseModel):
    material_status: Literal["ready", "expected", "unknown"] = "unknown"
    material_ready_date: date | None = None
    material_policy: Literal["conditional", "strict"] = "conditional"


class LeadTimeRequest(MaterialPlanningInputs):
    item_code: str
    quantity: float
    start: date
    work_center_ids: list[int] | None = None
    operation_seqs: list[int] | None = None
    horizon_days: int = 730


class LeadTimeStep(BaseModel):
    operation_seq: int
    operation_name: str
    work_center_code: str
    hours: float
    start: str | None = None
    end: str | None = None
    start_rule: str = ""
    scheduled_hours: float = 0.0
    remaining_hours: float = 0.0
    status: str = "scheduled"  # scheduled | insufficient_capacity | horizon_exceeded
    reason: str = ""


class LeadTimeOut(MaterialPlanningInputs):
    material_unverified: bool = False
    material_note: str = ""
    item_code: str
    quantity: float
    total_hours: float
    start: str | None = None
    end: str | None = None
    steps: list[LeadTimeStep]
    status: str = "complete"  # complete | partial | infeasible
    remaining_hours: float = 0.0
    failure_reason: str = ""
    planning_note: str = (
        "Kapasiteye gore yaklasik termin; kaynak araligi rezervasyonu kurulana kadar kesin teslim tarihi degildir."
    )


class ForecastFromLeadTimeIn(MaterialPlanningInputs):
    item_code: str
    quantity: float
    label: str = ""
    steps: list[LeadTimeStep]
    status: str = "complete"


class ForecastSummaryOut(BaseModel):
    material_status: str = "unknown"
    material_ready_date: date | None = None
    material_note: str = ""
    order_id: int
    order_no: str
    item_code: str
    item_name: str
    quantity: float
    due_date: date
    total_hours: float
    line_count: int
    week_from: date | None = None
    week_to: date | None = None
    created_at: datetime | None = None


class LoadDetailRow(BaseModel):
    plan_line_id: int = 0
    item_code: str
    item_name: str = ""
    semi_finished_code: str = ""
    operation_name: str = ""
    order_no: str = ""
    position_no: str = ""
    customer: str = ""
    batch_no: str = ""
    batch_order_nos: list[str] = []
    operation_seq: int = 0
    planned_hours: float
    planned_qty: float
    planned_start: date | None = None
    planned_end: date | None = None
    mode: str = ""


class LoadDetailParetoRow(BaseModel):
    item_code: str
    hours: float
    pct: float
    cum_pct: float


class LoadDetailOut(BaseModel):
    work_center_id: int
    work_center_code: str
    week_start: date
    total_hours: float
    total_qty: float
    rows: list[LoadDetailRow]
    pareto: list[LoadDetailParetoRow]


class WeeklyOutputWc(BaseModel):
    work_center_id: int
    work_center_code: str
    total_qty: float
    rows: list[LoadDetailRow]


class WeeklyOutputOut(BaseModel):
    week_start: date
    week_end: date
    work_centers: list[WeeklyOutputWc]
    total_jobs: int
    total_qty: float


# ---- Progress ----
class ProgressOut(BaseModel):
    work_center_id: int
    work_center_code: str
    week_start: date
    planned_hours: float
    expected_hours_to_date: float
    actual_hours_to_date: float  # standard_hour_equivalent_output (geriye uyumlu ad)
    standard_hour_equivalent_output: float = 0.0
    remaining_hours: float  # plan_adherence_remaining_hours
    plan_adherence_remaining_hours: float = 0.0
    remaining_days: float
    working_days: int
    elapsed_days: int
    status: str  # ahead / on_track / behind
    quality_unverified: bool = False


# ---- Imports ----
class ImportResult(BaseModel):
    kind: str
    inserted: int
    updated: int
    removed: int = 0
    errors: list[str]


class OrderImportRowPreview(BaseModel):
    order_id: int | None = None
    order_no: str
    position_no: str = ""
    item_code: str
    customer: str = ""
    order_date: date | None = None
    due_date: date | None = None
    revised_due_date: date | None = None
    market: str = "domestic"
    quantity: float | None = None
    unit_price: float | None = None
    excel_row: int | None = None


class OrderImportChangePreview(OrderImportRowPreview):
    changes: list[str] = []


class OrderImportErrorRow(OrderImportRowPreview):
    error: str = ""


class OrderImportPreview(BaseModel):
    parse_errors: list[str]
    error_rows: list[OrderImportErrorRow] = []
    only_in_system: list[OrderImportRowPreview]
    only_in_file: list[OrderImportRowPreview]
    updated: list[OrderImportChangePreview]
    unchanged_count: int
    file_row_count: int
    system_open_count: int
    missing_item_codes: list[str] = []
    no_routing_item_codes: list[str] = []  # rota tanımı eksik olduğu için aktarımı reddedilen ürün kodları


# ---- Haftalik is gucu (WorkCenterWeek) ----
class WcWeekIn(BaseModel):
    line_hours_per_day: float | None = Field(default=None, ge=0, le=24, allow_inf_nan=False)
    headcount: int | None = None
    efficient_hours_per_person: float | None = None
    working_days: int | None = Field(default=None, ge=0, le=7)
    note: str = ""
    # Fazla mesai (18:00-21:00): kişi, gün (None => çalışma günleri), kişi başı nominal saat (≤ 2,5)
    overtime_headcount: int | None = Field(default=None, ge=0)
    overtime_days: int | None = Field(default=None, ge=0, le=7)
    overtime_hours_per_person: float | None = Field(default=None, ge=0, le=2.5, allow_inf_nan=False)
    # Hafta sonu fazla mesaisi (Cmt/Paz 08:00-18:00): kişi, gün (0..2), kişi başı nominal saat (≤ 8,5)
    weekend_overtime_headcount: int | None = Field(default=None, ge=0)
    weekend_overtime_days: int | None = Field(default=None, ge=0, le=2)
    weekend_overtime_hours_per_person: float | None = Field(default=None, ge=0, le=8.5, allow_inf_nan=False)
    overtime_proposed: bool = False  # False => onaylı (elle giriş); True => plan önerisi, onay bekliyor
    overtime_extra_headcount: int | None = Field(default=None, ge=0)  # mesaiye alınabilecek ek kişi (komşu merkez)


class OvertimeCellRef(BaseModel):
    work_center_id: int
    week_start: date


class OvertimeDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    cells: list[OvertimeCellRef]


class WcWeekOut(BaseModel):
    planning_mode: Literal["labor", "line"] = "labor"
    required_crew_size: int = 1
    line_hours_per_day: float | None = None
    required_labor_hours: float = 0
    work_center_id: int
    week_start: date
    # etkin degerler (istisna varsa o, yoksa varsayilan)
    headcount: int
    efficient_hours_per_person: float
    working_days: int
    capacity_hours: float
    # varsayilanlar (vardiya/personel/makine tanimindan)
    default_headcount: int
    default_efficient_hours: float
    default_working_days: int
    # istisna kaydi (varsa)
    has_override: bool = False
    ov_headcount: int | None = None
    ov_efficient_hours_per_person: float | None = None
    ov_working_days: int | None = None
    note: str = ""
    # fazla mesai (etkin değerler + istisna kaydı)
    overtime_headcount: int = 0
    overtime_days: int = 0
    overtime_hours_per_person: float = 0.0
    overtime_efficiency_ratio: float = 0.0
    overtime_capacity_hours: float = 0.0
    base_capacity_hours: float = 0.0
    ov_overtime_headcount: int | None = None
    ov_overtime_days: int | None = None
    ov_overtime_hours_per_person: float | None = None
    # hafta sonu fazla mesaisi + kişi başı birikim
    weekend_overtime_headcount: int = 0
    weekend_overtime_days: int = 0
    weekend_overtime_hours_per_person: float = 0.0
    weekend_overtime_capacity_hours: float = 0.0
    ov_weekend_overtime_headcount: int | None = None
    ov_weekend_overtime_days: int | None = None
    ov_weekend_overtime_hours_per_person: float | None = None
    overtime_person_hours_week: float = 0.0  # fazla mesai yapan kişinin bu haftaki nominal saati
    overtime_person_hours_ytd: float = 0.0  # yıl başından bu haftaya birikim (kişi başı)
    overtime_legal_yearly_hours: float = 270.0
    overtime_proposed: bool = False
    overtime_extra_headcount: int = 0


# ---- Senaryo matrisi ----
RuleKind = Literal["finish", "cycles"]


class RuleOut(BaseModel):
    rule: str
    lag_cycles: float
    wait_minutes: float
    source: str  # default / group / item
    id: int | None = None
    note: str = ""
    description: str = ""


class FlowNode(BaseModel):
    name: str
    work_centers: list[str]
    item_count: int
    cycle_time_sec: float | None = None
    semi_finished_codes: list[str] = []


class FlowTransition(BaseModel):
    from_op: str
    to_op: str
    from_wip_code: str = ""
    to_wip_code: str = ""
    effective: RuleOut
    group_rule: RuleOut | None = None
    item_rule: RuleOut | None = None


class FlowItem(BaseModel):
    code: str
    name: str
    operations: list[str]
    differs: bool
    item_rules: int


class FlowOut(BaseModel):
    product_group: str
    item_code: str | None = None
    item_name: str | None = None
    nodes: list[FlowNode]
    transitions: list[FlowTransition]
    items: list[FlowItem]


class ScenarioGroup(BaseModel):
    product_group: str
    item_count: int
    operations: list[str]
    rule_count: int


class RuleIn(BaseModel):
    scope: Literal["group", "item"] = "group"
    product_group: str = ""
    item_code: str | None = None
    from_op: str = Field(min_length=1)
    to_op: str = Field(min_length=1)
    from_wip_code: str = ""
    to_wip_code: str = ""
    rule: RuleKind = "finish"
    lag_cycles: float = Field(default=0.0, ge=0)
    wait_minutes: float = Field(default=0.0, ge=0)
    note: str = ""


class RuleRow(RuleIn, ORM):
    id: int
    description: str = ""


# ---- Stok & rezervasyon ----
class ReceiptIn(BaseModel):
    item_code: str = Field(min_length=1)
    receipt_date: date
    quantity: float = Field(gt=0)
    lot: str = ""
    note: str = ""


class ReceiptOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    receipt_date: date
    quantity: float
    lot: str
    note: str
    source: str
    created_by: str


class StockRow(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    product_group: str
    on_hand: float  # depo girisi - sevk
    reserved: float  # acik rezervasyon
    free: float  # on_hand - reserved
    shipped: float
    open_demand: float  # acik siparislerin rezerve/sevk edilmemis miktari
    open_orders: int


class ReservationIn(BaseModel):
    item_id: int | None = None
    item_code: str | None = None
    order_id: int
    quantity: float = Field(gt=0)
    note: str = ""


class ReservationOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    due_date: date
    order_qty: float
    quantity: float
    source: str
    note: str
    created_by: str
    created_at: datetime | None = None


class OrderStockRow(BaseModel):
    """Acik siparisin rezervasyon / sevk durumu."""

    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    due_date: date
    item_id: int
    item_code: str
    item_name: str
    quantity: float
    reserved: float
    shipped: float
    remaining: float  # quantity - reserved - shipped
    status: str
    planned_end: date | None = None


class ShipIn(BaseModel):
    quantity: float | None = None  # bos = rezervasyonun tamami
    ship_date: date | None = None
    note: str = ""


class ShipmentOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str
    ship_date: date
    quantity: float
    note: str
    created_by: str


class AutoReserveRequest(BaseModel):
    item_ids: list[int] | None = None  # bos = tum urunler


class AutoReserveConfirm(BaseModel):
    preview_token: str = Field(min_length=1)


class AutoReserveLine(OrderStockRow):
    allocate: float
    remaining_after: float
    free_before: float
    free_after: float


class AutoReservePreview(BaseModel):
    preview_token: str
    rows: list[AutoReserveLine]
    reserved_qty: float
    items: int


class AutoReserveResult(BaseModel):
    created: int
    reserved_qty: float
    items: int
    message: str


# ---- Plan revizyonu ----
REVISION_REASON_CODES = (
    "material_issue",
    "machine_down",
    "absenteeism",
    "overtime_labor",
    "vip_pull_in",
    "customer_postpone",
    "other",
)


class PlanRevisionCreate(BaseModel):
    reason_codes: list[str]
    note: str = ""
    material_policy: MaterialPolicy = "conditional"
    placement: Placement = "flow"
    jit_buffer_days: int = Field(default=2, ge=0, le=60)
    slip_mode: SlipMode = "chain"
    use_overtime: bool = True
    prep_fill: bool = True
    start_week: date
    weeks: int = 8
    mode: PlanMode = "due_date"
    work_center_ids: list[int] | None = None
    replace_manual: bool = False


class JobMoveOpOut(BaseModel):
    operation_id: int
    operation_seq: int
    operation_name: str
    work_center_id: int
    work_center_code: str
    semi_finished_code: str = ""
    produced_qty: float
    remaining_qty: float
    locked: bool
    status: str  # completed | current | remaining


class JobMovePreviewOut(BaseModel):
    order_id: int
    order_no: str
    item_code: str
    quantity: float
    movable_qty: float
    current_start: date | None = None
    status: str
    ops: list[JobMoveOpOut] = []
    consumed_work_centers: list[str] = []


class PlanRevisionChangeIn(BaseModel):
    entity_type: str  # order | wc_week
    entity_id: int = 0
    extra_key: str = ""
    field: str
    new_value: str = ""


class PlanRevisionChangesBulkIn(BaseModel):
    changes: list[PlanRevisionChangeIn]


class PlanRevisionChangesRemoveIn(BaseModel):
    """Taslaktan toplu girdi çıkarma (ör. karşılanamayan talepleri geri çekme)."""

    change_ids: list[int] = Field(min_length=1)


class PlanRevisionChangeOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    extra_key: str
    field: str
    old_value: str
    new_value: str


class PlanRevisionEventOut(BaseModel):
    id: int
    action: str
    username: str
    detail: str
    created_at: datetime


class PlanRevisionKpis(BaseModel):
    planned_hours: float = 0
    line_count: int = 0
    late: int = 0
    on_time: int = 0
    unplanned: int = 0
    partial: int = 0


class PlanRevisionOrderDiff(BaseModel):
    """Canlı plan ile önerilen plan arasında sipariş bazlı önce/sonra satırı."""

    order_id: int
    order_no: str
    position_no: str = ""
    customer: str = ""
    item_code: str = ""
    quantity: float = 0.0
    due_date: date | None = None  # ilk termin
    due_before: date | None = None  # canlı planın esas aldığı (etkin) termin
    due_after: date | None = None  # önerilen planın esas aldığı termin (talep varsa yeni)
    end_before: date | None = None  # canlı plan tahmini bitiş
    end_after: date | None = None  # önerilen tahmini bitiş
    delta_days: int | None = None  # bitiş kayması: + geriye, - öne
    status_before: str = ""
    status_after: str = ""
    lateness_before: int | None = None
    lateness_after: int | None = None
    change_kind: str = ""  # revised_due_date / job_move / ""
    change_id: int | None = None
    requested: bool = False  # bu revizyonda talep girilen sipariş
    met: bool | None = None  # talep: yeni termine yetişiyor mu
    pushed: bool = False  # bitişi geriye kaydı
    pulled_forward: bool = False  # bitişi öne geldi
    newly_late: bool = False  # canlıda geç değilken öneride geç
    bumped: bool = False  # iş taşıma yolunda kaydırılan (çakışan) iş


class PullForwardMove(BaseModel):
    order_id: int
    order_no: str
    position_no: str = ""
    customer: str = ""
    item_code: str = ""
    operation_seq: int
    work_center_code: str
    from_week: date
    to_week: date
    hours: float
    due_date: date


class PullForwardPlanOut(BaseModel):
    """Tüm ufuk öne çekme planı: atıl hücreler ve sipariş başına tek taşıma."""

    start_week: date
    weeks: int
    idle_hours_total: float = 0.0
    idle_cells: int = 0
    moves: list[PullForwardMove] = []
    moved_hours: float = 0.0


class IdleSuggestionRow(BaseModel):
    """Atıl haftaya öne çekilebilir iş (sonraki haftada planlı satır)."""

    order_id: int
    order_no: str
    position_no: str = ""
    customer: str = ""
    item_code: str = ""
    operation_seq: int
    operation_name: str = ""
    semi_finished_code: str = ""
    from_week: date
    hours: float
    qty: float = 0.0
    due_date: date
    plan_line_id: int
    eligible: bool = True  # öncül ve malzeme uygun
    fits: bool = False  # atıl saate sığıyor (termin sırasıyla kümülatif)
    blocker: str = ""


class IdleSuggestionOut(BaseModel):
    work_center_id: int
    work_center_code: str
    week_start: date
    capacity_hours: float = 0.0
    planned_hours: float = 0.0
    idle_hours: float = 0.0
    eligible_count: int = 0
    fits_hours: float = 0.0
    rows: list[IdleSuggestionRow] = []
    notes: list[str] = []


class OvertimeSuggestionRow(BaseModel):
    """Darboğaz haftası için fazla mesai önerisi (18:00-21:00, kişi başı ≤ 2,5 sa)."""

    work_center_id: int
    work_center_code: str
    week_start: date
    headcount: int
    current_overtime_headcount: int = 0
    suggested_overtime_headcount: int
    overtime_days: int
    overtime_hours_per_person: float
    efficiency_ratio: float
    added_capacity_hours: float
    utilization: float = 0.0
    shortfall_hours_before: float = 0.0
    shortfall_hours_after: float = 0.0
    order_nos: list[str] = []
    explanation: str = ""


class OvertimeSuggestionOut(BaseModel):
    revision_id: int
    total_shortfall_hours: float = 0.0
    covered_hours: float = 0.0
    uncovered_hours: float = 0.0
    suggestions: list[OvertimeSuggestionRow] = []
    notes: list[str] = []
    window: str = "18:00-21:00"
    max_hours_per_person: float = 2.5


class PlanRevisionDiffSummary(BaseModel):
    requested: int = 0
    met: int = 0
    unmet: int = 0
    pushed: int = 0
    newly_late: int = 0
    pulled_forward: int = 0
    unchanged: int = 0
    bumped: int = 0


class PlanRevisionCompareOut(BaseModel):
    baseline: PlanRevisionKpis
    proposed: PlanRevisionKpis
    schedule_rows: list[dict] = []
    bumped_orders: list[str] = []
    insert_notes: list[str] = []
    unplanned: list[dict] = []
    order_diffs: list[PlanRevisionOrderDiff] = []
    diff_summary: PlanRevisionDiffSummary = PlanRevisionDiffSummary()


class PlanRevisionOut(BaseModel):
    id: int
    revision_no: str
    status: str
    reason_codes: list[str]
    note: str
    material_policy: MaterialPolicy = "conditional"
    placement: Placement = "flow"
    jit_buffer_days: int = 2
    slip_mode: str = "chain"
    use_overtime: bool = True
    prep_fill: bool = True
    start_week: date
    weeks: int
    mode: str
    work_center_ids: list[int]
    replace_manual: bool
    created_by: str
    created_at: datetime
    calculated_at: datetime | None = None
    approved_by: str = ""
    approved_at: datetime | None = None
    applied_at: datetime | None = None
    rejected_by: str = ""
    reject_note: str = ""
    input_fingerprint: str = ""
    changes: list[PlanRevisionChangeOut] = []
    events: list[PlanRevisionEventOut] = []
    compare: PlanRevisionCompareOut | None = None
    apply_message: str = ""
