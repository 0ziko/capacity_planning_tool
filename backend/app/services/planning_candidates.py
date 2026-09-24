"""Planlama adaylari: tekil siparis ve uretim partisi tek oncelik listesinde."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Item, Order, ProductionBatch, RoutingOperation, WorkCenter
from app.services.bom_tree import explode_order, has_wip_structure
from app.services.orders import effective_due
from app.services.remaining_work import (
    SchedulingContext,
    WorkKey,
    build_work_map_for_batch,
    build_work_map_for_order,
    operation_run_hours,
    qty_and_setup_for_placement,
)

CandidateKind = Literal["order", "batch"]

REVENUE_MODE_LABEL = "ciro oncelikli (sezgisel)"


@dataclass(frozen=True)
class PlanningCandidate:
    kind: CandidateKind
    candidate_id: int
    display_code: str
    route_item: Item
    anchor_order: Order
    order: Order | None
    batch: ProductionBatch | None
    effective_due_date: date
    remaining_sales_value: float
    remaining_required_hours: float
    warnings: tuple[str, ...] = ()

    @property
    def revenue_per_hour(self) -> float:
        if self.remaining_required_hours <= 1e-6:
            return 0.0
        return self.remaining_sales_value / self.remaining_required_hours


def batch_anchor_order(batch: ProductionBatch) -> Order | None:
    links = [l for l in batch.orders if l.order]
    if not links:
        return None
    return min(
        (l.order for l in links),
        key=lambda o: (effective_due(o), (o.order_no or "").upper(), o.id),
    )


def batch_effective_due(batch: ProductionBatch) -> date:
    dues = [effective_due(l.order) for l in batch.orders if l.order]
    return min(dues) if dues else batch.due_date


def _hours_for_ops(
    ops: list[RoutingOperation],
    work_map: dict,
    wc_by_id: dict[int, WorkCenter],
) -> float:
    total = 0.0
    for op in sorted(ops, key=lambda x: x.seq):
        if op.work_center_id not in wc_by_id:
            continue
        qty, setup = qty_and_setup_for_placement(work_map, op)
        if qty > 1e-6:
            total += operation_run_hours(op, qty, setup_required=setup)
    return total


def _gross_hours_order(db: Session, o: Order, wc_by_id: dict[int, WorkCenter]) -> float:
    if has_wip_structure(o):
        jobs = explode_order(db, o)
        total = 0.0
        for job in jobs.wip_jobs:
            ops = [op for op in job.item.operations or [] if op.work_center_id in wc_by_id]
            total += sum(op.hours_for(job.quantity) for op in ops)
        if jobs.finish_job:
            ops = [op for op in jobs.finish_job.item.operations or [] if op.work_center_id in wc_by_id]
            total += sum(op.hours_for(jobs.finish_job.quantity) for op in ops)
        return total
    return sum(op.hours_for(o.quantity) for op in o.item.operations or [] if op.work_center_id in wc_by_id)


def _gross_hours_batch(batch: ProductionBatch, wc_by_id: dict[int, WorkCenter]) -> float:
    if not batch.item:
        return 0.0
    return sum(op.hours_for(batch.quantity) for op in batch.item.operations or [] if op.work_center_id in wc_by_id)


def remaining_required_hours(
    db: Session,
    anchor: Order,
    wc_by_id: dict[int, WorkCenter],
    sched_ctx: SchedulingContext,
    produced_map: dict[WorkKey, float],
    *,
    batch: ProductionBatch | None = None,
) -> float:
    if batch:
        work_map = build_work_map_for_batch(db, batch, anchor, sched_ctx, produced=produced_map)
        route_item = batch.item
    else:
        work_map = build_work_map_for_order(db, anchor, sched_ctx, produced=produced_map)
        route_item = anchor.item

    if has_wip_structure(anchor):
        jobs = explode_order(db, anchor)
        total = 0.0
        for job in jobs.wip_jobs:
            ops = [op for op in job.item.operations or [] if op.work_center_id in wc_by_id]
            total += _hours_for_ops(ops, work_map, wc_by_id)
        if jobs.finish_job:
            ops = [op for op in jobs.finish_job.item.operations or [] if op.work_center_id in wc_by_id]
            total += _hours_for_ops(ops, work_map, wc_by_id)
        return total

    ops = [op for op in (route_item.operations or []) if op.work_center_id in wc_by_id]
    return _hours_for_ops(ops, work_map, wc_by_id)


def _scaled_remaining_revenue(
    gross_revenue: float,
    remaining_hours: float,
    gross_hours: float,
    *,
    has_price: bool,
    warnings: list[str],
    label: str,
) -> float:
    if not has_price:
        warnings.append(f"{label}: birim fiyat yok; ciro onceliginde en sona duser")
        return 0.0
    if remaining_hours <= 1e-6:
        return 0.0
    if gross_hours <= 1e-6:
        warnings.append(f"{label}: kalan saat hesaplanamadi")
        return 0.0
    return gross_revenue * (remaining_hours / gross_hours)


def _candidate_from_order(
    db: Session,
    o: Order,
    wc_by_id: dict[int, WorkCenter],
    sched_ctx: SchedulingContext,
    produced_map: dict[WorkKey, float],
) -> PlanningCandidate | None:
    rem_h = remaining_required_hours(db, o, wc_by_id, sched_ctx, produced_map)
    # Zero hours can mean missing route times; placement diagnoses remaining quantities.
    warnings: list[str] = []
    gross_h = _gross_hours_order(db, o, wc_by_id)
    gross_rev = o.quantity * (o.unit_price or 0.0)
    rem_rev = _scaled_remaining_revenue(
        gross_rev,
        rem_h,
        gross_h,
        has_price=bool(o.unit_price and o.unit_price > 0),
        warnings=warnings,
        label=o.order_no,
    )
    if rem_h <= 1e-6 and gross_h > 1e-6:
        warnings.append(f"{o.order_no}: sifir kalan saat")
    return PlanningCandidate(
        kind="order",
        candidate_id=o.id,
        display_code=o.order_no,
        route_item=o.item,
        anchor_order=o,
        order=o,
        batch=None,
        effective_due_date=effective_due(o),
        remaining_sales_value=round(rem_rev, 4),
        remaining_required_hours=round(rem_h, 4),
        warnings=tuple(warnings),
    )


def _candidate_from_batch(
    db: Session,
    batch: ProductionBatch,
    wc_by_id: dict[int, WorkCenter],
    sched_ctx: SchedulingContext,
    produced_map: dict[WorkKey, float],
) -> PlanningCandidate | None:
    anchor = batch_anchor_order(batch)
    if not anchor or not batch.item:
        return None
    rem_h = remaining_required_hours(db, anchor, wc_by_id, sched_ctx, produced_map, batch=batch)
    # Zero hours can mean missing route times; placement diagnoses remaining quantities.
    warnings: list[str] = []
    gross_h = _gross_hours_batch(batch, wc_by_id)
    gross_rev = sum(l.quantity * (l.order.unit_price or 0.0) for l in batch.orders if l.order)
    priced = all(l.order and (l.order.unit_price or 0) > 0 for l in batch.orders if l.order)
    rem_rev = _scaled_remaining_revenue(
        gross_rev,
        rem_h,
        gross_h,
        has_price=priced and bool(batch.orders),
        warnings=warnings,
        label=batch.batch_no,
    )
    return PlanningCandidate(
        kind="batch",
        candidate_id=batch.id,
        display_code=batch.batch_no,
        route_item=batch.item,
        anchor_order=anchor,
        order=None,
        batch=batch,
        effective_due_date=batch_effective_due(batch),
        remaining_sales_value=round(rem_rev, 4),
        remaining_required_hours=round(rem_h, 4),
        warnings=tuple(warnings),
    )


def build_planning_candidates(
    db: Session,
    orders: list[Order],
    batches: list[ProductionBatch],
    wc_by_id: dict[int, WorkCenter],
    sched_ctx: SchedulingContext,
    produced_map: dict[WorkKey, float],
) -> list[PlanningCandidate]:
    out: list[PlanningCandidate] = []
    for o in orders:
        c = _candidate_from_order(db, o, wc_by_id, sched_ctx, produced_map)
        if c:
            out.append(c)
    for b in batches:
        if not b.orders:
            continue
        c = _candidate_from_batch(db, b, wc_by_id, sched_ctx, produced_map)
        if c:
            out.append(c)
    return out


def due_date_sort_key(c: PlanningCandidate) -> tuple:
    # Termin sırası; aynı günde kalan satış değeri yüksek olan önce (mesai/kapasite kotası önce ona gider).
    return (c.effective_due_date, -float(c.remaining_sales_value or 0.0), c.display_code.upper(), c.kind, c.candidate_id)


def revenue_sort_key(c: PlanningCandidate) -> tuple:
    """Yuksek ciro/saat once; sifir saat veya fiyatsiz en sona."""
    schedulable = c.remaining_required_hours > 1e-6 and c.remaining_sales_value > 1e-6
    density = c.revenue_per_hour if schedulable else 0.0
    return (
        0 if schedulable else 1,
        -density,
        c.effective_due_date,
        c.display_code.upper(),
        c.kind,
        c.candidate_id,
    )


def sort_candidates(candidates: list[PlanningCandidate], mode: str) -> list[PlanningCandidate]:
    if mode == "revenue":
        return sorted(candidates, key=revenue_sort_key)
    return sorted(candidates, key=due_date_sort_key)


def candidate_skipped_record(c: PlanningCandidate) -> dict:
    item_code = c.route_item.code if c.route_item else ""
    rec = {
        "kind": c.kind,
        "item_code": item_code,
        "revenue": round(c.remaining_sales_value, 2),
        "hours": round(c.remaining_required_hours, 2),
    }
    if c.kind == "batch":
        rec["batch_no"] = c.display_code
        rec["order_no"] = c.anchor_order.order_no if c.anchor_order else ""
    else:
        rec["order_no"] = c.display_code
    return rec
