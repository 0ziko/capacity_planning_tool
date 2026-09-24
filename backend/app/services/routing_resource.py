"""Insan–makine kaynak ihtiyaci: tek birim donusum servisi (FAZ 10).

Haftalik planlama yuku legacy_unspecified rotalarda eski hours_for formulunu korur.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from app.models import RoutingOperation
from app.models.master import norm_op


def station_crew(machine) -> int | None:
    """Istasyonun gerekli ekibi; istasyonda bos ise bagli is merkezinin 'gerekli ekip' tanimi kullanilir.
    (Hat kapasitesi = istasyon saati x ekip; ekip tanimsizsa kapasite 0 kalir.)"""
    if machine is None:
        return None
    crew = getattr(machine, "required_crew_size", None)
    if crew:
        return int(crew)
    wc = getattr(machine, "work_center", None)
    wc_crew = getattr(wc, "required_crew_size", None) if wc is not None else None
    return int(wc_crew) if wc_crew else None


def is_line_operation(op) -> bool:
    return getattr(getattr(op, "work_center", None), "planning_mode", "labor") == "line"


def planning_unit_hours(op) -> float:
    if is_line_operation(op):
        return line_run_hours(op, 1)
    return standard_unit_hours(op)


def station_units(op, machine_id) -> int:
    """Seçilen istasyonun dizilimi (istasyon bağında tanımlıysa), yoksa operasyonun dizilimi."""
    if machine_id is not None:
        for st in getattr(op, "alt_stations", None) or []:
            if int(st.machine_id) == int(machine_id) and getattr(st, "units_per_cycle", None):
                return max(int(st.units_per_cycle), 1)
    return max(int(getattr(op, "units_per_cycle", None) or 1), 1)


def line_run_hours(op, quantity: float, units: int | None = None) -> float:
    """One continuous batch: first group dwell, then one interval per group. units: istasyon dizilimi (opsiyonel)."""
    if quantity <= 0:
        return 0.0
    cycle = float(op.cycle_time_sec or 0)
    interval = float(getattr(op, "line_interval_sec", None) or 0)
    if cycle <= 0 or interval <= 0:
        return 0.0
    groups = math.ceil(quantity / max(int(units or op.units_per_cycle or 1), 1))
    return (cycle + (groups - 1) * interval) / 3600


def line_quantity_for_hours(op, hours: float, units: int | None = None) -> float:
    """Whole groups that finish within an independent station/week batch."""
    cycle = float(op.cycle_time_sec or 0)
    interval = float(getattr(op, "line_interval_sec", None) or 0)
    seconds = max(0.0, hours) * 3600
    if cycle <= 0 or interval <= 0 or seconds + 1e-8 < cycle:
        return 0.0
    return (1 + math.floor((seconds - cycle + 1e-8) / interval)) * max(int(units or op.units_per_cycle or 1), 1)


def planning_setup_hours(op) -> float:
    return 0.0 if is_line_operation(op) else float(op.setup_time_min or 0) / 60


def conveyor_kind(op) -> str | None:
    name = norm_op(getattr(op, "operation_name", ""))
    if re.search(r"\btavlama\b", name):
        return "annealing"
    if re.search(r"\byikama\b", name):
        return "washing"
    return None


def conveyor_units(op) -> int:
    return max(int(getattr(op, "units_per_cycle", None) or 1), 1) if conveyor_kind(op) else 1


def standard_unit_hours(op) -> float:
    """Proportional earned labor, excluding setup and batch rounding."""
    machine_basis = getattr(op, "time_basis", None) == "machine_seconds_per_cycle"
    units = max(int(getattr(op, "units_per_cycle", None) or 1), 1) if machine_basis else conveyor_units(op)
    crew = max(getattr(op, "crew_size", None) or 1, 1) if machine_basis else 1
    return float(op.cycle_time_sec or 0) * crew / units / 3600

TimeBasis = Literal["labor_seconds_per_unit", "machine_seconds_per_cycle", "legacy_unspecified"]

TIME_BASIS_VALUES: frozenset[str] = frozenset(
    {"labor_seconds_per_unit", "machine_seconds_per_cycle", "legacy_unspecified"}
)


def normalize_time_basis(raw: str | None) -> TimeBasis:
    v = (raw or "legacy_unspecified").strip()
    if v not in TIME_BASIS_VALUES:
        return "legacy_unspecified"
    return v  # type: ignore[return-value]


@dataclass
class OperationResourceNeed:
    labor_hours: float
    machine_hours: float | None
    elapsed_labor_hours: float
    machine_cycles_nominal: float | None = None
    machine_cycles_rounded: int | None = None
    machine_hours_nominal: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def planning_load_hours(self) -> float:
        """Haftalik is gucu planinda kullanilan yuk (legacy uyumlu)."""
        basis = normalize_time_basis(getattr(self, "_basis", "legacy_unspecified"))
        if basis == "legacy_unspecified":
            return self.labor_hours
        return self.labor_hours


@dataclass
class _OpCtx:
    basis: TimeBasis
    cycle_time_sec: float
    setup_time_min: float
    setup_labor_minutes: float | None
    setup_machine_minutes: float | None
    crew_size: int | None
    machine_cycle_time_sec: float | None
    units_per_cycle: int


def _ctx(op: RoutingOperation) -> _OpCtx:
    upc = int(op.units_per_cycle or 1)
    if upc < 1:
        upc = 1
    return _OpCtx(
        basis=normalize_time_basis(getattr(op, "time_basis", None)),
        cycle_time_sec=float(op.cycle_time_sec or 0),
        setup_time_min=float(op.setup_time_min or 0),
        setup_labor_minutes=getattr(op, "setup_labor_minutes", None),
        setup_machine_minutes=getattr(op, "setup_machine_minutes", None),
        crew_size=getattr(op, "crew_size", None),
        machine_cycle_time_sec=getattr(op, "machine_cycle_time_sec", None),
        units_per_cycle=upc,
    )


def _setup_labor_hours(ctx: _OpCtx, setup_required: bool) -> float:
    if not setup_required:
        return 0.0
    if ctx.setup_labor_minutes is not None:
        return max(0.0, float(ctx.setup_labor_minutes)) / 60.0
    return max(0.0, ctx.setup_time_min) / 60.0


def _setup_machine_hours(ctx: _OpCtx, setup_required: bool) -> float:
    if not setup_required:
        return 0.0
    if ctx.setup_machine_minutes is not None:
        return max(0.0, float(ctx.setup_machine_minutes)) / 60.0
    if ctx.basis == "legacy_unspecified":
        return max(0.0, ctx.setup_time_min) / 60.0
    return 0.0


def legacy_hours_for(op: RoutingOperation, quantity: float, *, setup_required: bool = True) -> float:
    run = quantity * float(op.cycle_time_sec or 0) / conveyor_units(op) / 3600.0
    if setup_required and quantity > 1e-9:
        run += float(op.setup_time_min or 0) / 60.0
    return run


def eligible_machine_ids(op: RoutingOperation) -> list[int]:
    ids: list[int] = []
    if op.primary_machine_id:
        ids.append(int(op.primary_machine_id))
    for st in op.alt_stations or []:
        mid = int(st.machine_id)
        if mid not in ids:
            ids.append(mid)
    return ids


def missing_resource_definition(op: RoutingOperation) -> bool:
    ctx = _ctx(op)
    if ctx.basis == "legacy_unspecified":
        return True
    if ctx.basis == "machine_seconds_per_cycle" and not eligible_machine_ids(op):
        return True
    return False


def compute_operation_need(
    op: RoutingOperation,
    quantity: float,
    *,
    setup_required: bool = True,
    machine=None,
) -> OperationResourceNeed:
    if is_line_operation(op):
        hours = line_run_hours(op, quantity)
        station = machine or getattr(op, "primary_machine", None)
        crew = station_crew(station) if station is not None else None
        warnings = [] if getattr(op, "line_interval_sec", None) else ["missing_line_interval"]
        if not crew:
            warnings.append("missing_station_crew")
        return OperationResourceNeed(labor_hours=hours * (crew or 0), machine_hours=hours, elapsed_labor_hours=hours,
                                     warnings=warnings)
    ctx = _ctx(op)
    qty = max(0.0, float(quantity))
    warnings: list[str] = []

    if ctx.basis == "legacy_unspecified":
        labor = legacy_hours_for(op, qty, setup_required=setup_required)
        mh = None
        if ctx.machine_cycle_time_sec is not None and qty > 1e-9:
            mh = qty * float(ctx.machine_cycle_time_sec) / conveyor_units(op) / 3600.0
            if setup_required:
                mh += _setup_machine_hours(ctx, True)
        elif setup_required and qty > 1e-9:
            pass
        elapsed = labor
        if ctx.crew_size and ctx.crew_size > 1:
            elapsed = labor / ctx.crew_size
        if qty > 1e-9:
            warnings.append("missing_resource_definition")
        out = OperationResourceNeed(
            labor_hours=labor,
            machine_hours=mh,
            elapsed_labor_hours=elapsed,
            warnings=warnings,
        )
        out._basis = ctx.basis  # type: ignore[attr-defined]
        return out

    if ctx.basis == "labor_seconds_per_unit":
        run_labor = qty * ctx.cycle_time_sec / conveyor_units(op) / 3600.0 + _setup_labor_hours(ctx, setup_required and qty > 1e-9)
        crew = ctx.crew_size if ctx.crew_size and ctx.crew_size > 0 else 1
        elapsed = run_labor / crew
        mh = None
        if ctx.machine_cycle_time_sec is not None and qty > 1e-9:
            mh = qty * float(ctx.machine_cycle_time_sec) / conveyor_units(op) / 3600.0 + _setup_machine_hours(ctx, setup_required)
        out = OperationResourceNeed(
            labor_hours=run_labor,
            machine_hours=mh,
            elapsed_labor_hours=elapsed,
            warnings=warnings,
        )
        out._basis = ctx.basis  # type: ignore[attr-defined]
        return out

    # machine_seconds_per_cycle
    cycles_nom = qty / ctx.units_per_cycle if ctx.units_per_cycle else qty
    cycles_round = int(math.ceil(cycles_nom - 1e-12)) if qty > 1e-9 else 0
    run_machine_nom = cycles_nom * ctx.cycle_time_sec / 3600.0
    run_machine = (cycles_nom if conveyor_kind(op) else cycles_round) * ctx.cycle_time_sec / 3600.0
    if setup_required and qty > 1e-9:
        run_machine += _setup_machine_hours(ctx, True)
        run_machine_nom += _setup_machine_hours(ctx, True)
    crew = ctx.crew_size if ctx.crew_size and ctx.crew_size > 0 else None
    labor_content = run_machine * crew if crew else run_machine
    elapsed = run_machine if crew else run_machine
    if not eligible_machine_ids(op) and qty > 1e-9:
        warnings.append("missing_resource_definition")
    out = OperationResourceNeed(
        labor_hours=labor_content,
        machine_hours=run_machine,
        elapsed_labor_hours=elapsed,
        machine_cycles_nominal=cycles_nom if qty > 1e-9 else None,
        machine_cycles_rounded=cycles_round if qty > 1e-9 else None,
        machine_hours_nominal=run_machine_nom if qty > 1e-9 else None,
        warnings=warnings,
    )
    out._basis = ctx.basis  # type: ignore[attr-defined]
    return out


def planning_load_hours(op: RoutingOperation, quantity: float, *, setup_required: bool = True) -> float:
    if is_line_operation(op):
        return line_run_hours(op, quantity)
    ctx = _ctx(op)
    if ctx.basis == "legacy_unspecified":
        return legacy_hours_for(op, quantity, setup_required=setup_required)
    need = compute_operation_need(op, quantity, setup_required=setup_required)
    return need.labor_hours


def resource_definition_stats(db, ops: list[RoutingOperation] | None = None) -> dict:
    from app.models import RoutingOperation as RO

    if ops is None:
        ops = db.query(RO).all()
    legacy = 0
    labor_def = 0
    machine_def = 0
    missing_detail = 0
    for op in ops:
        b = normalize_time_basis(getattr(op, "time_basis", None))
        if b == "legacy_unspecified":
            legacy += 1
        elif b == "labor_seconds_per_unit":
            labor_def += 1
        elif b == "machine_seconds_per_cycle":
            machine_def += 1
        if missing_resource_definition(op):
            missing_detail += 1
    return {
        "total_operations": len(ops),
        "legacy_unspecified": legacy,
        "labor_seconds_per_unit": labor_def,
        "machine_seconds_per_cycle": machine_def,
        "missing_detailed_schedule_definition": missing_detail,
    }
