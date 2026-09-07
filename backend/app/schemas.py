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
    code: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    is_active: bool = True


class MachineOut(MachineIn, ORM):
    id: int
    work_center_id: int
    employee_count: int = 0  # atanmis aktif personel


class WorkCenterIn(BaseModel):
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


class WorkCenterOut(WorkCenterIn, ORM):
    id: int
    shifts: list[ShiftOut] = []
    machines: list[MachineOut] = []
    employee_count: int = 0  # is merkezine bagli aktif personel
    machine_employee_count: int = 0  # aktif makinelere atanmis aktif personel
    capacity_headcount: int = 0  # kapasite hesabinda kullanilan kisi (vardiya kisi sayisi haric)


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


class OperationOut(ORM):
    id: int
    seq: int
    operation_name: str
    work_center_id: int
    cycle_time_sec: float
    setup_time_min: float
    semi_finished_code: str = ""


class ItemOut(ORM):
    id: int
    code: str
    name: str
    product_group: str
    unit: str


class ItemDetail(ItemOut):
    bom_lines: list[BomLineOut] = []
    operations: list[OperationOut] = []


# ---- Orders ----
class OrderIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=64)
    customer: str = ""
    due_date: date
    item_code: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit_price: float = Field(default=0.0, ge=0)
    note: str = ""


class OrderOut(ORM):
    id: int
    order_no: str
    customer: str
    due_date: date
    item_id: int
    item_code: str = ""
    item_name: str = ""
    quantity: float
    unit_price: float = 0.0
    revenue: float = 0.0  # miktar x birim fiyat
    status: str
    merged_into_id: int | None = None
    note: str = ""


class OrderScheduleOut(BaseModel):
    """Plan sonucuna gore siparisin tahmini uretim bitisi."""

    order_id: int
    order_no: str
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
    last_work_center_code: str = ""
    lateness_days: int | None = None  # + gec, - erken
    plan_status: str  # unplanned / partial / late / on_time / no_ops


class OrderProgressOp(BaseModel):
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
    order_id: int
    order_no: str
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
    item_id: int
    item_code: str
    item_name: str
    order_count: int
    total_qty: float
    earliest_due: date
    latest_due: date
    customers: list[str]
    has_progress: bool  # siparislerden birinde uretim kaydi var (birlestirme dikkat)
    orders: list[OrderOut]


class MergeRequest(BaseModel):
    order_ids: list[int] = Field(min_length=2)
    order_no: str | None = None
    due_date: date | None = None  # None => en erken termin
    customer: str | None = None


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
    hours: float


class RequirementQuery(BaseModel):
    item_codes: list[str] | None = None
    work_center_ids: list[int] | None = None
    quantities: dict[str, float] | None = None  # item_code -> qty (verilmezse siparislerden)
    due_from: date | None = None
    due_to: date | None = None


# ---- Planning ----
PlanMode = Literal["due_date", "revenue"]


class AutoPlanRequest(BaseModel):
    start_week: date  # herhangi bir gun; pazartesiye yuvarlanir
    weeks: int = 12
    work_center_ids: list[int] | None = None  # None => is_planned olanlar
    replace_existing: bool = True
    mode: PlanMode = "due_date"  # due_date: termine gore; revenue: ufuk icinde maksimum ciro


class PlanCompareRequest(BaseModel):
    start_week: date
    weeks: int = 12
    work_center_ids: list[int] | None = None


class PeriodRevenue(BaseModel):
    period: str  # hafta: Pazartesi tarihi (YYYY-MM-DD); ay: YYYY-MM
    completed_revenue: float  # o donemde tamamlanan (son operasyonu biten) siparislerin cirosu
    completed_orders: int
    earned_revenue: float  # planlanan saat payina gore oransal ciro
    cumulative_completed: float
    cumulative_earned: float


class RevenueOut(BaseModel):
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
    order_id: int
    operation_id: int
    week_start: date
    planned_hours: float | None = None  # None => tum operasyon saati
    planned_qty: float | None = None


class PlanLineOut(ORM):
    id: int
    order_id: int
    order_no: str = ""
    customer: str = ""
    due_date: date | None = None
    item_code: str = ""
    operation_id: int
    operation_seq: int = 0
    work_center_id: int
    work_center_code: str = ""
    week_start: date
    planned_hours: float
    planned_qty: float
    mode: str
    strategy: str = ""


class WeekLoad(BaseModel):
    week_start: date
    capacity_hours: float
    planned_hours: float
    utilization: float
    capacity_units: float
    planned_units: float


class WorkCenterLoad(BaseModel):
    work_center_id: int
    work_center_code: str
    weeks: list[WeekLoad]


class LeadTimeRequest(BaseModel):
    item_code: str
    quantity: float
    start: date
    work_center_ids: list[int] | None = None
    operation_seqs: list[int] | None = None


class LeadTimeStep(BaseModel):
    operation_seq: int
    operation_name: str
    work_center_code: str
    hours: float
    start: str
    end: str
    start_rule: str = ""  # senaryo matrisi: baslangic kurali aciklamasi (bos = ilk operasyon)


class LeadTimeOut(BaseModel):
    item_code: str
    quantity: float
    total_hours: float
    start: str
    end: str
    steps: list[LeadTimeStep]


# ---- Progress ----
class ProgressOut(BaseModel):
    work_center_id: int
    work_center_code: str
    week_start: date
    planned_hours: float
    expected_hours_to_date: float
    actual_hours_to_date: float
    remaining_hours: float
    remaining_days: float
    working_days: int
    elapsed_days: int
    status: str  # ahead / on_track / behind


# ---- Imports ----
class ImportResult(BaseModel):
    kind: str
    inserted: int
    updated: int
    errors: list[str]


# ---- Haftalik is gucu (WorkCenterWeek) ----
class WcWeekIn(BaseModel):
    headcount: int | None = None
    efficient_hours_per_person: float | None = None
    working_days: int | None = Field(default=None, ge=0, le=7)
    note: str = ""


class WcWeekOut(BaseModel):
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
    customer: str
    ship_date: date
    quantity: float
    note: str
    created_by: str


class AutoReserveRequest(BaseModel):
    item_ids: list[int] | None = None  # bos = tum urunler


class AutoReserveResult(BaseModel):
    created: int
    reserved_qty: float
    items: int
    message: str
