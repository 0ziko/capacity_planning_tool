from datetime import date, time

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


class WorkCenterIn(BaseModel):
    code: str
    name: str
    description: str = ""
    is_active: bool = True
    is_planned: bool = False
    capacity_unit_hours: float = 10.0
    default_efficient_hours: float = 4.0


class WorkCenterOut(WorkCenterIn, ORM):
    id: int
    shifts: list[ShiftOut] = []
    employee_count: int = 0


# ---- Employees ----
class EmployeeIn(BaseModel):
    code: str
    name: str
    work_center_id: int | None = None
    is_active: bool = True


class EmployeeOut(EmployeeIn, ORM):
    id: int


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
class OrderOut(ORM):
    id: int
    order_no: str
    customer: str
    due_date: date
    item_id: int
    item_code: str = ""
    quantity: float
    status: str


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
class AutoPlanRequest(BaseModel):
    start_week: date  # herhangi bir gun; pazartesiye yuvarlanir
    weeks: int = 12
    work_center_ids: list[int] | None = None  # None => is_planned olanlar
    replace_existing: bool = True


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
