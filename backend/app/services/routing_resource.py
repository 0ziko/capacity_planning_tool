"""Insan–makine kaynak ihtiyaci: tek birim donusum servisi (FAZ 10).

Haftalik planlama yuku legacy_unspecified rotalarda eski hours_for formulunu korur.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from app.models import RoutingOperation

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
    run = quantity * float(op.cycle_time_sec or 0) / 3600.0
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
) -> OperationResourceNeed:
    ctx = _ctx(op)
    qty = max(0.0, float(quantity))
    warnings: list[str] = []

    if ctx.basis == "legacy_unspecified":
        labor = legacy_hours_for(op, qty, setup_required=setup_required)
        mh = None
        if ctx.machine_cycle_time_sec is not None and qty > 1e-9:
            mh = qty * float(ctx.machine_cycle_time_sec) / 3600.0
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
        run_labor = qty * ctx.cycle_time_sec / 3600.0 + _setup_labor_hours(ctx, setup_required and qty > 1e-9)
        crew = ctx.crew_size if ctx.crew_size and ctx.crew_size > 0 else 1
        elapsed = run_labor / crew
        mh = None
        if ctx.machine_cycle_time_sec is not None and qty > 1e-9:
            mh = qty * float(ctx.machine_cycle_time_sec) / 3600.0 + _setup_machine_hours(ctx, setup_required)
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
    run_machine = cycles_round * ctx.cycle_time_sec / 3600.0
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
