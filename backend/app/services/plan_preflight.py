"""Otomatik planlama oncesi kontroller."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, WorkCenter
from app.schemas import AutoPlanRequest, DataFreshnessCheckpoint, PlanPreflightOut, PreflightNoCapacity, PreflightNoRouting
from app.services import capacity as cap
from app.services import production_batches as pbatches
from app.services.data_freshness import daily_freshness
from app.services.planning import _open_orders_with_ops, _selected_work_centers


def _items_without_routing(db: Session) -> list[PreflightNoRouting]:
    in_batch = pbatches.batched_order_ids(db)
    orders = (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations))
        .filter(Order.status == "open")
        .all()
    )
    by_item: dict[str, dict] = {}
    for o in orders:
        if o.id in in_batch or not o.item:
            continue
        if o.item.operations:
            continue
        row = by_item.setdefault(
            o.item.code,
            {"item_code": o.item.code, "item_name": o.item.name or "", "order_nos": []},
        )
        if o.order_no not in row["order_nos"]:
            row["order_nos"].append(o.order_no)

    for batch in pbatches.open_batches_with_ops(db):
        item = batch.item
        if not item or item.operations:
            continue
        row = by_item.setdefault(
            item.code,
            {"item_code": item.code, "item_name": item.name or "", "order_nos": []},
        )
        label = batch.batch_no or f"parti-{batch.id}"
        if label not in row["order_nos"]:
            row["order_nos"].append(label)

    out: list[PreflightNoRouting] = []
    for v in sorted(by_item.values(), key=lambda x: x["item_code"]):
        nos = v["order_nos"]
        out.append(
            PreflightNoRouting(
                item_code=v["item_code"],
                item_name=v["item_name"],
                order_count=len(nos),
                order_nos=nos[:20],
            )
        )
    return out


def _needed_hours_by_wc(db: Session, planned_wc_ids: set[int]) -> dict[int, float]:
    hours: dict[int, float] = defaultdict(float)
    for o in _open_orders_with_ops(db):
        if not o.item:
            continue
        for op in o.item.operations:
            if op.work_center_id in planned_wc_ids:
                hours[op.work_center_id] += op.hours_for(o.quantity)
    for batch in pbatches.open_batches_with_ops(db):
        if not batch.item:
            continue
        for op in batch.item.operations:
            if op.work_center_id in planned_wc_ids:
                hours[op.work_center_id] += op.hours_for(batch.quantity)
    return dict(hours)


def _capacity_issues(db: Session, req: AutoPlanRequest, planned_wcs: list[WorkCenter]) -> list[PreflightNoCapacity]:
    start = cap.week_start(req.start_week)
    planned_ids = {w.id for w in planned_wcs}
    needed = _needed_hours_by_wc(db, planned_ids)
    wc_by_id = {w.id: w for w in planned_wcs}
    out: list[PreflightNoCapacity] = []

    for wc_id, need_h in sorted(needed.items(), key=lambda x: wc_by_id.get(x[0]).code if wc_by_id.get(x[0]) else ""):
        if need_h < 1e-6:
            continue
        wc = wc_by_id.get(wc_id) or db.get(WorkCenter, wc_id)
        if not wc or not wc.is_active:
            continue
        cap_h = cap.planning_capacity_hours(db, wc, start)
        hc = cap.employee_count(db, wc)
        if cap_h >= 0.5:
            continue
        if wc.capacity_source == "machines" and hc == 0:
            detail = "Makine modunda seçili ama makinelere atanmış personel yok — haftalık kapasite 0."
        elif hc == 0:
            detail = "İş merkezine personel atanmamış — haftalık kapasite 0."
        else:
            detail = "Vardiya / haftalık iş gücü tanımı yetersiz — planlanabilir kapasite 0."
        out.append(
            PreflightNoCapacity(
                work_center_id=wc.id,
                work_center_code=wc.code,
                work_center_name=wc.name or "",
                needed_hours=round(need_h, 1),
                capacity_hours=round(cap_h, 1),
                headcount=hc,
                detail=detail,
            )
        )
    return out


def plan_preflight(db: Session, req: AutoPlanRequest) -> PlanPreflightOut:
    no_routing = _items_without_routing(db)
    planned_wcs = _selected_work_centers(db, req.work_center_ids)
    no_capacity = _capacity_issues(db, req, planned_wcs)
    freshness = daily_freshness(db)
    daily_data = [DataFreshnessCheckpoint(**c) for c in freshness["checkpoints"]]
    stale_daily = [c for c in daily_data if c.status != "ok"]

    order_count = len(_open_orders_with_ops(db)) + len(pbatches.open_batches_with_ops(db))
    can_plan = len(no_routing) == 0
    needs_capacity_ack = len(no_capacity) > 0
    needs_daily_data_ack = len(stale_daily) > 0

    return PlanPreflightOut(
        can_plan=can_plan,
        order_count=order_count,
        no_routing=no_routing,
        no_capacity=no_capacity,
        daily_data=daily_data,
        today=freshness["today"],
        needs_capacity_ack=needs_capacity_ack,
        needs_daily_data_ack=needs_daily_data_ack,
    )
