"""Otomatik / manuel planlama, haftalik yuk ve terminleme (lead time)."""

from collections import defaultdict
from dataclasses import dataclass
from app.services.station_capacity import load_budgets, station_room
from app.services.routing_resource import is_line_operation, planning_unit_hours, line_run_hours, line_quantity_for_hours
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import Item, Order, PlanLine, ProductionBatch, ProductionBatchOrder, RoutingOperation, WorkCenter
from app.services import production_batches as pbatches
from app.services.orders import effective_due, plan_line_priority_key
from app.services.material_schedule import recorded_material_note
from app.schemas import (
    AutoPlanRequest,
    ForecastFromLeadTimeIn,
    LeadTimeOut,
    LeadTimeRequest,
    LeadTimeStep,
    LoadDetailOut,
    LoadDetailParetoRow,
    LoadDetailRow,
    ForecastSummaryOut,
    ForecastLoadDetail,
    ManualPlanLineIn,
    PlanLineOut,
    WeekLoad,
    WeeklyOutputOut,
    WeeklyOutputWc,
    WorkCenterLoad,
)
from app.services import capacity as cap
from app.services import scenarios as scen
from app.services.bom_tree import explode_order, has_wip_structure
from app.services.gantt import actual_hours_by_week
from app.services.kpi_units import plan_and_output_kpis_for_range
from app.services.plan_draft import DraftLine
from app.services import operation_constraints as opcon
from app.services.planning_candidates import (
    REVENUE_MODE_LABEL,
    PlanningCandidate,
    build_planning_candidates,
    candidate_skipped_record,
    sort_candidates,
)
from app.services.remaining_work import (
    SchedulingContext,
    build_work_map_for_batch,
    build_work_map_for_order,
    operation_run_hours,
    qty_and_setup_for_placement,
)


def _selected_work_centers(db: Session, ids: list[int] | None) -> list[WorkCenter]:
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if ids:
        q = q.filter(WorkCenter.id.in_(ids))
    else:
        q = q.filter(WorkCenter.is_planned.is_(True))
    return q.order_by(WorkCenter.code).all()


def planned_hours_by_week(db: Session, wc_ids: list[int], start: date, end: date, mode: str | None = None) -> dict[tuple[int, date], float]:
    q = (
        db.query(PlanLine.work_center_id, PlanLine.week_start, func.sum(PlanLine.planned_hours))
        .filter(PlanLine.work_center_id.in_(wc_ids), PlanLine.week_start >= start, PlanLine.week_start <= end)
    )
    if mode:
        q = q.filter(PlanLine.mode == mode)
    q = q.group_by(PlanLine.work_center_id, PlanLine.week_start)
    return {(wc, wk): float(h or 0) for wc, wk, h in q.all()}


@dataclass
class Simulation:
    mode: str
    start: date
    weeks: list[date]
    work_centers: list[WorkCenter]
    lines: list[DraftLine]
    unplanned: list[dict]  # ufka sigmayan operasyon kalanlari
    skipped: list[dict]  # ciro modunda tamamen disarida birakilan siparisler
    capacity_hours: float  # ufuk icindeki toplam (manuel dusulmus) kapasite
    orders: list[Order]
    co_shipment_results: list[dict] | None = None
    co_shipment_exceptions: list[dict] | None = None
    material_unverified: bool = False
    material_unverified_order_ids: list[int] | None = None
    placement_notes: list[dict] | None = None  # JIT kaydirma / ara stok siniri / kayan adet notlari
    overtime_proposals: list[dict] | None = None  # fazla mesai önerileri (iş merkezi × hafta)

    @property
    def planned_hours(self) -> float:
        return sum(l.planned_hours for l in self.lines)


@dataclass(frozen=True)
class PlanHorizonScope:
    """Planlama ufku: [start, end_exclusive) yarı açık aralık (Pazartesi hafta başları)."""

    start: date
    end_exclusive: date

    @property
    def end_inclusive(self) -> date:
        return self.end_exclusive - timedelta(days=1)

    def contains_week(self, week_start: date) -> bool:
        wk = cap.week_start(week_start)
        return self.start <= wk < self.end_exclusive


def plan_horizon_scope(start_week: date, weeks: int) -> PlanHorizonScope:
    start = cap.week_start(start_week)
    return PlanHorizonScope(start=start, end_exclusive=start + timedelta(days=weeks * 7))


def replace_scope_modes(*, replace_manual: bool) -> list[str]:
    modes = ["auto"]
    if replace_manual:
        modes.append("manual")
    return modes


def query_lines_in_replace_scope(
    db: Session,
    wc_ids: list[int],
    scope: PlanHorizonScope,
    modes: list[str],
):
    return db.query(PlanLine).filter(
        PlanLine.work_center_id.in_(wc_ids),
        PlanLine.week_start >= scope.start,
        PlanLine.week_start < scope.end_exclusive,
        PlanLine.mode.in_(modes),
    )


def count_lines_in_replace_scope(
    db: Session,
    wc_ids: list[int],
    scope: PlanHorizonScope,
    modes: list[str],
) -> int:
    return query_lines_in_replace_scope(db, wc_ids, scope, modes).count()


def delete_lines_in_replace_scope(
    db: Session,
    wc_ids: list[int],
    scope: PlanHorizonScope,
    modes: list[str],
) -> int:
    return query_lines_in_replace_scope(db, wc_ids, scope, modes).delete(synchronize_session=False)


def _open_orders_with_ops(db: Session) -> list[Order]:
    in_batch = pbatches.batched_order_ids(db)
    rows = (
        db.query(Order)
        .options(selectinload(Order.item).selectinload(Item.operations), selectinload(Order.item).selectinload(Item.bom_lines))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
        .all()
    )
    return [o for o in rows if o.id not in in_batch]


def _place_quantity(
    anchor: Order,
    quantity: float,
    label: str,
    production_batch_id: int | None,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None = None,
    *,
    item: Item | None = None,
    semi_finished_code: str = "",
    min_start_idx: int = 0,
    ops: list[RoutingOperation] | None = None,
    qty_by_op: dict[int, float] | None = None,
    setup_by_op: dict[int, bool] | None = None,
    completed_by_op: dict[int, float] | None = None,
    assembly_outputs: dict[str, float] | None = None,
    assembly_wip_req: list[tuple[str, float]] | None = None,
    max_week_idx: int | None = None,
    material_unverified: bool = False,
) -> tuple[list[DraftLine], list[dict], int]:
    """Siparis veya uretim partisi miktari icin operasyonlari yerlestirir. Son hafta indeksini dondurur."""
    route_item = item or anchor.item
    route_ops = [op for op in (list(ops) if ops is not None else list(route_item.operations or [])) if op.work_center_id in wc_by_id]
    if not route_ops:
        return [], [], min_start_idx

    qty_map = qty_by_op or {op.id: quantity for op in route_ops}
    graph = opcon.build_sequential_graph(route_ops, qty_map)
    if graph.errors:
        raise opcon.RouteCycleError(graph.errors[0])

    completed_by_op = completed_by_op or {}
    cum_planned: dict[int, float] = {}
    pred_cum_by_week: dict[int, dict[int, float]] = {}
    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    prev_op: RoutingOperation | None = None
    last_idx = min_start_idx
    week_limit = len(weeks) - 1 if max_week_idx is None else min(max_week_idx, len(weeks) - 1)

    for op in route_ops:
        op_qty = qty_map.get(op.id, quantity)
        if op_qty <= 1e-6:
            continue
        from app.services.routing_resource import planning_unit_hours, planning_setup_hours
        if planning_unit_hours(op) <= 0:
            unplanned.append(opcon.unplanned_entry(
                order_no=label or anchor.order_no, item_code=route_item.code,
                semi_finished_code=semi_finished_code or op.semi_finished_code or "",
                operation_seq=op.seq, work_center_code=wc_by_id[op.work_center_id].code,
                hours=0, reason="operasyon_suresi_eksik"))
            prev_op = op
            continue
        setup = setup_by_op.get(op.id, True) if setup_by_op is not None else True
        setup_left = setup
        hours_per_unit = planning_unit_hours(op)
        pred_completed = completed_by_op.get(prev_op.id, 0.0) if prev_op else 0.0
        succ_completed = completed_by_op.get(op.id, 0.0)
        pred_required = qty_map.get(prev_op.id, 0.0) + pred_completed if prev_op else 0.0
        pred_week_map = dict(pred_cum_by_week.get(prev_op.id, {})) if prev_op else {}
        if pred_completed > 0:
            pred_week_map[0] = pred_week_map.get(0, 0.0) + pred_completed

        idx, wait_reason = opcon.resolve_min_start_for_op(
            op, route_item, prev_op, rules, pred_required, pred_week_map, weeks, min_start_idx
        )
        if idx >= len(weeks) or idx > week_limit:
            pred_avail_now = opcon.predecessor_available(prev_op.id, completed_by_op, cum_planned) if prev_op else op_qty
            if wait_reason:
                block_reason: opcon.BlockReason = wait_reason
            elif prev_op and pred_avail_now + 1e-6 < pred_required:
                block_reason = "oncul_eksik"
            else:
                block_reason = "kapasite_yetersiz"
            unplanned.append(
                opcon.unplanned_entry(
                    order_no=label or anchor.order_no,
                    item_code=route_item.code,
                    semi_finished_code=semi_finished_code,
                    operation_seq=op.seq,
                    work_center_code=wc_by_id[op.work_center_id].code,
                    hours=round(operation_run_hours(op, op_qty - cum_planned.get(op.id, 0.0), setup_required=setup_left and cum_planned.get(op.id, 0) <= 1e-6), 8),
                    reason=block_reason,
                )
            )
            prev_op = op
            continue

        first_idx = None
        op_last_idx = None
        qty_left = op_qty - cum_planned.get(op.id, 0.0)

        while qty_left > 1e-6 and idx <= week_limit:
            rule = rules.get(route_item, prev_op, op) if rules and prev_op else scen.Rule()
            # Only output available by this week can feed the successor.
            pred_avail = opcon.cumulative_by_week(pred_week_map, idx) if prev_op else op_qty
            qty_cap = opcon.max_successor_qty(
                rule,
                pred_required,
                pred_avail,
                op_qty + succ_completed,
                cum_planned.get(op.id, 0.0) + succ_completed,
            )
            if assembly_outputs is not None and assembly_wip_req:
                qty_cap = min(
                    qty_cap,
                    opcon.assembly_cap_qty(assembly_outputs, assembly_wip_req, op_qty) - cum_planned.get(op.id, 0.0),
                )
            if qty_cap <= 1e-6:
                if idx >= week_limit:
                    reason: opcon.BlockReason = "yarimamul_eksik" if assembly_outputs is not None and assembly_wip_req and opcon.assembly_cap_qty(assembly_outputs, assembly_wip_req, op_qty) <= cum_planned.get(op.id, 0.0) + 1e-6 else "oncul_eksik" if prev_op and pred_avail + 1e-6 < pred_required else "kapasite_yetersiz"
                    if wait_reason:
                        reason = wait_reason
                    unplanned.append(
                        opcon.unplanned_entry(
                            order_no=label or anchor.order_no,
                            item_code=route_item.code,
                            semi_finished_code=semi_finished_code,
                            operation_seq=op.seq,
                            work_center_code=wc_by_id[op.work_center_id].code,
                            hours=round(operation_run_hours(op, qty_left, setup_required=setup_left), 8),
                            reason=reason,
                        )
                    )
                    break
                idx += 1
                continue

            week_qty_budget = min(qty_left, qty_cap)
            run_hours = week_qty_budget * hours_per_unit
            if setup_left and cum_planned.get(op.id, 0.0) <= 1e-6:
                run_hours += planning_setup_hours(op)
            if is_line_operation(op):
                run_hours = line_run_hours(op, week_qty_budget)

            wk = weeks[idx]
            avail = remaining[(op.work_center_id, wk)]
            machine_id = None
            if is_line_operation(op):
                machine_id, avail = station_room(op, wk, remaining)
            if avail <= 1e-6:
                if idx >= week_limit:
                    unplanned.append(
                        opcon.unplanned_entry(
                            order_no=label or anchor.order_no,
                            item_code=route_item.code,
                            semi_finished_code=semi_finished_code,
                            operation_seq=op.seq,
                            work_center_code=wc_by_id[op.work_center_id].code,
                            hours=round(run_hours, 8),
                            reason="kapasite_yetersiz",
                        )
                    )
                    break
                idx += 1
                continue

            if is_line_operation(op):
                from app.services.routing_resource import station_units

                su = station_units(op, machine_id)  # seçilen istasyonun konveyör dizilimi
                placed_qty = min(week_qty_budget, line_quantity_for_hours(op, avail, units=su))
                take = line_run_hours(op, placed_qty, units=su)
                setup_left = False
            elif run_hours > avail + 1e-6:
                if setup_left and cum_planned.get(op.id, 0.0) <= 1e-6:
                    setup_h = planning_setup_hours(op)
                    if avail <= setup_h + 1e-6:
                        idx += 1
                        continue
                    prod_h = avail - setup_h
                    placed_qty = min(week_qty_budget, prod_h / hours_per_unit if hours_per_unit > 0 else 0)
                    take = setup_h + placed_qty * hours_per_unit
                    setup_left = False
                else:
                    placed_qty = min(week_qty_budget, avail / hours_per_unit if hours_per_unit > 0 else 0)
                    take = placed_qty * hours_per_unit
            else:
                placed_qty = week_qty_budget
                take = run_hours
                setup_left = False

            if placed_qty <= 1e-6:
                idx += 1
                continue

            lines.append(
                DraftLine(
                    order=anchor,
                    order_id=anchor.id,
                    operation_id=op.id,
                    work_center_id=op.work_center_id,
                    week_start=wk,
                    machine_id=machine_id,
                    planned_hours=round(take, 10),
                    planned_qty=round(placed_qty, 2),
                    production_batch_id=production_batch_id,
                    label=label or anchor.order_no,
                    semi_finished_code=semi_finished_code or op.semi_finished_code or "",
                    material_unverified=material_unverified,
                )
            )
            remaining[(op.work_center_id, wk)] -= take
            if machine_id is not None:
                remaining[("machine", machine_id, wk)] -= take
            cum_planned[op.id] = cum_planned.get(op.id, 0.0) + placed_qty
            pred_cum_by_week.setdefault(op.id, {})[idx] = pred_cum_by_week.get(op.id, {}).get(idx, 0.0) + placed_qty
            qty_left -= placed_qty
            if first_idx is None:
                first_idx = idx
            op_last_idx = idx
            last_idx = max(last_idx, idx)
            if qty_left > 1e-6 and (not is_line_operation(op) or station_room(op, wk, remaining)[1] <= 1e-6):
                idx += 1

        if qty_left > 1e-6 and not any(u.get("operation_seq") == op.seq for u in unplanned):
            pred_avail_now = opcon.predecessor_available(prev_op.id, completed_by_op, cum_planned) if prev_op else op_qty
            tail_reason: opcon.BlockReason = (
                "oncul_eksik"
                if prev_op and pred_avail_now + 1e-6 < pred_required
                else "kapasite_yetersiz"
            )
            unplanned.append(
                opcon.unplanned_entry(
                    order_no=label or anchor.order_no,
                    item_code=route_item.code,
                    semi_finished_code=semi_finished_code,
                    operation_seq=op.seq,
                    work_center_code=wc_by_id[op.work_center_id].code,
                    hours=round(operation_run_hours(op, qty_left, setup_required=setup_left), 8),
                    reason=tail_reason,
                )
            )
        prev_op = op

    return lines, unplanned, last_idx


