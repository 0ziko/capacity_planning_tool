"""Operasyon bazinda kalan is: uretim gerceklesmesi + korunmus kesin plan dusulmus net yuk."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionActual, ProductionBatch, RoutingOperation
from app.services.bom_tree import explode_order, has_wip_structure
from app.services.wip import resolve_wip, wip_index

WorkKey = tuple[int, int]  # (order_id, operation_id)


@dataclass(frozen=True)
class SchedulingContext:
    """Planlama oturumu: ufuk ve yenileme modu."""

    horizon_start: date
    horizon_end_exclusive: date
    replace_existing: bool = True
    replace_manual: bool = False


@dataclass
class OperationRemainingWork:
    order_id: int
    operation_id: int
    production_batch_id: int | None
    required_qty: float
    completed_good_qty: float
    remaining_execution_qty: float
    preserved_planned_qty: float
    qty_to_schedule: float
    setup_required: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def gross_hours(self) -> float:
        return 0.0  # filled by caller with op reference


def operation_run_hours(op: RoutingOperation, quantity: float, *, setup_required: bool) -> float:
    run = quantity * op.cycle_time_sec / 3600.0
    if setup_required and quantity > 1e-9:
        run += op.setup_time_min / 60.0
    return run


def _open_orders_for_production(db: Session) -> list[Order]:
    return (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.position_no, Order.id)
        .all()
    )


def produced_qty_map(db: Session, *, as_of: date | None = None) -> tuple[dict[WorkKey, float], list[str]]:
    """(order_id, operation_id) -> uretilen miktar; position_no belirsizliginde uyari."""
    out: dict[WorkKey, float] = defaultdict(float)
    warnings: list[str] = []
    q = db.query(ProductionActual)
    if as_of:
        q = q.filter(ProductionActual.prod_date <= as_of)
    actuals = q.order_by(ProductionActual.prod_date, ProductionActual.id).all()
    if not actuals:
        return dict(out), warnings

    open_orders = _open_orders_for_production(db)
    orders_by_no_item: dict[tuple[str, int], list[Order]] = defaultdict(list)
    for o in open_orders:
        orders_by_no_item[(o.order_no.upper(), o.item_id)].append(o)
    orders_by_item: dict[int, list[Order]] = defaultdict(list)
    for o in open_orders:
        orders_by_item[o.item_id].append(o)

    idx = wip_index(db)

    def op_id_for(item: Item, seq: int | None, wc_id: int, wip_code: str) -> int | None:
        if not item:
            return None
        if wip_code:
            try:
                return resolve_wip(db, wip_code, item.code, idx).id
            except ValueError:
                pass
        if seq is not None:
            op = next((x for x in item.operations if x.seq == seq), None)
            return op.id if op else None
        op = next((x for x in item.operations if x.work_center_id == wc_id), None)
        return op.id if op else None

    fifo_left: dict[WorkKey, float] = {}
    for a in actuals:
        item = a.item
        if not item:
            continue
        op_id = a.operation_seq and next((x.id for x in item.operations if x.seq == a.operation_seq), None)
        if op_id is None:
            op_id = op_id_for(item, a.operation_seq, a.work_center_id, a.semi_finished_code or "")
        if op_id is None:
            continue
        if a.order_no:
            matches = orders_by_no_item.get((a.order_no.upper(), a.item_id), [])
            if len(matches) > 1:
                warnings.append(
                    f"Siparis no {a.order_no} / {item.code}: {len(matches)} acik pozisyon; uretim otomatik atanmadi"
                )
                continue
            if len(matches) == 1:
                out[(matches[0].id, op_id)] += a.quantity
            continue
        qty_left = a.quantity
        for o in orders_by_item.get(a.item_id, []):
            key = (o.id, op_id)
            if key not in fifo_left:
                fifo_left[key] = max(o.quantity - out[key], 0.0)
            room = fifo_left[key]
            if room <= 1e-9:
                continue
            take = min(room, qty_left)
            out[key] += take
            fifo_left[key] = room - take
            qty_left -= take
            if qty_left <= 1e-9:
                break
        if qty_left > 1e-9 and orders_by_item.get(a.item_id):
            last = orders_by_item[a.item_id][-1]
            out[(last.id, op_id)] += qty_left

    return dict(out), warnings


def required_qty_by_operation(db: Session, order: Order) -> dict[int, float]:
    """Operasyon id -> BOM/yari mamul katsayili gerekli miktar."""
    if not order.item:
        return {}
    if has_wip_structure(order):
        jobs = explode_order(db, order)
        req: dict[int, float] = {}
        for job in jobs.wip_jobs:
            for op in job.item.operations or []:
                req[op.id] = job.quantity
        if jobs.finish_job:
            for op in jobs.finish_job.item.operations or []:
                req[op.id] = jobs.finish_job.quantity
        return req
    return {op.id: float(order.quantity or 0) for op in order.item.operations or []}


def required_qty_for_batch(db: Session, batch: ProductionBatch, anchor: Order) -> dict[int, float]:
    if not batch.item:
        return {}
    if has_wip_structure(anchor):
        jobs = explode_order(db, anchor)
        ratio = float(batch.quantity or 0) / float(anchor.quantity or 1) if anchor.quantity else 1.0
        req: dict[int, float] = {}
        for job in jobs.wip_jobs:
            jq = job.quantity * ratio
            for op in job.item.operations or []:
                req[op.id] = jq
        if jobs.finish_job:
            for op in jobs.finish_job.item.operations or []:
                req[op.id] = float(batch.quantity or 0)
        return req
    return {op.id: float(batch.quantity or 0) for op in batch.item.operations or []}


def _line_is_preserved(pl: PlanLine, ctx: SchedulingContext) -> bool:
    if pl.mode == "forecast":
        return False
    if pl.mode not in ("auto", "manual"):
        return False
    wk = pl.week_start
    if wk >= ctx.horizon_end_exclusive:
        return True
    if wk < ctx.horizon_start:
        return False
    if pl.mode == "manual":
        return not ctx.replace_manual
    return not ctx.replace_existing


def _preserved_planned_qty_for_lines(lines: list[PlanLine], remaining_exec: float) -> float:
    total = sum(float(pl.planned_qty or 0) for pl in lines)
    return min(total, max(remaining_exec, 0.0))


def operation_remaining(
    db: Session,
    order_id: int,
    operation_id: int,
    required_qty: float,
    ctx: SchedulingContext,
    *,
    production_batch_id: int | None = None,
    produced: dict[WorkKey, float] | None = None,
    plan_lines: list[PlanLine] | None = None,
    warnings: list[str] | None = None,
) -> OperationRemainingWork:
    produced = produced if produced is not None else produced_qty_map(db)[0]
    completed = float(produced.get((order_id, operation_id), 0.0))
    remaining_exec = max(required_qty - completed, 0.0)
    setup_required = completed <= 1e-6

    if plan_lines is None:
        q = db.query(PlanLine).filter(
            PlanLine.order_id == order_id,
            PlanLine.operation_id == operation_id,
            PlanLine.mode.in_(["auto", "manual"]),
        )
        if production_batch_id is not None:
            q = q.filter(PlanLine.production_batch_id == production_batch_id)
        else:
            q = q.filter(PlanLine.production_batch_id.is_(None))
        plan_lines = q.all()

    preserved_lines = [pl for pl in plan_lines if _line_is_preserved(pl, ctx)]
    preserved_qty = _preserved_planned_qty_for_lines(preserved_lines, remaining_exec)
    qty_to_schedule = max(remaining_exec - preserved_qty, 0.0)

    return OperationRemainingWork(
        order_id=order_id,
        operation_id=operation_id,
        production_batch_id=production_batch_id,
        required_qty=round(required_qty, 4),
        completed_good_qty=round(completed, 4),
        remaining_execution_qty=round(remaining_exec, 4),
        preserved_planned_qty=round(preserved_qty, 4),
        qty_to_schedule=round(qty_to_schedule, 4),
        setup_required=setup_required,
        warnings=list(warnings or []),
    )


def build_work_map_for_order(
    db: Session,
    order: Order,
    ctx: SchedulingContext,
    *,
    produced: dict[WorkKey, float] | None = None,
    global_warnings: list[str] | None = None,
) -> dict[int, OperationRemainingWork]:
    if produced is None:
        produced, prod_warnings = produced_qty_map(db)
        global_warnings = (global_warnings or []) + prod_warnings
    req = required_qty_by_operation(db, order)
    all_lines = db.query(PlanLine).filter(PlanLine.order_id == order.id, PlanLine.mode.in_(["auto", "manual"])).all()
    by_op: dict[int, list[PlanLine]] = defaultdict(list)
    for pl in all_lines:
        if pl.production_batch_id is None:
            by_op[pl.operation_id].append(pl)
    out: dict[int, OperationRemainingWork] = {}
    for op_id, rq in req.items():
        out[op_id] = operation_remaining(
            db,
            order.id,
            op_id,
            rq,
            ctx,
            production_batch_id=None,
            produced=produced,
            plan_lines=by_op.get(op_id, []),
            warnings=global_warnings,
        )
    return out


def build_work_map_for_batch(
    db: Session,
    batch: ProductionBatch,
    anchor: Order,
    ctx: SchedulingContext,
    *,
    produced: dict[WorkKey, float] | None = None,
) -> dict[int, OperationRemainingWork]:
    if produced is None:
        produced, _ = produced_qty_map(db)
    req = required_qty_for_batch(db, batch, anchor)
    all_lines = db.query(PlanLine).filter(
        PlanLine.production_batch_id == batch.id,
        PlanLine.mode.in_(["auto", "manual"]),
    ).all()
    by_op: dict[int, list[PlanLine]] = defaultdict(list)
    for pl in all_lines:
        by_op[pl.operation_id].append(pl)
    out: dict[int, OperationRemainingWork] = {}
    for op_id, rq in req.items():
        out[op_id] = operation_remaining(
            db,
            anchor.id,
            op_id,
            rq,
            ctx,
            production_batch_id=batch.id,
            produced=produced,
            plan_lines=by_op.get(op_id, []),
        )
    return out


def qty_and_setup_for_placement(
    work_map: dict[int, OperationRemainingWork],
    op: RoutingOperation,
) -> tuple[float, bool]:
    w = work_map.get(op.id)
    if not w:
        return 0.0, True
    return w.qty_to_schedule, w.setup_required
