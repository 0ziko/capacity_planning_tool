"""Otomatik planlama oncesi kontroller."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from types import SimpleNamespace
import hashlib
import json

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import Item, Order, WorkCenter
from app.schemas import AutoPlanRequest, DataFreshnessCheckpoint, PlanPreflightOut, PlanPreflightScopeOut, PreflightNoCapacity, PreflightNoRouting, PreflightLaborWeek
from app.services import capacity as cap
from app.services import production_batches as pbatches
from app.services.data_freshness import daily_freshness
from app.services.bom_tree import explode_order, has_wip_structure, fg_has_wip_structure, is_wip_asm_link
from app.services.planning import (
    _open_orders_with_ops,
    _selected_work_centers,
    count_lines_in_replace_scope,
    plan_horizon_scope,
    replace_scope_modes,
)

_MODE_LABELS = {"auto": "Otomatik", "manual": "Manuel"}


def _preflight_inputs(db: Session):
    """Request-local graph; each shared WIP route is loaded only once."""
    orders = _open_orders_with_ops(db)
    batches = pbatches.open_batches_with_ops(db)
    items = {d.item.id: d.item for d in [*orders, *batches] if d.item}
    if items:
        db.query(Item).options(selectinload(Item.bom_lines)).filter(Item.id.in_(items)).all()
    codes = {
        bl.component_code.strip().upper()
        for item in items.values() if fg_has_wip_structure(item)
        for bl in item.bom_lines
        if is_wip_asm_link(bl.component_code, bl.source_wip, bl.recipe_seq)
    }
    wips = {}
    if codes:
        rows = db.query(Item).options(selectinload(Item.operations)).filter(func.upper(Item.code).in_(codes)).all()
        wips = {item.code.upper(): item for item in rows}
    return orders, batches, wips


def _items_without_routing(db: Session, inputs=None) -> list[PreflightNoRouting]:
    orders, batches, wip_cache = inputs if inputs is not None else _preflight_inputs(db)
    by_item: dict[str, dict] = {}
    for o in orders:
        if not o.item:
            continue
        if o.item.operations:
            continue
        row = by_item.setdefault(
            o.item.code,
            {"item_code": o.item.code, "item_name": o.item.name or "", "order_nos": []},
        )
        if o.order_no not in row["order_nos"]:
            row["order_nos"].append(o.order_no)

    for batch in batches:
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

    # A finished route alone is insufficient when a required WIP branch is absent.
    demands = [(o.item, o.order_no) for o in orders if o.item]
    demands += [(b.item, b.batch_no or f"parti-{b.id}") for b in batches if b.item]
    for item, label in demands:
        if not fg_has_wip_structure(item):
            continue
        for bl in item.bom_lines:
            if not is_wip_asm_link(bl.component_code, bl.source_wip, bl.recipe_seq):
                continue
            code = bl.component_code.strip().upper()
            wip = wip_cache.get(code)
            if wip and wip.operations:
                continue
            row = by_item.setdefault(code, {"item_code": code, "item_name": wip.name if wip else bl.component_name or "Eksik yarımamul", "order_nos": []})
            if label not in row["order_nos"]:
                row["order_nos"].append(label)

    out: list[PreflightNoRouting] = []
    # A line operation without a measured interval must never become zero work.
    from app.services.routing_resource import is_line_operation
    for item, label in demands:
        route_items = [item]
        if fg_has_wip_structure(item):
            route_items += [wip_cache[b.component_code.strip().upper()] for b in item.bom_lines
                            if is_wip_asm_link(b.component_code, b.source_wip, b.recipe_seq)
                            and b.component_code.strip().upper() in wip_cache]
        for route_item in route_items:
            if any(is_line_operation(op) and (not op.line_interval_sec or not op.cycle_time_sec) for op in route_item.operations):
                row = by_item.setdefault(route_item.code, {"item_code": route_item.code,
                    "item_name": (route_item.name or "") + " — hat çıkış aralığı eksik", "order_nos": []})
                if label not in row["order_nos"]:
                    row["order_nos"].append(label)
    from app.services.routing_resource import eligible_machine_ids
    for item, label in demands:
        for op in item.operations:
            if not is_line_operation(op):
                continue
            valid = {m.id for m in op.work_center.machines if m.is_active}
            if not valid.intersection(eligible_machine_ids(op)):
                row = by_item.setdefault(item.code, {"item_code": item.code,
                    "item_name": (item.name or "") + " — uygun aktif hat istasyonu eksik", "order_nos": []})
                if label not in row["order_nos"]:
                    row["order_nos"].append(label)
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


def _needed_hours_by_wc(db: Session, planned_wc_ids: set[int], inputs=None) -> dict[int, float]:
    orders, batches, wips = inputs if inputs is not None else _preflight_inputs(db)
    hours: dict[int, float] = defaultdict(float)
    def add(item, quantity):
        source = SimpleNamespace(item=item, quantity=quantity)
        jobs = [(item, quantity)]
        if has_wip_structure(source):
            expanded = explode_order(db, source, wip_items=wips)
            jobs = [(job.item, job.quantity) for job in expanded.wip_jobs]
            if expanded.finish_job:
                jobs.append((expanded.finish_job.item, expanded.finish_job.quantity))
        for route_item, qty in jobs:
            for op in route_item.operations:
                if op.work_center_id in planned_wc_ids:
                    hours[op.work_center_id] += op.hours_for(qty)

    for o in orders:
        if not o.item:
            continue
        add(o.item, o.quantity)
    for batch in batches:
        if not batch.item:
            continue
        add(batch.item, batch.quantity)
    return dict(hours)


def _capacity_issues(db: Session, req: AutoPlanRequest, planned_wcs: list[WorkCenter], inputs=None) -> tuple[list[PreflightNoCapacity], list[PreflightLaborWeek]]:
    scope = plan_horizon_scope(req.start_week, req.weeks)
    planned_ids = {w.id for w in planned_wcs}
    needed = _needed_hours_by_wc(db, planned_ids, inputs)
    wc_by_id = {w.id: w for w in planned_wcs}
    out: list[PreflightNoCapacity] = []
    missing: list[PreflightLaborWeek] = []

    fields = ("headcount", "efficient_hours_per_person", "working_days")
    for wc in sorted(wc_by_id.values(), key=lambda w: w.code):
        fields = () if wc.planning_mode == "line" else ("headcount", "efficient_hours_per_person", "working_days")
        calendar = cap.LaborCapacityCalendar(db, wc, scope.start, scope.end_inclusive)
        for i in range(req.weeks):
            week = scope.start + timedelta(weeks=i)
            ov = calendar.overrides.get(week)
            cap_h = cap.apply_planning_reserve(wc, calendar.capacity(week, week + timedelta(days=6)).capacity_hours)
            absent = [field for field in fields if ov is None or getattr(ov, field) is None]
            if wc.planning_mode == "line":
                from app.services.station_capacity import station_rows
                stations = station_rows(db, wc, week)
                absent = [f"{r['code']}: haftalık saat" for r in stations if r["working_hours"] is None]
                absent += [f"{r['code']}: gerekli ekip" for r in stations if not r["required_crew_size"]]
                if not stations:
                    absent = ["Aktif istasyon tanımı"]
                from app.models import PlanLine
                retained = db.query(PlanLine).filter(PlanLine.work_center_id == wc.id, PlanLine.week_start == week, PlanLine.machine_id.is_(None))
                if req.replace_existing:
                    retained = retained.filter(PlanLine.mode != "auto")
                if retained.first():
                    absent.append("Korunan eski planın istasyon ataması eksik; satırı silip yeniden planlayın")
            if absent:
                missing.append(PreflightLaborWeek(
                    work_center_id=wc.id, work_center_code=wc.code, week_start=week,
                    missing_fields=absent, capacity_hours=round(cap_h, 2),
                ))
            need_h = needed.get(wc.id, 0.0)
            if need_h < 1e-6 or cap_h > 1e-6:
                continue
            explicit_zero = ov is not None and any(getattr(ov, field) == 0 for field in fields)
            detail = (
                "Haftalık iş gücünde sıfır kişi, saat veya gün girilmiş; bu hafta kapasite yok."
                if explicit_zero else
                "Bu hafta iş gücü ve takvim birlikte değerlendirildiğinde planlanabilir kapasite yok."
            )
            if wc.planning_mode == "line":
                detail = "Bu hafta istasyonların toplam çalışma saati veya gerekli ekip girişi eksik/sıfır; hat kapasitesi yok."
            out.append(PreflightNoCapacity(
                work_center_id=wc.id, work_center_code=wc.code, work_center_name=wc.name or "",
                needed_hours=round(need_h, 1), capacity_hours=round(cap_h, 2),
                headcount=cap.daily_headcount(wc, week, calendar.emp, ov),
                detail=detail, week_start=week,
            ))
    return out, missing


def _replace_scope(db: Session, req: AutoPlanRequest, planned_wcs: list[WorkCenter]) -> PlanPreflightScopeOut:
    scope = plan_horizon_scope(req.start_week, req.weeks)
    wc_ids = [w.id for w in planned_wcs]
    modes = replace_scope_modes(replace_manual=False)
    lines_to_replace = 0
    if req.replace_existing and wc_ids:
        lines_to_replace = count_lines_in_replace_scope(db, wc_ids, scope, modes)
    replace_modes = [_MODE_LABELS[m] for m in modes] if req.replace_existing else []
    return PlanPreflightScopeOut(
        horizon_start=scope.start,
        horizon_end_inclusive=scope.end_inclusive,
        work_center_codes=[w.code for w in planned_wcs],
        replace_modes=replace_modes,
        lines_to_replace=lines_to_replace,
        replace_existing=req.replace_existing,
    )


def plan_preflight(db: Session, req: AutoPlanRequest) -> PlanPreflightOut:
    inputs = _preflight_inputs(db)
    no_routing = _items_without_routing(db, inputs)
    planned_wcs = _selected_work_centers(db, req.work_center_ids)
    no_capacity, missing_labor_weeks = _capacity_issues(db, req, planned_wcs, inputs)
    freshness = daily_freshness(db)
    daily_data = [DataFreshnessCheckpoint(**c) for c in freshness["checkpoints"]]
    stale_daily = [c for c in daily_data if c.status != "ok"]

    order_count = len(inputs[0]) + len(inputs[1])
    can_plan = len(no_routing) == 0
    needs_capacity_ack = bool(no_capacity or missing_labor_weeks)
    needs_daily_data_ack = len(stale_daily) > 0
    missing_headcounts = sorted((r.work_center_id, r.week_start.isoformat()) for r in missing_labor_weeks if "headcount" in r.missing_fields)
    missing_token = hashlib.sha256(json.dumps(missing_headcounts).encode()).hexdigest() if missing_headcounts else None

    return PlanPreflightOut(
        missing_headcount_token=missing_token,
        can_plan=can_plan,
        order_count=order_count,
        no_routing=no_routing,
        no_capacity=no_capacity,
        missing_labor_weeks=missing_labor_weeks,
        daily_data=daily_data,
        today=freshness["today"],
        needs_capacity_ack=needs_capacity_ack,
        needs_daily_data_ack=needs_daily_data_ack,
        replace_scope=_replace_scope(db, req, planned_wcs),
    )