def _completed_qty_by_op(produced_map: dict | None, order_id: int) -> dict[int, float]:
    if not produced_map:
        return {}
    return {op_id: float(qty) for (oid, op_id), qty in produced_map.items() if oid == order_id}


def _place_order(
    db: Session,
    o: Order,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None = None,
    *,
    sched_ctx: SchedulingContext | None = None,
    work_map: dict | None = None,
    produced_map: dict | None = None,
    material_min_idx: int = 0,
    material_unverified: bool = False,
    qty_factor: float = 1.0,
    max_week_idx: int | None = None,
    prep: bool = False,
) -> tuple[list[DraftLine], list[dict]]:
    if sched_ctx is None:
        sched_ctx = SchedulingContext(horizon_start=weeks[0], horizon_end_exclusive=weeks[-1] + timedelta(days=7))
    if work_map is None:
        work_map = build_work_map_for_order(db, o, sched_ctx)

    def _maps_for_ops(ops: list[RoutingOperation]) -> tuple[dict[int, float], dict[int, bool]]:
        qty: dict[int, float] = {}
        setup: dict[int, bool] = {}
        for op in ops:
            q, s = qty_and_setup_for_placement(work_map, op)
            qty[op.id] = q * qty_factor if qty_factor < 1.0 else q
            setup[op.id] = s
        return qty, setup

    completed = _completed_qty_by_op(produced_map, o.id)
    if has_wip_structure(o):
        jobs = explode_order(db, o)
        all_lines: list[DraftLine] = []
        all_unplanned: list[dict] = []
        wip_end = 0
        wip_placed: dict[int, float] = {}
        for job in jobs.wip_jobs:
            rem_ops = [op for op in sorted(job.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
            if not rem_ops:
                continue
            qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
            if sum(qty_by_op.values()) <= 1e-6:
                continue
            lbl = f"{o.order_no}/{job.label_suffix}" if job.label_suffix else o.order_no
            ls, un, end_idx = _place_quantity(
                o,
                job.quantity * qty_factor,
                lbl,
                None,
                wc_by_id,
                weeks,
                remaining,
                rules,
                item=job.item,
                semi_finished_code=job.semi_finished_code,
                min_start_idx=material_min_idx,
                ops=rem_ops,
                qty_by_op=qty_by_op,
                setup_by_op=setup_by_op,
                completed_by_op=completed,
                material_unverified=material_unverified,
                max_week_idx=max_week_idx,
            )
            all_lines.extend(ls)
            all_unplanned.extend(un)
            for ln in ls:
                wip_placed[ln.operation_id] = wip_placed.get(ln.operation_id, 0.0) + ln.planned_qty
            wip_end = max(wip_end, end_idx)
        if jobs.finish_job:
            rem_ops = [op for op in sorted(jobs.finish_job.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
            if prep:
                rem_ops = rem_ops[:-1]  # hazırlık: bitmiş ürün adımı yazılmaz
            if rem_ops:
                qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
                if sum(qty_by_op.values()) > 1e-6:
                    assembly_outputs: dict[str, float] = {}
                    wip_req: list[tuple[str, float]] = []
                    for job in jobs.wip_jobs:
                        wops = sorted(job.item.operations or [], key=lambda x: x.seq)
                        if not wops:
                            continue
                        last_op = wops[-1]
                        assembly_outputs[job.semi_finished_code] = completed.get(last_op.id, 0.0) + wip_placed.get(last_op.id, 0.0)
                        wip_req.append((job.semi_finished_code, job.quantity * qty_factor))
                    ls, un, _ = _place_quantity(
                        o,
                        jobs.finish_job.quantity * qty_factor,
                        o.order_no,
                        None,
                        wc_by_id,
                        weeks,
                        remaining,
                        rules,
                        item=jobs.finish_job.item,
                        semi_finished_code="",
                        min_start_idx=max(wip_end, material_min_idx),
                        ops=rem_ops,
                        qty_by_op=qty_by_op,
                        setup_by_op=setup_by_op,
                        completed_by_op=completed,
                        assembly_outputs=assembly_outputs,
                        assembly_wip_req=wip_req,
                        material_unverified=material_unverified,
                        max_week_idx=max_week_idx,
                    )
                    all_lines.extend(ls)
                    all_unplanned.extend(un)
        return all_lines, all_unplanned
    rem_ops = [op for op in sorted(o.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
    if prep:
        rem_ops = rem_ops[:-1]  # hazırlık: son operasyon (bitmiş ürün) yazılmaz
    if not rem_ops:
        return [], []
    qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
    if sum(qty_by_op.values()) <= 1e-6:
        return [], []
    ls, un, _ = _place_quantity(
        o,
        o.quantity * qty_factor,
        o.order_no,
        None,
        wc_by_id,
        weeks,
        remaining,
        rules,
        ops=rem_ops,
        qty_by_op=qty_by_op,
        setup_by_op=setup_by_op,
        completed_by_op=completed,
        min_start_idx=material_min_idx,
        material_unverified=material_unverified,
        max_week_idx=max_week_idx,
    )
    return ls, un


def _order_hours(db: Session, o: Order, wc_by_id: dict[int, WorkCenter]) -> float:
    if has_wip_structure(o):
        jobs = explode_order(db, o)
        total = 0.0
        for job in jobs.wip_jobs:
            total += sum(op.hours_for(job.quantity) for op in job.item.operations if op.work_center_id in wc_by_id)
        if jobs.finish_job:
            total += sum(op.hours_for(jobs.finish_job.quantity) for op in jobs.finish_job.item.operations if op.work_center_id in wc_by_id)
        return total
    return sum(op.hours_for(o.quantity) for op in o.item.operations if op.work_center_id in wc_by_id)


def _place_batch(
    db: Session,
    batch: ProductionBatch,
    anchor: Order,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
    sched_ctx: SchedulingContext,
    produced_map: dict,
    *,
    material_min_idx: int = 0,
    material_unverified: bool = False,
    max_week_idx: int | None = None,
) -> tuple[list[DraftLine], list[dict], int]:
    if not batch.item:
        return [], [], 0
    work_map = build_work_map_for_batch(db, batch, anchor, sched_ctx, produced=produced_map)
    ratio = float(batch.quantity or 0) / float(anchor.quantity or 1) if anchor.quantity else 1.0

    def _maps_for_ops(ops: list[RoutingOperation]) -> tuple[dict[int, float], dict[int, bool]]:
        qty: dict[int, float] = {}
        setup: dict[int, bool] = {}
        for op in ops:
            q, s = qty_and_setup_for_placement(work_map, op)
            qty[op.id] = q
            setup[op.id] = s
        return qty, setup

    completed = _completed_qty_by_op(produced_map, anchor.id)
    if has_wip_structure(anchor):
        jobs = explode_order(db, anchor)
        all_lines: list[DraftLine] = []
        all_unplanned: list[dict] = []
        wip_end = 0
        wip_placed: dict[int, float] = {}
        for job in jobs.wip_jobs:
            rem_ops = [op for op in sorted(job.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
            if not rem_ops:
                continue
            qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
            if sum(qty_by_op.values()) <= 1e-6:
                continue
            job_qty = job.quantity * ratio
            lbl = f"{batch.batch_no}/{job.label_suffix}" if job.label_suffix else batch.batch_no
            ls, un, end_idx = _place_quantity(
                anchor,
                job_qty,
                lbl,
                batch.id,
                wc_by_id,
                weeks,
                remaining,
                rules,
                item=job.item,
                semi_finished_code=job.semi_finished_code,
                min_start_idx=material_min_idx,
                ops=rem_ops,
                qty_by_op=qty_by_op,
                setup_by_op=setup_by_op,
                completed_by_op=completed,
                material_unverified=material_unverified,
                max_week_idx=max_week_idx,
            )
            all_lines.extend(ls)
            all_unplanned.extend(un)
            for ln in ls:
                wip_placed[ln.operation_id] = wip_placed.get(ln.operation_id, 0.0) + ln.planned_qty
            wip_end = max(wip_end, end_idx)
        if jobs.finish_job:
            rem_ops = [op for op in sorted(jobs.finish_job.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
            if rem_ops:
                qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
                if sum(qty_by_op.values()) > 1e-6:
                    assembly_outputs: dict[str, float] = {}
                    wip_req: list[tuple[str, float]] = []
                    for job in jobs.wip_jobs:
                        wops = sorted(job.item.operations or [], key=lambda x: x.seq)
                        if not wops:
                            continue
                        last_op = wops[-1]
                        assembly_outputs[job.semi_finished_code] = completed.get(last_op.id, 0.0) + wip_placed.get(last_op.id, 0.0)
                        wip_req.append((job.semi_finished_code, job.quantity * ratio))
                    ls, un, end_idx = _place_quantity(
                        anchor,
                        float(batch.quantity or 0),
                        batch.batch_no,
                        batch.id,
                        wc_by_id,
                        weeks,
                        remaining,
                        rules,
                        item=jobs.finish_job.item,
                        semi_finished_code="",
                        min_start_idx=max(wip_end, material_min_idx),
                        ops=rem_ops,
                        qty_by_op=qty_by_op,
                        setup_by_op=setup_by_op,
                        completed_by_op=completed,
                        assembly_outputs=assembly_outputs,
                        assembly_wip_req=wip_req,
                        material_unverified=material_unverified,
                        max_week_idx=max_week_idx,
                    )
                    all_lines.extend(ls)
                    all_unplanned.extend(un)
                    wip_end = max(wip_end, end_idx)
        return all_lines, all_unplanned, wip_end

    rem_ops = [op for op in sorted(batch.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
    if not rem_ops:
        return [], [], 0
    qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
    if sum(qty_by_op.values()) <= 1e-6:
        return [], [], 0
    return _place_quantity(
        anchor,
        batch.quantity,
        batch.batch_no,
        batch.id,
        wc_by_id,
        weeks,
        remaining,
        rules,
        item=batch.item,
        ops=rem_ops,
        qty_by_op=qty_by_op,
        setup_by_op=setup_by_op,
        completed_by_op=completed,
        min_start_idx=material_min_idx,
        material_unverified=material_unverified,
        max_week_idx=max_week_idx,
    )


def _batch_hours(batch: ProductionBatch, wc_by_id: dict[int, WorkCenter]) -> float:
    if not batch.orders:
        return 0.0
    item = batch.item
    return sum(op.hours_for(batch.quantity) for op in item.operations if op.work_center_id in wc_by_id)


def _material_context(
    candidate: PlanningCandidate,
    weeks: list[date],
    policy: str,
) -> tuple[int, bool, dict | None]:
    from app.services.material_schedule import material_gate_for_batch, material_gate_for_order, week_index

    if candidate.kind == "order" and candidate.order:
        gate = material_gate_for_order(candidate.order, policy=policy)
    elif candidate.batch:
        gate = material_gate_for_batch(candidate.batch, policy=policy)
    else:
        return 0, False, None
    if gate.blocks_planning:
        item_code = candidate.route_item.code if candidate.route_item else ""
        return (
            0,
            False,
            {
                "order_no": candidate.display_code,
                "item_code": item_code,
                "reason": "material_date_missing" if gate.status == "expected" else "malzeme_unknown_strict",
                "detail": "Beklenen malzemenin hazır olacağı tarih girilmemiş." if gate.status == "expected" else "Malzeme durumu bilinmiyor; strict modda planlanmaz.",
            },
        )
    idx = week_index(weeks, gate.earliest_week) if gate.earliest_week else 0
    return idx, gate.material_unverified, None


def _place_candidate(
    db: Session,
    candidate: PlanningCandidate,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
    sched_ctx: SchedulingContext,
    produced_map: dict,
    *,
    material_policy: str = "conditional",
    qty_factor: float = 1.0,
    max_week_idx: int | None = None,
    prep: bool = False,
) -> tuple[list[DraftLine], list[dict]]:
    min_idx, m_unv, blocked = _material_context(candidate, weeks, material_policy)
    if blocked:
        return [], [blocked]
    if candidate.kind == "order" and candidate.order:
        wm = build_work_map_for_order(db, candidate.order, sched_ctx, produced=produced_map)
        return _place_order(
            db,
            candidate.order,
            wc_by_id,
            weeks,
            remaining,
            rules,
            sched_ctx=sched_ctx,
            work_map=wm,
            produced_map=produced_map,
            material_min_idx=min_idx,
            material_unverified=m_unv,
            qty_factor=qty_factor,
            max_week_idx=max_week_idx,
            prep=prep,
        )
    if candidate.batch and candidate.anchor_order:
        if prep:
            return [], []  # üretim partileri için hazırlık dolgusu yok
        ls, un, _ = _place_batch(
            db,
            candidate.batch,
            candidate.anchor_order,
            wc_by_id,
            weeks,
            remaining,
            rules,
            sched_ctx,
            produced_map,
            material_min_idx=min_idx,
            material_unverified=m_unv,
            max_week_idx=max_week_idx,
        )
        return ls, un
    return [], []


def _finish_ops(order: Order, wc_by_id: dict[int, WorkCenter]) -> list[RoutingOperation]:
    ops = (order.item.operations if order.item is not None else None) or []
    return [op for op in sorted(ops, key=lambda x: x.seq) if op.work_center_id in wc_by_id]


def _finish_output_qty(order: Order, wc_by_id: dict[int, WorkCenter], lines: list[DraftLine]) -> float:
    """Yerleşen satırlardan bitmiş ürün (rota son operasyonu) adedi."""
    ops = _finish_ops(order, wc_by_id)
    if not ops:
        return 0.0
    last_id = ops[-1].id
    return sum(l.planned_qty for l in lines if l.operation_id == last_id and l.order_id == order.id)


def _finish_remaining_qty(db: Session, order: Order, wc_by_id: dict[int, WorkCenter], sched_ctx: SchedulingContext, produced_map: dict) -> float:
    ops = _finish_ops(order, wc_by_id)
    if not ops:
        return 0.0
    wm = build_work_map_for_order(db, order, sched_ctx, produced=produced_map)
    q, _ = qty_and_setup_for_placement(wm, ops[-1])
    return float(q or 0.0)


_PASS_THROUGH_REASONS = {"operasyon_suresi_eksik", "rota_dongusu", "material_date_missing", "malzeme_unknown_strict"}


def _candidate_full_hours(db: Session, o: Order, wc_by_id: dict[int, WorkCenter], sched_ctx: SchedulingContext, produced_map: dict) -> tuple[dict[int, float], dict[int, RoutingOperation]]:
    """Siparişin (parçalar + bitiş rotası) kalan işinin operasyon bazında tam saati; plansız muhasebesi için."""
    from app.services.routing_resource import planning_setup_hours

    wm = build_work_map_for_order(db, o, sched_ctx, produced=produced_map)
    if has_wip_structure(o):
        jobs = explode_order(db, o)
        items = [j.item for j in jobs.wip_jobs] + ([jobs.finish_job.item] if jobs.finish_job else [])
    else:
        items = [o.item]
    full: dict[int, float] = {}
    ops_by_id: dict[int, RoutingOperation] = {}
    for it in items:
        for op in sorted((it.operations if it is not None else None) or [], key=lambda x: x.seq):
            if op.work_center_id not in wc_by_id:
                continue
            q, setup = qty_and_setup_for_placement(wm, op)
            if q <= 1e-6 or planning_unit_hours(op) <= 0:
                continue
            h = line_run_hours(op, q) if is_line_operation(op) else q * planning_unit_hours(op) + (planning_setup_hours(op) if setup else 0.0)
            full[op.id] = full.get(op.id, 0.0) + h
            ops_by_id[op.id] = op
    return full, ops_by_id


def _place_candidate_balanced(
    db: Session,
    c: PlanningCandidate,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict,
    pool,
    rules: scen.RuleLookup | None,
    sched_ctx: SchedulingContext,
    produced_map: dict,
    *,
    material_policy: str,
    slip_mode: str,
    use_overtime: bool,
) -> tuple[list[DraftLine], list[dict], dict, list[dict]]:
    """Dengeli yerleşim (Aşama 2).

    1. Hedef haftaya (etkin termin − teslim tamponu) kadar normal kapasiteyle çıkabilecek bitmiş ürün adedi
       deneme yerleşimiyle bulunur; tüm parça ve operasyonlar o adette yerleşir (yetim parça üretilmez).
    2. Kalan adet için fazla mesai havuzu açılır (aynı hedef hafta); kullanılan saat öneri olur.
    3. Hâlâ kalan adet: chain ⇒ hedef sonrasına dengeli yerleşir (satır etiketi slip, zincir etkisi görünür);
       defer ⇒ plana yazılmaz, tahmini bitişle plansız (termin_kaydi) raporlanır.
    Termini ufuk başından önce olan sipariş: hedef yok; ufuk içinde dengeli ASAP, fazla mesai kullanılmaz
    (önce revize termin girilmeli). Üretim partileri eski yoldan yerleşir.
    Döner: satırlar, plansız, yeni normal kalan kapasite, notlar (kind=slip)."""
    from dataclasses import replace as _replace

    from app.services import overtime_pool as otp

    def _place(rem: dict, factor: float, max_idx: int | None):
        return _place_candidate(db, c, wc_by_id, weeks, rem, rules, sched_ctx, produced_map,
                                material_policy=material_policy, qty_factor=factor, max_week_idx=max_idx)

    o = c.order
    if c.kind != "order" or o is None or o.item is None:
        ls, un = _place(remaining, 1.0, None)
        return ls, un, remaining, []
    F = _finish_remaining_qty(db, o, wc_by_id, sched_ctx, produced_map)
    if F <= otp.EPS:
        ls, un = _place(remaining, 1.0, None)
        return ls, un, remaining, []
    tgt = otp.target_week_index(weeks, c.effective_due_date)
    limit = 0 if tgt < 0 else tgt  # termini geçmiş: hedef = en erken hafta; sığmayan kalan (mesai dahil) aşağıda ele alınır
    lines: list[DraftLine] = []
    unplanned: list[dict] = []

    trial = dict(remaining)
    ls_a, un_a = _place(trial, 1.0, limit)
    A = _finish_output_qty(o, wc_by_id, ls_a)
    if A >= F - otp.EPS or any(u.get("reason") in _PASS_THROUGH_REASONS and not u.get("operation_seq") for u in un_a):
        return ls_a, un_a, trial, []  # tam yerleşti veya aday düzeyinde engel (malzeme / rota döngüsü)
    bottleneck = next((u.get("work_center_code") for u in un_a if u.get("reason") == "kapasite_yetersiz"), None) \
        or (un_a[0].get("work_center_code") if un_a else None)
    if A > otp.EPS:
        ls, _un = _place(remaining, A / F, limit)
        lines.extend(ls)
    rem_f = max(0.0, 1.0 - A / F)

    B = 0.0
    if tgt >= 0 and use_overtime and rem_f > otp.EPS and pool is not None and pool.has_capacity():
        merged = pool.merged(remaining)
        trial = dict(merged)
        ls_b, _ = _place(trial, rem_f, limit)
        B = _finish_output_qty(o, wc_by_id, ls_b)
        if B > otp.EPS:
            if B < rem_f * F - otp.EPS:
                trial = dict(merged)
                ls_b, _ = _place(trial, B / F, limit)
            remaining, ot_cells = pool.split_usage(remaining, merged, trial)
            lines.extend(_replace(l, tag="overtime") if (l.work_center_id, l.week_start) in ot_cells else l for l in ls_b)
            rem_f = max(0.0, rem_f - B / F)

    slip_qty = rem_f * F
    est_week: date | None = None
    slip_hours = 0.0
    ot_saved_weeks = 0
    ot_extra_qty = 0.0
    if rem_f > otp.EPS:
        # Kalan adet de dengeli: ufuk içinde normal kapasiteyle çıkabilecek adet (C) ve bitiş haftası deneme ile bulunur.
        # Termini geçmiş siparişte de aynı yol (hedef = en erken). Sığmayan kısım kök neden kayıtlarıyla plansız kalır.
        trial = dict(remaining)
        ls_t, un_t = _place(trial, rem_f, None)
        C = _finish_output_qty(o, wc_by_id, ls_t)
        est_n = max((l.week_start for l in ls_t), default=None)
        merged = None
        # Fazla mesai: hedefi tam kurtarmasa da gecikmeyi azaltıyorsa (daha çok bitmiş adet ya da daha erken bitiş) kabul.
        # Termini geçmiş siparişler termin sırasında en önde olduğu için havuzdan ilk onlar yararlanır. 'Komple kaydır' modunda
        # kalan adet plana yazılmadığı için mesai de değerlendirilmez.
        if use_overtime and (slip_mode == "chain" or tgt < 0) and pool is not None and pool.has_capacity():
            m_try = pool.merged(remaining)
            trial_o = dict(m_try)
            ls_o, un_o = _place(trial_o, rem_f, None)
            Co = _finish_output_qty(o, wc_by_id, ls_o)
            est_o = max((l.week_start for l in ls_o), default=None)
            improves = Co > C + otp.EPS or (Co >= C - otp.EPS and est_o is not None and est_n is not None and est_o < est_n)
            if improves:
                ot_extra_qty = max(0.0, Co - C)
                ot_saved_weeks = ((est_n - est_o).days // 7) if (est_n and est_o and est_o < est_n) else 0
                ls_t, un_t, trial, C, merged = ls_o, un_o, trial_o, Co, m_try
        ot_cells2: set = set()
        if C >= rem_f * F - otp.EPS:
            ls_c, un_c, after = ls_t, un_t, trial
        elif C > otp.EPS:
            after = dict(merged if merged is not None else remaining)
            ls_c, _ = _place(after, C / F, None)
            un_c = un_t
        else:
            ls_c, un_c, after = [], un_t, dict(remaining)
        if merged is not None and ls_c:
            after, ot_cells2 = pool.split_usage(remaining, merged, after)
        slip_hours = sum(l.planned_hours for l in ls_c)
        est_week = max((l.week_start for l in ls_c), default=None)
        if (slip_mode == "chain" or tgt < 0) and ls_c:
            remaining = after
            lines.extend(_replace(l, tag=("overtime" if (l.work_center_id, l.week_start) in ot_cells2 else ("slip" if tgt >= 0 else ""))) for l in ls_c)
        # Plansız muhasebesi: tam ihtiyaç − yerleşen. Kök neden (kapasite/yarımamül/bekleme) deneme kayıtlarından,
        # geri kalanı 'darboğaz bekliyor' (tek sayım; her operasyonun saati bir kez yazılır).
        deferred: dict[int, float] = defaultdict(float)
        if tgt >= 0 and slip_mode != "chain":
            for l in ls_c:
                deferred[l.operation_id] += l.planned_hours
        root: dict[tuple[int, str], str] = {}
        seen_pt: set = set()
        for u in list(un_t) + list(un_a):  # veri eksiği kayıtları (süre 0, döngü) aynen aktarılır
            if u.get("reason") in _PASS_THROUGH_REASONS:
                k = (u.get("operation_seq"), u.get("work_center_code"), u.get("reason"))
                if k not in seen_pt:
                    seen_pt.add(k)
                    unplanned.append(u)
        for u in list(un_a) + list(un_t):
            root[(int(u.get("operation_seq") or 0), u.get("work_center_code") or "")] = u.get("reason") or "kapasite_yetersiz"
        full, ops_by_id = _candidate_full_hours(db, o, wc_by_id, sched_ctx, produced_map)
        placed: dict[int, float] = defaultdict(float)
        for l in lines:
            placed[l.operation_id] += l.planned_hours
        for op_id, fh in full.items():
            cut = fh - placed.get(op_id, 0.0)
            if cut <= 0.01:  # yuvarlama artığı (36 sn altı) plansız sayılmaz
                continue
            op = ops_by_id[op_id]
            wc_code = wc_by_id[op.work_center_id].code
            common = dict(order_no=c.display_code, item_code=c.route_item.code if c.route_item else "", semi_finished_code=op.semi_finished_code or "",
                          operation_seq=op.seq, work_center_code=wc_code)
            d = min(cut, deferred.get(op_id, 0.0))
            if d > 1e-6:
                entry = opcon.unplanned_entry(hours=round(d, 8), reason="termin_kaydi", **common)  # type: ignore[arg-type]
                entry["detail"] = f"Hedef tarihe sığmayan {slip_qty:.0f} adet plana yazılmadı; tahmini bitiş haftası {est_week.isoformat() if est_week else '-'}; darboğaz {bottleneck or '-'}"
                unplanned.append(entry)
                cut -= d
            if cut > 1e-6:
                reason = root.get((op.seq, wc_code)) or "darbogaz_bekliyor"
                entry = opcon.unplanned_entry(hours=round(cut, 8), reason=reason, **common)  # type: ignore[arg-type]
                if reason == "darbogaz_bekliyor":
                    entry["detail"] = f"{bottleneck or 'darboğaz'} yetişmediği için bu adım dengeli olarak kısıldı"
                    entry["blocked_by"] = bottleneck
                unplanned.append(entry)
    note = {
        "kind": "slip", "label": c.display_code, "order_id": o.id,
        "detail": (f"Hedefte {A:.0f}/{F:.0f} adet" + (f", fazla mesaiyle +{B:.0f}" if B > otp.EPS else "")
                   + (f"; kalan {slip_qty:.0f} adet " + ("ufka sığmadı" if tgt < 0 else ("hedef sonrasına yerleşti" if slip_mode == "chain" else "plana yazılmadı"))
                      + (f" (tahmini bitiş {est_week.isoformat()})" if est_week else "") if slip_qty > otp.EPS else "")
                   + (f"; darboğaz {bottleneck}" if bottleneck else "")),
        "hours": round(slip_hours, 2), "qty": round(slip_qty, 2),
        "target_qty": round(A, 2), "overtime_qty": round(B, 2), "remaining_qty": round(F, 2),
        "bottleneck": bottleneck, "est_finish_week": est_week.isoformat() if est_week else None,
        "overdue": tgt < 0, "slip_mode": slip_mode,
        "unplaced_qty": round(max(F - _finish_output_qty(o, wc_by_id, lines), 0.0), 2),  # hazırlık dolgusu adayı
        "overtime_saved_weeks": ot_saved_weeks, "overtime_extra_qty": round(ot_extra_qty, 2),
    }
    return lines, unplanned, remaining, [note]


def _note_prep(prep_todo: list, c: PlanningCandidate, notes_: list[dict]) -> None:
    for n in notes_:
        if n.get("kind") == "slip" and (n.get("unplaced_qty") or 0) > 1e-6 and (n.get("remaining_qty") or 0) > 1e-6:
            prep_todo.append((c, min(1.0, float(n["unplaced_qty"]) / float(n["remaining_qty"]))))


def _reduce_unplanned(unplanned: list[dict], order_no: str, prep_lines: list[DraftLine], wc_by_id: dict[int, WorkCenter]) -> None:
    """Hazırlıkta yerleşen saat, aynı siparişin plansız kayıtlarından (aynı iş merkezi) düşülür; tek sayım korunur."""
    placed: dict[str, float] = defaultdict(float)
    for l in prep_lines:
        placed[wc_by_id[l.work_center_id].code] += l.planned_hours
    for u in unplanned:
        if u.get("order_no") != order_no or u.get("reason") in _PASS_THROUGH_REASONS:
            continue
        wc = u.get("work_center_code") or ""
        left = placed.get(wc, 0.0)
        if left <= 1e-6:
            continue
        take = min(left, float(u.get("hours") or 0))
        u["hours"] = round(float(u.get("hours") or 0) - take, 8)
        placed[wc] = left - take
    unplanned[:] = [u for u in unplanned if float(u.get("hours") or 0) > 1e-6 or u.get("reason") in _PASS_THROUGH_REASONS]


def _try_place_candidate(
    db: Session,
    candidate: PlanningCandidate,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
    sched_ctx: SchedulingContext,
    produced_map: dict,
    *,
    material_policy: str = "conditional",
) -> tuple[bool, list[DraftLine], list[dict], dict[tuple[int, date], float]]:
    trial = dict(remaining)
    ls, un = _place_candidate(
        db, candidate, wc_by_id, weeks, trial, rules, sched_ctx, produced_map, material_policy=material_policy
    )
    return not un, ls, un, trial


def simulate(
    db: Session,
    req: AutoPlanRequest,
    extra_batches: list | None = None,
    *,
    production_as_of: date | None = None,
) -> Simulation:
    """Otomatik plani hesaplar, veritabanina yazmaz.

    due_date: adaylar (tekil + parti) effective_due sirasiyla yerlestirilir; sigmayan 'unplanned'.
    revenue : ciro oncelikli (sezgisel); kalan satis degeri / kalan saat azalan sira;
              yalnizca ufka TAMAMEN sigan adaylar once alinir, kalan kapasite termin sirasiyla doldurulur.
    """
    scope = plan_horizon_scope(req.start_week, req.weeks)
    start = scope.start
    weeks = [start + timedelta(weeks=i) for i in range(req.weeks)]
    wcs = _selected_work_centers(db, req.work_center_ids)
    extra_batches = extra_batches or []
    extra_order_ids = {link.order_id for b in extra_batches for link in b.orders}
    orders = [o for o in _open_orders_with_ops(db) if o.id not in extra_order_ids]
    batches = list(pbatches.open_batches_with_ops(db)) + list(extra_batches)
    all_orders = (
        db.query(Order)
        .options(selectinload(Order.item).selectinload(Item.operations), selectinload(Order.item).selectinload(Item.bom_lines))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
        .all()
    )
    if not wcs:
        return Simulation(req.mode, start, weeks, [], [], [], [], 0.0, all_orders, [], [])

    wc_ids = [w.id for w in wcs]
    wc_by_id = {w.id: w for w in wcs}
    sched_ctx = SchedulingContext(
        horizon_start=start,
        horizon_end_exclusive=scope.end_exclusive,
        replace_existing=req.replace_existing,
        production_as_of=production_as_of,
    )
    from app.services.remaining_work import produced_qty_map

    produced_map, _ = produced_qty_map(db, as_of=sched_ctx.production_as_of)

    # kalan kapasite = planlanabilir kapasite - mevcut plan (manuel + tahmin + otomatik)
    manual = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="manual")
    forecast = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="forecast")
    auto = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="auto")
    remaining: dict[tuple[int, date], float] = {}
    for w in wcs:
        for wk in weeks:
            plan_cap = cap.planning_capacity_hours(db, w, wk)
            used = manual.get((w.id, wk), 0.0) + forecast.get((w.id, wk), 0.0)
            if not req.replace_existing:
                used += auto.get((w.id, wk), 0.0)
            remaining[(w.id, wk)] = max(plan_cap - used, 0.0)
    from app.services import overtime_pool as otp

    pool = otp.build_pool(db, wcs, weeks)
    if req.replace_existing:
        for k, h in pool.proposed_base.items():  # eski plandan kalan 'onay bekliyor' fazla mesaisi taban kapasite değildir
            remaining[k] = max(remaining.get(k, 0.0) - h, 0.0)
    if not bool(getattr(req, "use_overtime", True)):
        pool.potential = {}
    slip_mode = str(getattr(req, "slip_mode", "chain") or "chain")
    use_ot = bool(getattr(req, "use_overtime", True))
    slip_notes: list[dict] = []
    prep_todo: list[tuple[PlanningCandidate, float]] = []  # (aday, yerleşmeyen adet oranı)
    capacity_total = sum(remaining.values())
    load_budgets(db, wcs, weeks, remaining, replace_auto=req.replace_existing)

    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    skipped: list[dict] = []
    rules = scen.RuleLookup(db)
    co_results: list[dict] = []
    co_exceptions: list[dict] = []
    co_handled: set[int] = set()

    cs_opts = req.co_shipment
    if cs_opts and cs_opts.enabled and cs_opts.selections and req.mode == "due_date":
        from app.services import co_shipment as co_ship

        co_out = co_ship.apply_co_shipment(db, cs_opts, weeks, start, wc_by_id, remaining, rules)
        lines.extend(co_out.lines)
        co_results = co_out.results
        co_exceptions = co_out.exceptions
        co_handled = co_out.handled_order_ids
        orders = [o for o in orders if o.id not in co_handled]

    candidates = build_planning_candidates(db, orders, batches, wc_by_id, sched_ctx, produced_map)
    mat_policy = req.material_policy or "conditional"
    mat_unverified_ids: set[int] = set()

    def _collect_lines(new_lines: list[DraftLine]) -> None:
        for ln in new_lines:
            if ln.material_unverified:
                mat_unverified_ids.add(ln.order_id)

    flow_mode = str(getattr(req, "placement", "flow") or "flow") == "flow"
    flow_notes: list[dict] = []

    def _flow_align(new_lines: list[DraftLine]) -> list[DraftLine]:
        """Akis modu: aday yerlesir yerlesmez onculleri ardilina yaslanir; boşalan erken kapasite sonraki
        adaylara acilir (toplam kapasite kullanimi artar, ara stok dusuk kalir, bitis erken kalir)."""
        if not flow_mode or not new_lines:
            return new_lines
        from app.services.jit_placement import apply_placement_pass as _pass

        aligned, notes_ = _pass(db, new_lines, weeks, remaining, mode="flow", buffer_days=0)
        flow_notes.extend(notes_)
        return aligned

    if req.mode == "revenue":
        ranked = sort_candidates(candidates, "revenue")
        leftover: list[PlanningCandidate] = []
        for c in ranked:
            fits, ls, _, trial = _try_place_candidate(
                db, c, wc_by_id, weeks, remaining, rules, sched_ctx, produced_map, material_policy=mat_policy
            )
            if fits:
                remaining = trial
                lines.extend(ls)
                _collect_lines(ls)
            else:
                leftover.append(c)
        for c in sort_candidates(leftover, "due_date"):
            ls, un, remaining, notes_ = _place_candidate_balanced(
                db, c, wc_by_id, weeks, remaining, pool, rules, sched_ctx, produced_map,
                material_policy=mat_policy, slip_mode=slip_mode, use_overtime=use_ot,
            )
            slip_notes.extend(notes_)
            _note_prep(prep_todo, c, notes_)
            ls = _flow_align(ls)
            lines.extend(ls)
            unplanned.extend(un)
            _collect_lines(ls)
            if not ls:
                skipped.append(candidate_skipped_record(c))
    else:
        for c in sort_candidates(candidates, "due_date"):
            ls, un, remaining, notes_ = _place_candidate_balanced(
                db, c, wc_by_id, weeks, remaining, pool, rules, sched_ctx, produced_map,
                material_policy=mat_policy, slip_mode=slip_mode, use_overtime=use_ot,
            )
            slip_notes.extend(notes_)
            _note_prep(prep_todo, c, notes_)
            ls = _flow_align(ls)
            lines.extend(ls)
            unplanned.extend(un)
            _collect_lines(ls)

    # Aşama 4: hazırlık dolgusu. Termin işleri yerleştikten sonra kalan NORMAL kapasiteye (fazla mesai yok),
    # termin sırasıyla, bitmiş ürüne dönüşemeyen adedin yarımamülleri (son operasyon hariç) 'prep' etiketiyle yazılır.
    if bool(getattr(req, "prep_fill", True)) and prep_todo:
        from dataclasses import replace as _replace_line

        for c, frac in prep_todo:
            ls, _un = _place_candidate(db, c, wc_by_id, weeks, remaining, rules, sched_ctx, produced_map,
                                       material_policy=mat_policy, qty_factor=frac, max_week_idx=None, prep=True)
            if not ls:
                continue
            ls = _flow_align([_replace_line(l, tag="prep") for l in ls])
            lines.extend(ls)
            _collect_lines(ls)
            _reduce_unplanned(unplanned, c.display_code, ls, wc_by_id)
            slip_notes.append({"kind": "prep", "label": c.display_code, "order_id": c.order.id if c.order else None,
                               "hours": round(sum(l.planned_hours for l in ls), 2), "qty": round(max((l.planned_qty for l in ls), default=0.0), 2),
                               "detail": f"{round(sum(l.planned_hours for l in ls), 1)} sa hazırlık: bitmiş ürüne dönüşemeyen %{frac * 100:.0f} için yarımamül atıl kapasitede üretildi (fazla mesai yok)"})

    # Son gecis: termine yakin (JIT) kaydirma ve/veya yarimamul ara stok siniri; satirlar yalnizca daha gece kayar.
    from app.services.jit_placement import apply_placement_pass

    lines, placement_notes = apply_placement_pass(
        db, lines, weeks, remaining,
        mode=str(getattr(req, "placement", "flow") or "flow"),
        buffer_days=int(getattr(req, "jit_buffer_days", 2) or 0),
        skip_order_ids=set(co_handled),
    )
    placement_notes = slip_notes + flow_notes + placement_notes
    return Simulation(
        req.mode,
        start,
        weeks,
        wcs,
        lines,
        unplanned,
        skipped,
        capacity_total,
        all_orders,
        co_results,
        co_exceptions,
        material_unverified=bool(mat_unverified_ids),
        material_unverified_order_ids=sorted(mat_unverified_ids),
        placement_notes=placement_notes,
        overtime_proposals=otp.proposals(pool),
    )


def draft_line_to_dict(line: DraftLine) -> dict:
    return {
        "order_id": line.order_id,
        "production_batch_id": line.production_batch_id,
        "operation_id": line.operation_id,
        "work_center_id": line.work_center_id,
        "machine_id": line.machine_id,
        "week_start": line.week_start.isoformat(),
        "planned_hours": round(float(line.planned_hours or 0), 6),
        "planned_qty": round(float(line.planned_qty or 0), 6),
        "mode": line.mode or "auto",
        "material_unverified": line.material_unverified,
        "semi_finished_code": line.semi_finished_code or "",
        "tag": line.tag or "",
    }


def apply_plan_snapshot(
    db: Session,
    req: AutoPlanRequest,
    plan_lines: list[dict],
    username: str,
    *,
    revision_id: int | None,
    replace_manual: bool,
    keep_line_mode: bool = False,
    message_tag: str = "revizyon snapshot",
    overtime_proposals: list[dict] | None = None,
) -> dict:
    """Onayda kayitli plan satirlarini aynen yazar (yeniden simulate etmez).

    Cagiran, plan_input_write_lock altinda olmalidir (revizyon onay / auto_plan).
    """
    scope = plan_horizon_scope(req.start_week, req.weeks)
    wc_ids = sorted({int(r["work_center_id"]) for r in plan_lines}) if plan_lines else list(req.work_center_ids or [])
    if not wc_ids:
        return {"created": 0, "message": "Plan satiri yok", "unplanned": [], "skipped": [], "mode": req.mode}
    modes = replace_scope_modes(replace_manual=replace_manual)
    delete_lines_in_replace_scope(db, wc_ids, scope, modes)
    db.flush()
    from app.services import overtime_pool as otp

    otp.apply_proposals(db, overtime_proposals or [], wc_ids, [scope.start + timedelta(weeks=i) for i in range(req.weeks)], clear_existing=True)
    for row in plan_lines:
        mode = row.get("mode") or "auto"
        if not keep_line_mode:
            mode = "auto"
        db.add(
            PlanLine(
                order_id=int(row["order_id"]),
                material_unverified=row.get("material_unverified"),
                production_batch_id=row.get("production_batch_id"),
                operation_id=int(row["operation_id"]),
                work_center_id=int(row["work_center_id"]),
                machine_id=row.get("machine_id"),
                week_start=date.fromisoformat(str(row["week_start"])[:10]),
                planned_hours=float(row["planned_hours"]),
                planned_qty=float(row.get("planned_qty") or 0),
                semi_finished_code=row.get("semi_finished_code") or "",
                mode=mode if mode in ("auto", "manual") else "auto",
                strategy=req.mode,
                tag=row.get("tag") or "",
                revision_id=revision_id,
                created_by=username,
            )
        )
    db.flush()
    label = REVENUE_MODE_LABEL if req.mode == "revenue" else "termine gore"
    return {
        "created": len(plan_lines),
        "unplanned": [],
        "skipped": [],
        "mode": req.mode,
        "message": f"{len(plan_lines)} plan satiri uygulandi ({label}, {message_tag}).",
    }


def write_simulation(
    db: Session,
    req: AutoPlanRequest,
    sim: Simulation,
    username: str,
    *,
    revision_id: int | None = None,
    commit: bool = True,
    replace_manual: bool = False,
    message_tag: str | None = None,
    keep_line_mode: bool = False,
) -> dict:
    """Simulasyon sonucunu plan satiri olarak yazar."""
    from app.services.plan_input_lock import plan_input_write_lock

    if not sim.work_centers:
        return {
            "created": 0,
            "unplanned": [],
            "skipped": [],
            "mode": req.mode,
            "message": "Planlanacak is merkezi secilmedi (is merkezinde 'Planlaniyor' isaretli olmali).",
            "co_shipment_results": [],
            "co_shipment_exceptions": [],
        }
    wc_ids = [w.id for w in sim.work_centers]
    hk = f"{sim.start.isoformat()}|{len(sim.weeks)}|{','.join(str(i) for i in sorted(wc_ids))}"
    with plan_input_write_lock(db, hk):
        if req.replace_existing:
            scope = plan_horizon_scope(sim.start, len(sim.weeks))
            modes = replace_scope_modes(replace_manual=replace_manual)
            delete_lines_in_replace_scope(db, wc_ids, scope, modes)
            db.flush()
        from app.services import overtime_pool as otp

        otp.apply_proposals(db, sim.overtime_proposals or [], wc_ids, sim.weeks, clear_existing=bool(req.replace_existing))
        for l in sim.lines:
            db.add(
                PlanLine(
                    order_id=l.order_id,
                    material_unverified=l.material_unverified,
                    production_batch_id=l.production_batch_id,
                    operation_id=l.operation_id,
                    work_center_id=l.work_center_id,
                    machine_id=l.machine_id,
                    week_start=l.week_start,
                    planned_hours=l.planned_hours,
                    planned_qty=l.planned_qty,
                    semi_finished_code=l.semi_finished_code or "",
                    mode=l.mode if keep_line_mode and l.mode in ("auto", "manual") else "auto",
                    strategy=req.mode,
                    tag=l.tag or "",
                    revision_id=revision_id,
                    created_by=username,
                )
            )
        if commit:
            db.commit()
        else:
            db.flush()
    label = REVENUE_MODE_LABEL if req.mode == "revenue" else "termine gore"
    tag = message_tag if message_tag is not None else ("revizyon" if revision_id else "revizyonsuz")
    msg = f"{len(sim.lines)} plan satiri olusturuldu ({label}, {tag})."
    if sim.co_shipment_results:
        msg += f" Birlikte sevk modu: {len(sim.co_shipment_results)} siparis grubu."
    out = {
        "created": len(sim.lines),
        "unplanned": sim.unplanned,
        "skipped": sim.skipped,
        "mode": req.mode,
        "message": msg,
        "co_shipment_results": sim.co_shipment_results or [],
        "co_shipment_exceptions": sim.co_shipment_exceptions or [],
        "material_unverified": bool(sim.material_unverified),
        "material_unverified_order_ids": sim.material_unverified_order_ids or [],
        "placement": getattr(req, "placement", "flow"),
        "placement_notes": sim.placement_notes or [],
        "overtime_proposals": sim.overtime_proposals or [],
        "slip_mode": getattr(req, "slip_mode", "chain"),
        "prep_hours": round(sum(l.planned_hours for l in sim.lines if (l.tag or "") == "prep"), 1),
    }
    if sim.material_unverified:
        out["message"] += " Malzeme dogrulanmadi (kosullu plan)."
    return out


def auto_plan(
    db: Session,
    req: AutoPlanRequest,
    username: str,
    *,
    revision_id: int | None = None,
    commit: bool = True,
    replace_manual: bool = False,
) -> dict:
    sim = simulate(db, req)
    try:
        # Weekly replacement and daily schedule are one transaction. A failed
        # daily calculation must not leave a newly committed weekly plan.
        out = write_simulation(db, req, sim, username, revision_id=revision_id, commit=False, replace_manual=replace_manual)
        if req.planning_granularity == "daily_detailed":
            from app.services.daily_scheduler import build_daily_schedule

            dr = build_daily_schedule(db, req, username=username, plan_lines=sim.lines)
            out["daily_schedule"] = {
                "version_id": dr.version_id,
                "segments_created": dr.segments_created,
                "skipped": dr.skipped,
                "remaining_qty": dr.remaining_qty,
            }
        if commit:
            db.commit()
    except Exception:
        if commit:
            db.rollback()
        raise
    return out


def add_manual_line(db: Session, line: ManualPlanLineIn, username: str) -> PlanLine:
    order = db.get(Order, line.order_id)
    op = db.get(RoutingOperation, line.operation_id)
    if not order or not op:
        raise ValueError("Siparis veya operasyon bulunamadi")
    total = op.hours_for(order.quantity)
    hours = line.planned_hours if line.planned_hours is not None else total
    qty = line.planned_qty if line.planned_qty is not None else (order.quantity * hours / total if total > 0 else 0)
    machine_id = None
    if is_line_operation(op):
        week = cap.week_start(line.week_start)
        hours = line_run_hours(op, qty)
        if hours <= 0:
            raise ValueError("Hat ilerleme süresi ve miktar pozitif olmalı")
        used = planned_hours_by_week(db, [op.work_center_id], week, week)
        remaining = {(op.work_center_id, week): max(0, cap.planning_capacity_hours(db, op.work_center, week) - used.get((op.work_center_id, week), 0))}
        load_budgets(db, [op.work_center], [week], remaining)
        if line.machine_id is not None:
            for m in op.work_center.machines:
                if m.id != line.machine_id:
                    remaining[("machine", m.id, week)] = 0
        machine_id, room = station_room(op, week, remaining)
        while machine_id is not None and room + 1e-6 < hours:
            remaining[("machine", machine_id, week)] = 0
            machine_id, room = station_room(op, week, remaining)
        if room + 1e-6 < hours or machine_id is None:
            raise ValueError("Uygun istasyonda yeterli haftalık hat saati yok; istasyon saatlerini ve ekiplerini kontrol edin")
    pl = PlanLine(
        machine_id=machine_id,
        material_unverified=(order.material_status or "unknown") == "unknown",
        order_id=order.id,
        operation_id=op.id,
        work_center_id=op.work_center_id,
        week_start=cap.week_start(line.week_start),
        planned_hours=round(hours, 10),
        planned_qty=round(qty, 2),
        mode="manual",
        created_by=username,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return pl


def plan_lines(db: Session, wc_ids: list[int] | None, start: date | None, end: date | None, mode: str | None = None) -> list[PlanLineOut]:
    q = db.query(PlanLine).options(
        joinedload(PlanLine.order).joinedload(Order.item),
        joinedload(PlanLine.production_batch).joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order),
        joinedload(PlanLine.operation),
        joinedload(PlanLine.work_center),
    )
    if wc_ids:
        q = q.filter(PlanLine.work_center_id.in_(wc_ids))
    if start:
        q = q.filter(PlanLine.week_start >= cap.week_start(start))
    if end:
        q = q.filter(PlanLine.week_start <= end)
    if mode:
        q = q.filter(PlanLine.mode == mode)
    out = []
    rows = q.order_by(PlanLine.week_start, PlanLine.work_center_id, PlanLine.id).all()
    codes = {pl.semi_finished_code or (pl.operation.semi_finished_code if pl.operation else "") for pl in rows}
    names = dict(db.query(Item.code, Item.name).filter(Item.code.in_(codes - {"", None})).all()) if codes else {}
    for pl in rows:
        if pl.order is None or pl.operation is None or pl.work_center is None:
            continue  # bagli kayit silinmis (yetim plan satiri)
        members = []
        batch_nos: list[str] = []
        batch_no = ""
        batch_id = pl.production_batch_id
        if pl.production_batch:
            members = [dict(order_id=link.order_id, order_no=link.order.order_no,
                            position_no=link.order.position_no or "", customer=link.order.customer or "",
                            quantity=link.quantity, due_date=effective_due(link.order))
                       for link in sorted(pl.production_batch.orders, key=lambda link: link.order_id) if link.order]
            batch_no = pl.production_batch.batch_no
            batch_nos = [
                f"{l.order.order_no}{f'/{l.order.position_no}' if l.order and l.order.position_no else ''}"
                for l in sorted(pl.production_batch.orders, key=lambda x: x.order_id)
                if l.order
            ]
        out.append(
            PlanLineOut(
                material_unverified=pl.material_unverified,
                material_note=recorded_material_note(pl.material_unverified),
                id=pl.id,
                machine_id=pl.machine_id,
                machine_code=next((m.code for m in pl.work_center.machines if m.id == pl.machine_id), ""),
                order_id=pl.order_id,
                order_no=batch_no if batch_id else pl.order.order_no,
                position_no="" if batch_id else (pl.order.position_no or ""),
                production_batch_id=batch_id,
                batch_no=batch_no,
                batch_order_nos=batch_nos,
                batch_members=members,
                customer=" / ".join(dict.fromkeys(m["customer"] for m in members)) if members else pl.order.customer,
                due_date=min(m["due_date"] for m in members) if members else effective_due(pl.order),
                item_code=pl.order.item.code,
                operation_id=pl.operation_id,
                operation_seq=pl.operation.seq,
                operation_name=pl.operation.operation_name or "",
                work_center_id=pl.work_center_id,
                work_center_code=pl.work_center.code,
                week_start=pl.week_start,
                planned_hours=pl.planned_hours,
                planned_qty=pl.planned_qty,
                semi_finished_code=pl.semi_finished_code or (pl.operation.semi_finished_code if pl.operation else ""),
                semi_finished_name=names.get(pl.semi_finished_code or pl.operation.semi_finished_code, ""),
                mode=pl.mode,
                strategy=pl.strategy or "",
                tag=pl.tag or "",
                revision_id=pl.revision_id,
            )
        )
    return out


def load(db: Session, wc_ids: list[int] | None, start: date, weeks: int) -> list[WorkCenterLoad]:
    start = cap.week_start(start)
    wcs = _selected_work_centers(db, wc_ids) if wc_ids else db.query(WorkCenter).filter(WorkCenter.is_active.is_(True)).order_by(WorkCenter.code).all()
    if not wcs:
        return []
    wk_list = [start + timedelta(weeks=i) for i in range(weeks)]
    planned = planned_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1])
    forecast = planned_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1], mode="forecast")
    forecast_pls = (
        db.query(PlanLine)
        .options(joinedload(PlanLine.order).joinedload(Order.item))
        .filter(
            PlanLine.work_center_id.in_([w.id for w in wcs]),
            PlanLine.week_start >= start,
            PlanLine.week_start <= wk_list[-1],
            PlanLine.mode == "forecast",
        )
        .all()
    )
    fc_detail: dict[tuple[int, date], list[ForecastLoadDetail]] = defaultdict(list)
    for pl in forecast_pls:
        if not pl.order:
            continue
        fc_detail[(pl.work_center_id, pl.week_start)].append(
            ForecastLoadDetail(
                order_no=pl.order.order_no,
                item_code=pl.order.item.code if pl.order.item else "",
                hours=round(pl.planned_hours, 2),
            )
        )
    actual = actual_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1])
    weekly_kpis = plan_and_output_kpis_for_range(db, [w.id for w in wcs], start, wk_list[-1])
    result = []
    for w in wcs:
        rows = []
        unit = w.capacity_unit_hours or 1.0
        calendar = cap.LaborCapacityCalendar(db, w, start, wk_list[-1] + timedelta(days=6))
        ovl = calendar.overrides
        for wk in wk_list:
            c_raw = calendar.capacity(wk, wk + timedelta(days=6)).capacity_hours
            c_plan = cap.apply_planning_reserve(w, c_raw)
            p = planned.get((w.id, wk), 0.0)
            fc = forecast.get((w.id, wk), 0.0)
            a = actual.get((w.id, wk), 0.0)
            kpis = weekly_kpis.get((w.id, wk), {
                "standard_hour_equivalent_output": 0.0,
                "plan_adherence_remaining_hours": 0.0,
                "plan_matched_output_hours": 0.0,
            })
            std_out = kpis["standard_hour_equivalent_output"]
            plan_rem = kpis["plan_adherence_remaining_hours"]
            idle = max(c_plan - p, 0.0)
            ot = sum(
                cap.overtime_daily_capacity_hours(w, wk + timedelta(days=i), ovl.get(wk + timedelta(days=i)))
                for i in range(7) if (wk + timedelta(days=i)) not in calendar.holidays
            )
            n_days = len(cap.working_days(w, wk, wk + timedelta(days=6), ovl))
            daily_cap = c_raw / n_days if n_days else 0.0
            rem_days = plan_rem / daily_cap if daily_cap > 0 else 0.0
            rows.append(
                WeekLoad(
                    week_start=wk,
                    capacity_hours=round(c_raw, 2),
                    planning_capacity_hours=round(c_plan, 2),
                    planned_hours=round(p, 2),
                    forecast_hours=round(fc, 2),
                    firm_planned_hours=round(max(p - fc, 0.0), 2),
                    forecast_details=fc_detail.get((w.id, wk), []),
                    utilization=round(p / c_plan, 3) if c_plan > 0 else 0.0,
                    actual_hours=round(std_out if std_out else a, 2),
                    standard_hour_equivalent_output=round(std_out, 2),
                    actual_utilization=round(std_out / c_raw, 3) if c_raw > 0 else 0.0,
                    remaining_hours=round(plan_rem, 2),
                    plan_adherence_remaining_hours=round(plan_rem, 2),
                    plan_matched_output_hours=round(kpis["plan_matched_output_hours"], 2),
                    remaining_days=round(rem_days, 2),
                    idle_hours=round(idle, 2),
                    overtime_hours=round(ot, 2),
                    capacity_units=round(c_raw / unit, 2),
                    planned_units=round(p / unit, 2),
                    actual_units=round(std_out / unit, 2),
                )
            )
        material_counts = dict(db.query(PlanLine.material_unverified, func.count(PlanLine.id)).filter(
            PlanLine.work_center_id == w.id, PlanLine.week_start >= start, PlanLine.week_start <= wk_list[-1]
        ).group_by(PlanLine.material_unverified).all())
        result.append(WorkCenterLoad(work_center_id=w.id, work_center_code=w.code, weeks=rows,
                                    machines=machine_loads(db, w, wk_list) if (w.planning_mode or "labor") == "line" else [],
                                    conditional_line_count=material_counts.get(True, 0),
                                    unknown_material_line_count=material_counts.get(None, 0)))
    return result


def machine_loads(db: Session, w: WorkCenter, wk_list: list[date]) -> list:
    """Hat merkezinde istasyon bazlı haftalık doluluk: kapasite = istasyon haftalık saati, plan = o istasyona yerleşen satırlar."""
    from app.models import MachineWeek
    from app.schemas import MachineLoad, MachineWeekLoad
    from app.services.routing_resource import station_crew

    machines = [m for m in w.machines if m.is_active]
    if not machines:
        return []
    mids = [m.id for m in machines]
    caps = {(mw.machine_id, mw.week_start): float(mw.working_hours or 0) for mw in db.query(MachineWeek).filter(MachineWeek.machine_id.in_(mids), MachineWeek.week_start >= wk_list[0], MachineWeek.week_start <= wk_list[-1]).all()}
    used = {(mid, wk): float(h or 0) for mid, wk, h in db.query(PlanLine.machine_id, PlanLine.week_start, func.sum(PlanLine.planned_hours)).filter(
        PlanLine.machine_id.in_(mids), PlanLine.week_start >= wk_list[0], PlanLine.week_start <= wk_list[-1], PlanLine.mode.in_(["auto", "manual"])).group_by(PlanLine.machine_id, PlanLine.week_start).all()}
    out = []
    for m in machines:
        rows = []
        for wk in wk_list:
            c = caps.get((m.id, wk), 0.0) if station_crew(m) else 0.0
            p = used.get((m.id, wk), 0.0)
            rows.append(MachineWeekLoad(week_start=wk, capacity_hours=round(c, 2), planned_hours=round(p, 2), idle_hours=round(max(c - p, 0.0), 2), utilization=round(p / c, 3) if c > 0 else 0.0))
        out.append(MachineLoad(machine_id=m.id, machine_code=m.code, machine_name=m.name or "", weeks=rows))
    return out


# ---------------- Terminleme (lead time) ----------------

_WEEK_FULL_EPS = 0.5  # haftalik plan ~%100 ise kalan saatle baslama (249.9/250 bug)
_HOURS_LEFT_EPS = 1e-6  # isin kalan suresi — kucuk isler (15 dk) tam yerlesmeli
LEADTIME_DEFAULT_HORIZON_DAYS = 730


@dataclass
class ScheduleOpResult:
    status: str  # scheduled | insufficient_capacity | horizon_exceeded
    scheduled_hours: float
    remaining_hours: float
    start: datetime | None
    end: datetime | None
    reason: str
    horizon_end: date


def _week_room_hours(db: Session, wc: WorkCenter, wk: date, planned_week: dict[date, float]) -> float:
    """Haftada kalan planlanabilir saat; dolu haftada 0."""
    cap_h = cap.planning_capacity_hours(db, wc, wk)
    used = planned_week.get(wk, 0.0)
    if used >= cap_h - _WEEK_FULL_EPS:
        return 0.0
    return cap_h - used


def _daily_free_hours(db: Session, wc: WorkCenter, day: date, emp: int, planned_week: dict[date, float], wdays_cache: dict[date, int], ovl: cap.Overrides) -> float:
    from app.services.calendar_capacity import daily_labor_capacity_hours
    total = daily_labor_capacity_hours(db, wc, day, emp, ovl)
    if total <= 0:
        return 0.0
    wk = cap.week_start(day)
    week_room = _week_room_hours(db, wc, wk, planned_week)
    if week_room <= _WEEK_FULL_EPS:
        return 0.0
    if wk not in wdays_cache:
        wdays_cache[wk] = len(cap.working_days(wc, wk, wk + timedelta(days=6), ovl)) or 1
    used = planned_week.get(wk, 0.0) / wdays_cache[wk]
    return max(min(total - used, week_room), 0.0)


def _schedule_line_op(op, hours, earliest, horizon_end, remaining, quantity=None):
    """Weekly estimate: dates mark capacity weeks, not a shift commitment."""
    from datetime import time
    left = hours
    qty_left = quantity
    used_hours = 0.0
    first = last = None
    week = cap.week_start(earliest.date())
    if hours <= _HOURS_LEFT_EPS:
        return ScheduleOpResult("insufficient_capacity", 0, hours, None, None,
                                "Hat ilerleme süresi eksik", horizon_end)
    while left > _HOURS_LEFT_EPS and week <= horizon_end:
        mid, room = station_room(op, week, remaining)
        if room <= _HOURS_LEFT_EPS:
            week += timedelta(weeks=1)
            continue
        take = min(left, room)
        if qty_left is not None:
            placed = min(qty_left, line_quantity_for_hours(op, room))
            take = line_run_hours(op, placed)
            qty_left -= placed
        used_hours += take
        first = first or max(earliest, datetime.combine(week, time(0)))
        last = datetime.combine(min(week + timedelta(days=6), horizon_end), time(23, 59))
        remaining[(op.work_center_id, week)] -= take
        remaining[("machine", mid, week)] -= take
        left = line_run_hours(op, qty_left) if qty_left is not None else left - take
    return ScheduleOpResult("scheduled" if left <= _HOURS_LEFT_EPS else "insufficient_capacity",
                            used_hours, max(left, 0), first, last,
                            "" if left <= _HOURS_LEFT_EPS else "Uygun hat istasyonlarında haftalık kapasite yetersiz", horizon_end)


def _schedule_op(
    db: Session,
    wc: WorkCenter,
    hours: float,
    earliest: datetime,
    planned_week: dict[date, float],
    horizon_end: date,
) -> ScheduleOpResult:
    """Bir operasyonu 'earliest' anindan itibaren ilk gercek bos kapasiteye yerlestirir (ufuk dahil)."""
    emp = cap.employee_count(db, wc)
    ovl = cap.Overrides(db, wc.id)
    wdays_cache: dict[date, int] = {}
    hours_left = hours
    cursor = earliest
    day = cursor.date()
    step_start: datetime | None = None
    step_end: datetime | None = None
    scheduled = 0.0
    calendar = cap.LaborCapacityCalendar(db, wc, day, horizon_end)
    if hours > _HOURS_LEFT_EPS and not calendar.capacity(day, horizon_end).days:
        return ScheduleOpResult(
            status="insufficient_capacity", scheduled_hours=0, remaining_hours=hours,
            start=None, end=None, reason=f"İş merkezi '{wc.code}': seçili ufukta iş gücü kapasitesi yok.",
            horizon_end=horizon_end,
        )

    def _fail(status: str, reason: str) -> ScheduleOpResult:
        return ScheduleOpResult(
            status=status,
            scheduled_hours=round(scheduled, 6),
            remaining_hours=round(max(hours_left, 0.0), 6),
            start=step_start,
            end=step_end if step_end and step_start else step_start,
            reason=reason,
            horizon_end=horizon_end,
        )

    while hours_left > _HOURS_LEFT_EPS:
        if day > horizon_end:
            return _fail(
                "horizon_exceeded",
                f"Is merkezi '{wc.code}': {round(hours_left, 2)} saat icin plan ufku ({horizon_end.isoformat()}) yetersiz.",
            )
        wk = cap.week_start(day)
        cap_h = cap.planning_capacity_hours(db, wc, wk)
        if cap_h <= _WEEK_FULL_EPS:
            day = wk + timedelta(days=7)
            cursor = datetime.combine(day, cap.first_shift_start(wc, day, ovl.get(day)))
            continue
        if _week_room_hours(db, wc, wk, planned_week) <= _WEEK_FULL_EPS:
            next_day = wk + timedelta(days=7)
            if next_day > horizon_end:
                return _fail(
                    "horizon_exceeded",
                    f"Is merkezi '{wc.code}': dolu haftalar nedeniyle ufuk ({horizon_end.isoformat()}) icinde bos kapasite yok.",
                )
            day = next_day
            cursor = datetime.combine(day, cap.first_shift_start(wc, day, ovl.get(day)))
            continue
        ov = ovl.get(day)
        free = _daily_free_hours(db, wc, day, emp, planned_week, wdays_cache, ovl)
        if free > _HOURS_LEFT_EPS:
            day_start = datetime.combine(day, cap.first_shift_start(wc, day, ov))
            hc = max(cap.daily_headcount(wc, day, emp, ov), 1)
            nominal_per_day = cap.daily_nominal_hours(wc, day, emp, ov) / hc
            if day == cursor.date() and cursor > day_start:
                frac_used = (cursor - day_start).total_seconds() / 3600.0
                free = max(free * (1 - min(frac_used / max(nominal_per_day, 1.0), 1.0)), 0.0)
                day_start = cursor
            week_room = _week_room_hours(db, wc, wk, planned_week)
            take = min(free, hours_left, week_room)
            if take > _HOURS_LEFT_EPS:
                daily_eff = cap.daily_capacity_hours(wc, day, emp, ov)
                elapsed_nominal = (take / daily_eff) * nominal_per_day if daily_eff > 0 else take
                if step_start is None:
                    step_start = day_start
                step_end = day_start + timedelta(hours=elapsed_nominal)
                hours_left -= take
                scheduled += take
                planned_week[wk] = planned_week.get(wk, 0.0) + take
        if hours_left > _HOURS_LEFT_EPS:
            day += timedelta(days=1)
            if day > horizon_end:
                return _fail(
                    "horizon_exceeded",
                    f"Is merkezi '{wc.code}': {round(hours_left, 2)} saat icin plan ufku ({horizon_end.isoformat()}) yetersiz.",
                )
            cursor = datetime.combine(day, cap.first_shift_start(wc, day, ovl.get(day)))

    if step_start is None:
        step_start = cursor
        step_end = cursor
    return ScheduleOpResult(
        status="scheduled",
        scheduled_hours=round(scheduled, 6),
        remaining_hours=0.0,
        start=step_start,
        end=max(step_end or step_start, step_start),
        reason="",
        horizon_end=horizon_end,
    )


def _dt_str(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else None


def lead_time(db: Session, req: LeadTimeRequest) -> LeadTimeOut:
    from app.services.orders import validate_material_fields
    from app.services.material_schedule import material_gate_for_order
    validate_material_fields(req.material_status, req.material_ready_date)
    material_gate = material_gate_for_order(req, policy=req.material_policy)
    item = (
        db.query(Item)
        .options(joinedload(Item.operations).joinedload(RoutingOperation.work_center))
        .filter(Item.code == req.item_code)
        .first()
    )
    if not item:
        raise ValueError("Stok kodu bulunamadi")
    ops = [
        op
        for op in item.operations
        if (not req.work_center_ids or op.work_center_id in req.work_center_ids)
        and (not req.operation_seqs or op.seq in req.operation_seqs)
    ]
    if not ops:
        raise ValueError("Secime uyan operasyon yok")

    material_fields = dict(material_status=req.material_status, material_ready_date=req.material_ready_date,
                           material_policy=req.material_policy, material_unverified=material_gate.material_unverified,
                           material_note="Malzeme doğrulanmadı; termin malzemenin başlangıçta hazır olması koşuluyla geçerlidir." if material_gate.material_unverified else "")
    if material_gate.blocks_planning:
        return LeadTimeOut(item_code=item.code, quantity=req.quantity,
                           total_hours=round(sum(op.hours_for(req.quantity) for op in ops), 2),
                           steps=[], status="infeasible", failure_reason="Malzeme durumu bilinmiyor; katı politikada termin hesaplanamaz.", **material_fields)
    material_start = max(req.start, req.material_ready_date) if req.material_status == "expected" and req.material_ready_date else req.start
    if req.material_status == "expected":
        material_fields['material_note'] = f"Malzeme bekleniyor; {req.material_ready_date.isoformat()} tarihinden önce üretim başlamaz."

    horizon_days = max(int(req.horizon_days or LEADTIME_DEFAULT_HORIZON_DAYS), 7)
    horizon_end = req.start + timedelta(days=horizon_days)
    wc_ids = list({op.work_center_id for op in ops})
    planned_all = planned_hours_by_week(db, wc_ids, cap.week_start(req.start), horizon_end)
    planned_by_wc: dict[int, dict[date, float]] = defaultdict(dict)
    for (wc_id, wk), h in planned_all.items():
        planned_by_wc[wc_id][wk] = h

    line_wcs = {op.work_center_id: op.work_center for op in ops if is_line_operation(op)}
    line_weeks = []
    week = cap.week_start(req.start)
    while week <= horizon_end:
        line_weeks.append(week)
        week += timedelta(weeks=1)
    line_remaining = {(wc.id, week): max(0, cap.planning_capacity_hours(db, wc, week) - planned_all.get((wc.id, week), 0))
                      for wc in line_wcs.values() for week in line_weeks}
    load_budgets(db, list(line_wcs.values()), line_weeks, line_remaining)
    rules = scen.RuleLookup(db)
    steps: list[LeadTimeStep] = []
    first_wc = ops[0].work_center
    cursor = datetime.combine(material_start, cap.first_shift_start(first_wc, material_start, cap.Overrides(db, first_wc.id).get(material_start)))
    overall_start: datetime | None = None
    overall_end: datetime | None = None
    prev_op: RoutingOperation | None = None
    prev_start: datetime | None = None
    prev_end: datetime | None = None
    outcome_status = "complete"
    failure_reason = ""
    total_remaining = 0.0

    for op in ops:
        wc = op.work_center
        hours = op.hours_for(req.quantity)
        rule_desc = ""
        if prev_op is not None and prev_start is not None and prev_end is not None:
            rule = rules.get(item, prev_op, op)
            earliest = opcon.leadtime_earliest_datetime(rule, req.quantity, prev_start, prev_end, req.quantity)
            rule_desc = rule.describe() + ("" if rule.source == "default" else f" ({'stok' if rule.source == 'item' else 'grup'} kuralı)")
        else:
            earliest = cursor
        if is_line_operation(op):
            sched = _schedule_line_op(op, hours, earliest, horizon_end, line_remaining, req.quantity)
            rule_desc += " · Haftalık istasyon kapasitesi; gün/saatler yaklaşık"
        else:
            sched = _schedule_op(db, wc, hours, earliest, planned_by_wc[wc.id], horizon_end)
        step_start = sched.start
        step_end = sched.end
        if sched.status == "scheduled" and prev_end is not None and step_end and step_end < prev_end:
            from app.services.routing_resource import planning_unit_hours
            step_end = prev_end + timedelta(hours=planning_unit_hours(op))
        step = LeadTimeStep(
            operation_seq=op.seq,
            operation_name=op.operation_name,
            work_center_code=wc.code,
            hours=round(hours, 2),
            start=_dt_str(step_start),
            end=_dt_str(step_end) if sched.status == "scheduled" and step_end else None,
            start_rule=rule_desc,
            scheduled_hours=round(sched.scheduled_hours, 4),
            remaining_hours=round(sched.remaining_hours, 4),
            status=sched.status,
            reason=sched.reason,
        )
        steps.append(step)

        if sched.status != "scheduled":
            total_remaining += sched.remaining_hours
            failure_reason = sched.reason or sched.status
            if not steps[:-1]:
                outcome_status = "infeasible"
            else:
                outcome_status = "partial"
            break

        overall_start = overall_start or step_start
        if step_end and (overall_end is None or step_end > overall_end):
            overall_end = step_end
        prev_op, prev_start, prev_end = op, step_start, step_end

    if outcome_status == "complete":
        total_remaining = 0.0

    return LeadTimeOut(
        **material_fields,
        item_code=item.code,
        quantity=req.quantity,
        total_hours=round(sum(s.hours for s in steps), 2),
        start=_dt_str(overall_start),
        end=_dt_str(overall_end) if outcome_status == "complete" else None,
        steps=steps,
        status=outcome_status,
        remaining_hours=round(total_remaining, 4),
        failure_reason=failure_reason,
    )


def load_detail(db: Session, work_center_id: int, week_start: date, *, firm_only: bool = False) -> LoadDetailOut:
    from app.services.gantt import _line_window_in_week

    wc = db.get(WorkCenter, work_center_id)
    if not wc:
        raise ValueError("Is merkezi bulunamadi")
    wk = cap.week_start(week_start)
    pls = (
        db.query(PlanLine)
        .options(
            joinedload(PlanLine.order).joinedload(Order.item),
            joinedload(PlanLine.production_batch).joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order),
            joinedload(PlanLine.operation),
        )
        .filter(PlanLine.work_center_id == work_center_id, PlanLine.week_start == wk)
    )
    if firm_only:
        pls = pls.filter(PlanLine.mode != "forecast")
    pls = pls.all()
    rows: list[LoadDetailRow] = []
    window_cache: dict = {}  # hafta takvimi/kapasitesi bir kez hesaplanir (satir basina sorgu yok)
    for pl in sorted(pls, key=plan_line_priority_key):
        if pl.order is None or pl.operation is None:
            continue
        ps, pe = _line_window_in_week(db, wc, wk, pl.id, pls, cache=window_cache)
        op = pl.operation
        item = pl.order.item
        batch_no = ""
        batch_nos: list[str] = []
        order_no = pl.order.order_no
        position_no = pl.order.position_no or ""
        customer = pl.order.customer
        qty = pl.planned_qty if pl.planned_qty > 0 else pl.order.quantity
        if pl.production_batch:
            batch_no = pl.production_batch.batch_no
            order_no = batch_no
            position_no = ""
            batch_nos = [
                f"{l.order.order_no}{f'/{l.order.position_no}' if l.order and l.order.position_no else ''}"
                for l in sorted(pl.production_batch.orders, key=lambda x: x.order_id)
                if l.order
            ]
            customers = sorted({l.order.customer for l in pl.production_batch.orders if l.order and l.order.customer})
            customer = ", ".join(customers) if customers else "parti"
            qty = pl.planned_qty if pl.planned_qty > 0 else pl.production_batch.quantity
        rows.append(
            LoadDetailRow(
                plan_line_id=pl.id,
                item_code=item.code if item else "",
                item_name=item.name if item else "",
                semi_finished_code=(op.semi_finished_code or "").strip(),
                operation_name=op.operation_name or "",
                order_no=order_no,
                position_no=position_no,
                customer=customer,
                batch_no=batch_no,
                batch_order_nos=batch_nos,
                operation_seq=op.seq,
                planned_hours=round(pl.planned_hours, 3),
                planned_qty=round(qty, 2),
                planned_start=ps,
                planned_end=pe,
                mode=pl.mode,
            )
        )
    total_hours = round(sum(r.planned_hours for r in rows), 2)
    total_qty = round(sum(r.planned_qty for r in rows), 2)
    by_item: dict[str, float] = defaultdict(float)
    for r in rows:
        by_item[r.item_code] += r.planned_hours
    pareto: list[LoadDetailParetoRow] = []
    cum = 0.0
    for code, h in sorted(by_item.items(), key=lambda x: -x[1]):
        cum += h
        pareto.append(
            LoadDetailParetoRow(
                item_code=code,
                hours=round(h, 2),
                pct=round(h / total_hours * 100, 1) if total_hours > 0 else 0.0,
                cum_pct=round(cum / total_hours * 100, 1) if total_hours > 0 else 0.0,
            )
        )
    return LoadDetailOut(
        work_center_id=wc.id,
        work_center_code=wc.code,
        week_start=wk,
        total_hours=total_hours,
        total_qty=total_qty,
        rows=rows,
        pareto=pareto,
    )


def weekly_output(db: Session, week_start: date, work_center_ids: list[int] | None = None) -> WeeklyOutputOut:
    """Haftalık üretim planı — iş merkezi bazlı, tahmin hariç."""
    wk = cap.week_start(week_start)
    week_end = wk + timedelta(days=4)
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True), WorkCenter.is_planned.is_(True))
    if work_center_ids:
        q = q.filter(WorkCenter.id.in_(work_center_ids))
    wcs = q.order_by(WorkCenter.code).all()
    sections: list[WeeklyOutputWc] = []
    total_jobs = 0
    total_qty = 0.0
    for wc in wcs:
        detail = load_detail(db, wc.id, wk, firm_only=True)
        if not detail.rows:
            continue
        rows = sorted(detail.rows, key=lambda r: (r.planned_start or "", r.order_no, r.operation_seq))
        sections.append(
            WeeklyOutputWc(
                work_center_id=wc.id,
                work_center_code=wc.code,
                total_qty=detail.total_qty,
                rows=rows,
            )
        )
        total_jobs += len(rows)
        total_qty += detail.total_qty
    return WeeklyOutputOut(
        week_start=wk,
        week_end=week_end,
        work_centers=sections,
        total_jobs=total_jobs,
        total_qty=round(total_qty, 2),
    )


def add_forecast_from_leadtime(db: Session, req: ForecastFromLeadTimeIn, username: str) -> tuple[int, int]:
    """Terminleme sonucunu tahmin plan satirlari olarak kaydeder; kapasite asimi engellenir. (order_id, satir_sayisi)"""
    from app.services.orders import validate_material_fields
    from app.services.material_schedule import material_gate_for_order
    validate_material_fields(req.material_status, req.material_ready_date)
    gate = material_gate_for_order(req, policy=req.material_policy)
    if gate.blocks_planning:
        raise ValueError("Malzeme durumu bilinmiyor; katı politikada tahmin kaydedilemez.")
    item = (
        db.query(Item)
        .options(joinedload(Item.operations).joinedload(RoutingOperation.work_center))
        .filter(Item.code == req.item_code)
        .first()
    )
    if not item:
        raise ValueError("Stok kodu bulunamadi")
    if not req.steps:
        raise ValueError("Operasyon adimi yok")
    if req.status != "complete":
        raise ValueError("Yalnizca basarili (complete) termin hesabi plana tahmin olarak eklenebilir.")
    for s in req.steps:
        if s.status != "scheduled" or s.remaining_hours > _HOURS_LEFT_EPS:
            raise ValueError(f"Operasyon {s.operation_seq} tam yerlesmedi; tahmin kaydedilemez.")
        if not s.start or not s.end:
            raise ValueError(f"Operasyon {s.operation_seq} baslangic/bitis eksik; tahmin kaydedilemez.")

    step_dates = [datetime.strptime(s.start[:10], "%Y-%m-%d").date() for s in req.steps]
    if req.material_status == "expected" and req.material_ready_date and min(step_dates) < req.material_ready_date:
        raise ValueError("Tahmin malzemenin hazır olacağı tarihten önce başlayamaz.")
    end_dt = datetime.strptime(req.steps[-1].end[:10], "%Y-%m-%d").date()
    horizon_start = cap.week_start(min(step_dates))
    horizon_end = cap.week_start(end_dt) + timedelta(weeks=16)
    weeks: list[date] = []
    wk = horizon_start
    while wk <= horizon_end:
        weeks.append(wk)
        wk += timedelta(weeks=1)

    wc_ids = list({op.work_center_id for op in item.operations})
    wcs = {w.id: w for w in db.query(WorkCenter).filter(WorkCenter.id.in_(wc_ids)).all()}
    existing = planned_hours_by_week(db, wc_ids, horizon_start, horizon_end)
    remaining: dict[tuple[int, date], float] = {}
    for wc_id in wc_ids:
        wc = wcs.get(wc_id)
        if not wc:
            continue
        for week in weeks:
            used = existing.get((wc_id, week), 0.0)
            remaining[(wc_id, week)] = _week_room_hours(db, wc, week, {week: used})

    load_budgets(db, list(wcs.values()), weeks, remaining)

    label = (req.label or "").strip() or f"TAH-{item.code}-{datetime.now():%m%d%H%M}"
    order = Order(
        order_no=label,
        customer="Tahmin",
        due_date=end_dt,
        item_id=item.id,
        quantity=req.quantity,
        status="forecast",
        note="Yeni iş terminleme tahmini" + (" — Malzeme doğrulanmadı; koşullu tahmin." if gate.material_unverified else ""),
        material_status=req.material_status,
        material_ready_date=req.material_ready_date,
    )
    db.add(order)
    db.flush()

    created = 0
    unplaced: list[str] = []
    for step in req.steps:
        op = next((o for o in item.operations if o.seq == step.operation_seq), None)
        if not op:
            continue
        start_day = datetime.strptime(step.start[:10], "%Y-%m-%d").date()
        start_wk = cap.week_start(start_day)
        idx = weeks.index(start_wk) if start_wk in weeks else 0
        hours_left = op.hours_for(req.quantity) if is_line_operation(op) else float(step.hours)
        total_hours = hours_left
        qty_left = req.quantity
        while hours_left > 1e-6 and idx < len(weeks):
            week = weeks[idx]
            avail = remaining.get((op.work_center_id, week), 0.0)
            machine_id = None
            if is_line_operation(op):
                machine_id, avail = station_room(op, week, remaining)
            if avail > _WEEK_FULL_EPS:
                take = min(avail, hours_left)
                qty = req.quantity * (take / total_hours) if total_hours > 0 else 0.0
                if is_line_operation(op):
                    qty = min(qty_left, line_quantity_for_hours(op, avail))
                    take = line_run_hours(op, qty)
                    qty_left -= qty
                db.add(
                    PlanLine(
                        order_id=order.id,
                        material_unverified=gate.material_unverified,
                        operation_id=op.id,
                        work_center_id=op.work_center_id,
                        week_start=week,
                        machine_id=machine_id,
                        planned_hours=round(take, 10),
                        planned_qty=round(qty, 2),
                        mode="forecast",
                        created_by=username,
                    )
                )
                remaining[(op.work_center_id, week)] -= take
                if machine_id is not None:
                    remaining[("machine", machine_id, week)] -= take
                hours_left = line_run_hours(op, qty_left) if is_line_operation(op) else hours_left - take
                created += 1
            if hours_left > 1e-6 and (not is_line_operation(op) or station_room(op, week, remaining)[1] <= 1e-6):
                idx += 1
        if hours_left > _WEEK_FULL_EPS:
            wc = wcs.get(op.work_center_id)
            code = wc.code if wc else "?"
            unplaced.append(f"{code} ({round(hours_left, 1)} sa)")

    if unplaced:
        db.rollback()
        raise ValueError("Kapasite yetersiz — plana eklenemedi: " + ", ".join(unplaced))

    db.commit()
    return order.id, created


def clear_forecast_plans(db: Session, work_center_ids: list[int] | None = None) -> int:
    q = db.query(PlanLine).filter(PlanLine.mode == "forecast")
    if work_center_ids:
        q = q.filter(PlanLine.work_center_id.in_(work_center_ids))
    forecast_order_ids = {pl.order_id for pl in q.all()}
    n = q.delete(synchronize_session=False)
    if forecast_order_ids:
        db.query(Order).filter(Order.id.in_(forecast_order_ids), Order.status == "forecast").delete(synchronize_session=False)
    db.commit()
    return n


def list_forecasts(db: Session) -> list[ForecastSummaryOut]:
    """Plana eklenmis terminleme tahminleri (forecast siparis + plan satirlari)."""
    orders = (
        db.query(Order)
        .options(joinedload(Order.item))
        .filter(Order.status == "forecast")
        .order_by(Order.created_at.desc(), Order.id.desc())
        .all()
    )
    out: list[ForecastSummaryOut] = []
    for o in orders:
        pls = db.query(PlanLine).filter(PlanLine.order_id == o.id, PlanLine.mode == "forecast").order_by(PlanLine.week_start).all()
        if not pls:
            continue
        weeks = [p.week_start for p in pls]
        out.append(
            ForecastSummaryOut(
                material_status=o.material_status or "unknown",
                material_ready_date=o.material_ready_date,
                material_note=o.note or "",
                order_id=o.id,
                order_no=o.order_no,
                item_code=o.item.code if o.item else "",
                item_name=o.item.name if o.item else "",
                quantity=o.quantity,
                due_date=o.due_date,
                total_hours=round(sum(p.planned_hours for p in pls), 2),
                line_count=len(pls),
                week_from=weeks[0],
                week_to=weeks[-1],
                created_at=o.created_at,
            )
        )
    return out


def delete_forecast(db: Session, order_id: int) -> int:
    order = db.get(Order, order_id)
    if not order or order.status != "forecast":
        raise ValueError("Tahmin siparisi bulunamadi")
    n = db.query(PlanLine).filter(PlanLine.order_id == order_id, PlanLine.mode == "forecast").delete(synchronize_session=False)
    db.delete(order)
    db.commit()
    return n
