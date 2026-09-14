"""Otomatik / manuel planlama, haftalik yuk ve terminleme (lead time)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionBatch, ProductionBatchOrder, RoutingOperation, WorkCenter
from app.services import production_batches as pbatches
from app.services.orders import effective_due, plan_line_priority_key
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
        .options(joinedload(Order.item).joinedload(Item.operations), joinedload(Order.item).joinedload(Item.bom_lines))
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
        setup = setup_by_op.get(op.id, True) if setup_by_op is not None else True
        setup_left = setup
        hours_per_unit = op.cycle_time_sec / 3600.0
        pred_required = qty_map.get(prev_op.id, 0.0) if prev_op else 0.0
        pred_week_map = pred_cum_by_week.get(prev_op.id, {}) if prev_op else {}

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
                    hours=round(operation_run_hours(op, op_qty - cum_planned.get(op.id, 0.0), setup_required=setup_left and cum_planned.get(op.id, 0) <= 1e-6), 2),
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
            pred_avail = opcon.predecessor_available(prev_op.id, completed_by_op, cum_planned) if prev_op else op_qty
            qty_cap = opcon.max_successor_qty(
                rule,
                pred_required,
                pred_avail,
                op_qty,
                cum_planned.get(op.id, 0.0),
            )
            if assembly_outputs is not None and assembly_wip_req:
                qty_cap = min(
                    qty_cap,
                    opcon.assembly_cap_qty(assembly_outputs, assembly_wip_req, op_qty) - cum_planned.get(op.id, 0.0),
                )
            if qty_cap <= 1e-6:
                if idx >= week_limit:
                    reason: opcon.BlockReason = "oncul_eksik" if prev_op and pred_avail + 1e-6 < pred_required else "kapasite_yetersiz"
                    if wait_reason:
                        reason = wait_reason
                    unplanned.append(
                        opcon.unplanned_entry(
                            order_no=label or anchor.order_no,
                            item_code=route_item.code,
                            semi_finished_code=semi_finished_code,
                            operation_seq=op.seq,
                            work_center_code=wc_by_id[op.work_center_id].code,
                            hours=round(qty_left * hours_per_unit + (op.setup_time_min / 60.0 if setup_left else 0), 2),
                            reason=reason,
                        )
                    )
                    break
                idx += 1
                continue

            week_qty_budget = min(qty_left, qty_cap)
            run_hours = week_qty_budget * hours_per_unit
            if setup_left and cum_planned.get(op.id, 0.0) <= 1e-6:
                run_hours += op.setup_time_min / 60.0

            wk = weeks[idx]
            avail = remaining[(op.work_center_id, wk)]
            if avail <= 1e-6:
                if idx >= week_limit:
                    unplanned.append(
                        opcon.unplanned_entry(
                            order_no=label or anchor.order_no,
                            item_code=route_item.code,
                            semi_finished_code=semi_finished_code,
                            operation_seq=op.seq,
                            work_center_code=wc_by_id[op.work_center_id].code,
                            hours=round(run_hours, 2),
                            reason="kapasite_yetersiz",
                        )
                    )
                    break
                idx += 1
                continue

            if run_hours > avail + 1e-6:
                if setup_left and cum_planned.get(op.id, 0.0) <= 1e-6:
                    setup_h = op.setup_time_min / 60.0
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
                    planned_hours=round(take, 3),
                    planned_qty=round(placed_qty, 2),
                    production_batch_id=production_batch_id,
                    label=label or anchor.order_no,
                    semi_finished_code=semi_finished_code or op.semi_finished_code or "",
                )
            )
            remaining[(op.work_center_id, wk)] = avail - take
            cum_planned[op.id] = cum_planned.get(op.id, 0.0) + placed_qty
            pred_cum_by_week.setdefault(op.id, {})[idx] = pred_cum_by_week.get(op.id, {}).get(idx, 0.0) + placed_qty
            qty_left -= placed_qty
            if first_idx is None:
                first_idx = idx
            op_last_idx = idx
            last_idx = max(last_idx, idx)
            if qty_left > 1e-6:
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
                    hours=round(qty_left * hours_per_unit, 2),
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
            qty[op.id] = q
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
                job.quantity,
                lbl,
                None,
                wc_by_id,
                weeks,
                remaining,
                rules,
                item=job.item,
                semi_finished_code=job.semi_finished_code,
                min_start_idx=0,
                ops=rem_ops,
                qty_by_op=qty_by_op,
                setup_by_op=setup_by_op,
                completed_by_op=completed,
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
                        wip_req.append((job.semi_finished_code, job.quantity))
                    ls, un, _ = _place_quantity(
                        o,
                        jobs.finish_job.quantity,
                        o.order_no,
                        None,
                        wc_by_id,
                        weeks,
                        remaining,
                        rules,
                        item=jobs.finish_job.item,
                        semi_finished_code="",
                        min_start_idx=wip_end,
                        ops=rem_ops,
                        qty_by_op=qty_by_op,
                        setup_by_op=setup_by_op,
                        completed_by_op=completed,
                        assembly_outputs=assembly_outputs,
                        assembly_wip_req=wip_req,
                    )
                    all_lines.extend(ls)
                    all_unplanned.extend(un)
        return all_lines, all_unplanned
    rem_ops = [op for op in sorted(o.item.operations or [], key=lambda x: x.seq) if op.work_center_id in wc_by_id]
    if not rem_ops:
        return [], []
    qty_by_op, setup_by_op = _maps_for_ops(rem_ops)
    if sum(qty_by_op.values()) <= 1e-6:
        return [], []
    ls, un, _ = _place_quantity(
        o,
        o.quantity,
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
                min_start_idx=0,
                ops=rem_ops,
                qty_by_op=qty_by_op,
                setup_by_op=setup_by_op,
                completed_by_op=completed,
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
                        min_start_idx=wip_end,
                        ops=rem_ops,
                        qty_by_op=qty_by_op,
                        setup_by_op=setup_by_op,
                        completed_by_op=completed,
                        assembly_outputs=assembly_outputs,
                        assembly_wip_req=wip_req,
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
    )


def _batch_hours(batch: ProductionBatch, wc_by_id: dict[int, WorkCenter]) -> float:
    if not batch.orders:
        return 0.0
    item = batch.item
    return sum(op.hours_for(batch.quantity) for op in item.operations if op.work_center_id in wc_by_id)


def _place_candidate(
    db: Session,
    candidate: PlanningCandidate,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
    sched_ctx: SchedulingContext,
    produced_map: dict,
) -> tuple[list[DraftLine], list[dict]]:
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
        )
    if candidate.batch and candidate.anchor_order:
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
        )
        return ls, un
    return [], []


def _try_place_candidate(
    db: Session,
    candidate: PlanningCandidate,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
    sched_ctx: SchedulingContext,
    produced_map: dict,
) -> tuple[bool, list[DraftLine], list[dict], dict[tuple[int, date], float]]:
    trial = dict(remaining)
    ls, un = _place_candidate(db, candidate, wc_by_id, weeks, trial, rules, sched_ctx, produced_map)
    return not un, ls, un, trial


def simulate(db: Session, req: AutoPlanRequest, extra_batches: list | None = None) -> Simulation:
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
        .options(joinedload(Order.item).joinedload(Item.operations), joinedload(Order.item).joinedload(Item.bom_lines))
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
    )
    from app.services.remaining_work import produced_qty_map

    produced_map, _ = produced_qty_map(db)

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
    capacity_total = sum(remaining.values())

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

    if req.mode == "revenue":
        ranked = sort_candidates(candidates, "revenue")
        leftover: list[PlanningCandidate] = []
        for c in ranked:
            fits, ls, _, trial = _try_place_candidate(
                db, c, wc_by_id, weeks, remaining, rules, sched_ctx, produced_map
            )
            if fits:
                remaining = trial
                lines.extend(ls)
            else:
                leftover.append(c)
        for c in sort_candidates(leftover, "due_date"):
            ls, un = _place_candidate(db, c, wc_by_id, weeks, remaining, rules, sched_ctx, produced_map)
            lines.extend(ls)
            unplanned.extend(un)
            if not ls:
                skipped.append(candidate_skipped_record(c))
    else:
        for c in sort_candidates(candidates, "due_date"):
            ls, un = _place_candidate(db, c, wc_by_id, weeks, remaining, rules, sched_ctx, produced_map)
            lines.extend(ls)
            unplanned.extend(un)

    return Simulation(
        req.mode, start, weeks, wcs, lines, unplanned, skipped, capacity_total, all_orders, co_results, co_exceptions
    )


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
    if req.replace_existing:
        scope = plan_horizon_scope(sim.start, len(sim.weeks))
        modes = replace_scope_modes(replace_manual=replace_manual)
        delete_lines_in_replace_scope(db, wc_ids, scope, modes)
        db.flush()
    for l in sim.lines:
        db.add(
            PlanLine(
                order_id=l.order_id,
                production_batch_id=l.production_batch_id,
                operation_id=l.operation_id,
                work_center_id=l.work_center_id,
                week_start=l.week_start,
                planned_hours=l.planned_hours,
                planned_qty=l.planned_qty,
                semi_finished_code=l.semi_finished_code or "",
                mode=l.mode if keep_line_mode and l.mode in ("auto", "manual") else "auto",
                strategy=req.mode,
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
    return {
        "created": len(sim.lines),
        "unplanned": sim.unplanned,
        "skipped": sim.skipped,
        "mode": req.mode,
        "message": msg,
        "co_shipment_results": sim.co_shipment_results or [],
        "co_shipment_exceptions": sim.co_shipment_exceptions or [],
    }


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
    return write_simulation(db, req, sim, username, revision_id=revision_id, commit=commit, replace_manual=replace_manual)


def add_manual_line(db: Session, line: ManualPlanLineIn, username: str) -> PlanLine:
    order = db.get(Order, line.order_id)
    op = db.get(RoutingOperation, line.operation_id)
    if not order or not op:
        raise ValueError("Siparis veya operasyon bulunamadi")
    total = op.hours_for(order.quantity)
    hours = line.planned_hours if line.planned_hours is not None else total
    qty = line.planned_qty if line.planned_qty is not None else (order.quantity * hours / total if total > 0 else 0)
    pl = PlanLine(
        order_id=order.id,
        operation_id=op.id,
        work_center_id=op.work_center_id,
        week_start=cap.week_start(line.week_start),
        planned_hours=round(hours, 3),
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
    for pl in q.order_by(PlanLine.week_start, PlanLine.work_center_id, PlanLine.id).all():
        if pl.order is None or pl.operation is None or pl.work_center is None:
            continue  # bagli kayit silinmis (yetim plan satiri)
        batch_nos: list[str] = []
        batch_no = ""
        batch_id = pl.production_batch_id
        if pl.production_batch:
            batch_no = pl.production_batch.batch_no
            batch_nos = [
                f"{l.order.order_no}{f'/{l.order.position_no}' if l.order and l.order.position_no else ''}"
                for l in sorted(pl.production_batch.orders, key=lambda x: x.order_id)
                if l.order
            ]
        out.append(
            PlanLineOut(
                id=pl.id,
                order_id=pl.order_id,
                order_no=batch_no if batch_id else pl.order.order_no,
                position_no="" if batch_id else (pl.order.position_no or ""),
                production_batch_id=batch_id,
                batch_no=batch_no,
                batch_order_nos=batch_nos,
                customer=pl.order.customer,
                due_date=effective_due(pl.order) if pl.order else None,
                item_code=pl.order.item.code,
                operation_id=pl.operation_id,
                operation_seq=pl.operation.seq,
                work_center_id=pl.work_center_id,
                work_center_code=pl.work_center.code,
                week_start=pl.week_start,
                planned_hours=pl.planned_hours,
                planned_qty=pl.planned_qty,
                semi_finished_code=pl.semi_finished_code or (pl.operation.semi_finished_code if pl.operation else ""),
                mode=pl.mode,
                strategy=pl.strategy or "",
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
    result = []
    for w in wcs:
        rows = []
        unit = w.capacity_unit_hours or 1.0
        ovl = cap.Overrides(db, w.id)
        for wk in wk_list:
            c_raw = cap.week_capacity_hours(db, w, wk)
            c_plan = cap.planning_capacity_hours(db, w, wk)
            p = planned.get((w.id, wk), 0.0)
            fc = forecast.get((w.id, wk), 0.0)
            a = actual.get((w.id, wk), 0.0)
            remaining = max(p - a, 0.0)
            idle = max(c_plan - p, 0.0)
            n_days = len(cap.working_days(w, wk, wk + timedelta(days=6), ovl))
            daily_cap = c_raw / n_days if n_days else 0.0
            rem_days = remaining / daily_cap if daily_cap > 0 else 0.0
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
                    actual_hours=round(a, 2),
                    actual_utilization=round(a / c_raw, 3) if c_raw > 0 else 0.0,
                    remaining_hours=round(remaining, 2),
                    remaining_days=round(rem_days, 2),
                    idle_hours=round(idle, 2),
                    capacity_units=round(c_raw / unit, 2),
                    planned_units=round(p / unit, 2),
                    actual_units=round(a / unit, 2),
                )
            )
        result.append(WorkCenterLoad(work_center_id=w.id, work_center_code=w.code, weeks=rows))
    return result


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
    total = cap.daily_capacity_hours(wc, day, emp, ovl.get(day))
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
            return _fail(
                "insufficient_capacity",
                f"Is merkezi '{wc.code}' icin planlanabilir kapasite tanimli degil (0 saat/hafta). "
                "Personel ve calisma takvimini kontrol edin.",
            )
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

    horizon_days = max(int(req.horizon_days or LEADTIME_DEFAULT_HORIZON_DAYS), 7)
    horizon_end = req.start + timedelta(days=horizon_days)
    wc_ids = list({op.work_center_id for op in ops})
    planned_all = planned_hours_by_week(db, wc_ids, cap.week_start(req.start), horizon_end)
    planned_by_wc: dict[int, dict[date, float]] = defaultdict(dict)
    for (wc_id, wk), h in planned_all.items():
        planned_by_wc[wc_id][wk] = h

    rules = scen.RuleLookup(db)
    steps: list[LeadTimeStep] = []
    first_wc = ops[0].work_center
    cursor = datetime.combine(req.start, cap.first_shift_start(first_wc, req.start, cap.Overrides(db, first_wc.id).get(req.start)))
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
        sched = _schedule_op(db, wc, hours, earliest, planned_by_wc[wc.id], horizon_end)
        step_start = sched.start
        step_end = sched.end
        if sched.status == "scheduled" and prev_end is not None and step_end and step_end < prev_end:
            step_end = prev_end + timedelta(seconds=op.cycle_time_sec or 0)
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
    for pl in sorted(pls, key=plan_line_priority_key):
        if pl.order is None or pl.operation is None:
            continue
        ps, pe = _line_window_in_week(db, wc, wk, pl.id, pls)
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

    label = (req.label or "").strip() or f"TAH-{item.code}-{datetime.now():%m%d%H%M}"
    order = Order(
        order_no=label,
        customer="Tahmin",
        due_date=end_dt,
        item_id=item.id,
        quantity=req.quantity,
        status="forecast",
        note="Yeni is terminleme tahmini",
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
        hours_left = float(step.hours)
        total_hours = hours_left
        while hours_left > 1e-6 and idx < len(weeks):
            week = weeks[idx]
            avail = remaining.get((op.work_center_id, week), 0.0)
            if avail > _WEEK_FULL_EPS:
                take = min(avail, hours_left)
                qty = req.quantity * (take / total_hours) if total_hours > 0 else 0.0
                db.add(
                    PlanLine(
                        order_id=order.id,
                        operation_id=op.id,
                        work_center_id=op.work_center_id,
                        week_start=week,
                        planned_hours=round(take, 3),
                        planned_qty=round(qty, 2),
                        mode="forecast",
                        created_by=username,
                    )
                )
                remaining[(op.work_center_id, week)] = avail - take
                hours_left -= take
                created += 1
            if hours_left > 1e-6:
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
