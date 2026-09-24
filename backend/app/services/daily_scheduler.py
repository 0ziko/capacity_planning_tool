"""Deterministik gunluk cizelge (FAZ 11): makine araliklari + is gucu havuzu."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal

from sqlalchemy import and_, not_
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Item,
    Machine,
    Order,
    PlanLine,
    PlanOperationSegment,
    PlanScheduleVersion,
    ProductionBatch,
    RoutingOperation,
    SetupFamilyTransition,
    WorkCenter,
)
from app.schemas import AutoPlanRequest
from app.services import capacity as cap
from app.services import scenarios as scen
from app.services.calendar_capacity import TimeRange, machine_work_intervals, work_center_crew_pool_size, daily_labor_capacity_hours
from app.services.material_schedule import MATERIAL_READY, MATERIAL_EXPECTED, material_gate_for_order
from app.services.operation_constraints import max_successor_qty, assembly_cap_qty
from app.services.bom_tree import explode_order, fg_has_wip_structure, is_wip_asm_link
from app.services.remaining_work import produced_qty_map
from app.services.orders import effective_due
from app.services.routing_resource import compute_operation_need, eligible_machine_ids, missing_resource_definition, conveyor_kind, conveyor_units

SegmentKind = Literal["setup", "process"]


@dataclass
class _MachineBook:
    segments: list[TimeRange] = field(default_factory=list)
    last_batch_key: str | None = None
    last_family: str | None = None
    interrupted_by_other: bool = False


@dataclass
class _CrewUse:
    start: datetime
    end: datetime
    crew: int


@dataclass
class DailyScheduleResult:
    version_id: int
    segments_created: int
    skipped: list[dict]
    remaining_qty: list[dict]


def _batch_key(batch_id: int | None, order_id: int, operation_id: int) -> str:
    if batch_id:
        return f"b{batch_id}"
    return f"o{order_id}-op{operation_id}"


def _overlap(a: TimeRange, b: TimeRange) -> bool:
    return a.start < b.end and b.start < a.end


def _max_crew_in_window(uses: list[_CrewUse], start: datetime, end: datetime) -> int:
    events: list[tuple[datetime, int]] = []
    for u in uses:
        if u.end <= start or u.start >= end:
            continue
        events.append((max(u.start, start), u.crew))
        events.append((min(u.end, end), -u.crew))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return peak


def _labor_free_slots(db, wc, slots, uses, crew, ovl):
    """Intersect machine openings with crew availability and remaining daily labor."""
    result = []
    budgets = {}
    for slot in slots:
        start = slot.start
        while start < slot.end:
            day = start.date()
            end = min(slot.end, datetime.combine(day + timedelta(days=1), time.min))
            pool = work_center_crew_pool_size(wc, day, ovl.get(day))
            if day not in budgets:
                midnight = datetime.combine(day, time.min)
                tomorrow = midnight + timedelta(days=1)
                used = sum(max(0.0, (min(u.end, tomorrow) - max(u.start, midnight)).total_seconds()) / 3600 * u.crew for u in uses)
                budgets[day] = max(0.0, cap.apply_planning_reserve(wc, daily_labor_capacity_hours(db, wc, day, 0, ovl)) - used)
            boundaries = sorted({start, end} | {t for u in uses for t in (u.start, u.end) if start < t < end})
            for a, b in zip(boundaries, boundaries[1:]):
                if pool < crew or _max_crew_in_window(uses, a, b) + crew > pool:
                    continue
                hours = min((b - a).total_seconds() / 3600, budgets[day] / crew)
                if hours > 1e-9:
                    result.append(TimeRange(a, a + timedelta(hours=hours)))
                    budgets[day] -= hours * crew
            start = end
    return result


def _material_earliest(order: Order) -> datetime | None:
    st = (order.material_status or "unknown").lower()
    if st not in (MATERIAL_READY, MATERIAL_EXPECTED):
        return None
    if order.material_ready_date:
        return datetime.combine(order.material_ready_date, time.min)
    return datetime.combine(date.today(), time.min)


def _process_outputs(pred, segments):
    """Quantity becomes available at actual cycle completion, including locked intervals."""
    cycle_hours = float((pred.machine_cycle_time_sec if pred.time_basis == "labor_seconds_per_unit" else pred.cycle_time_sec) or 0) / 3600
    units = 1 if pred.time_basis == "labor_seconds_per_unit" else max(int(pred.units_per_cycle or 1), 1)
    if conveyor_kind(pred):
        cycle_hours /= conveyor_units(pred)
        units = 1
    events = []
    for seg in segments:
        if seg.segment_kind != "process" or cycle_hours <= 0:
            continue
        for index in range(int(math.ceil(float(seg.good_qty) / units - 1e-9))):
            ready = seg.start_at + timedelta(hours=(index + 1) * cycle_hours)
            if getattr(seg, "is_locked", False):
                ready = seg.end_at
            events.append((ready, min(units, float(seg.good_qty) - index * units), seg))
    return sorted(events, key=lambda event: event[0])


def _successor_releases(rule, pred, required, successor_qty, segments, successor_cycle_hours=0.0):
    """Actual cycle completion events, using the shared transition quantity rule."""
    output = 0.0
    releases = []
    for ready, quantity, seg in _process_outputs(pred, segments):
        output += quantity
        ready += timedelta(minutes=rule.wait_minutes)
        if rule.rule == "cycles" and rule.lag_cycles == 0 and not getattr(seg, "is_locked", False):
            ready = max(seg.start_at + timedelta(minutes=rule.wait_minutes), ready - timedelta(hours=successor_cycle_hours))
        allowed = max_successor_qty(rule, required, output, successor_qty, 0)
        if allowed > 1e-9:
            releases.append((ready, allowed))
    return sorted(releases)


def _assembly_releases(jobs, order, ledger, produced, start, finished_before=0.0):
    """Use the weekly assembly ratio rule at each WIP completion event."""
    outputs, requirements, events = {}, [], []
    for job in jobs.wip_jobs:
        last = max(job.item.operations, key=lambda op: op.seq)
        code = job.semi_finished_code
        outputs[code] = float(produced.get((order.id, last.id), 0))
        requirements.append((code, job.quantity))
        for ready, quantity, _ in _process_outputs(last, ledger.get((order.id, last.id), [])):
            events.append((ready, code, quantity))
    def available():
        return max(0.0, assembly_cap_qty(outputs, requirements, order.quantity) - finished_before)
    releases = [(start, available())]
    for ready, code, quantity in sorted(events):
        outputs[code] += quantity
        releases.append((max(start, ready), available()))
    return releases


def resolve_setup_minutes(
    db: Session,
    op: RoutingOperation,
    machine_id: int,
    from_family: str,
    to_family: str,
) -> float | None:
    ff, tf = (from_family or "").strip(), (to_family or "").strip()
    if ff and tf:
        row = (
            db.query(SetupFamilyTransition)
            .filter(
                SetupFamilyTransition.machine_id == machine_id,
                SetupFamilyTransition.from_family == ff,
                SetupFamilyTransition.to_family == tf,
            )
            .first()
        )
        if row is not None:
            return float(row.setup_minutes)
    if op.setup_machine_minutes is not None:
        return float(op.setup_machine_minutes)
    tb = getattr(op, "time_basis", None) or "legacy_unspecified"
    if tb == "legacy_unspecified" and float(op.setup_time_min or 0) > 0:
        return float(op.setup_time_min)
    if float(op.setup_time_min or 0) == 0 and op.setup_machine_minutes is None:
        return None
    return None


def _machine_free_slots(
    db: Session,
    machine: Machine,
    horizon_start: datetime,
    horizon_end: datetime,
    booked: list[TimeRange],
    ovl: cap.Overrides,
) -> list[TimeRange]:
    d = horizon_start.date()
    end_d = horizon_end.date()
    free: list[TimeRange] = []
    while d <= end_d:
        for wr in machine_work_intervals(db, machine, d, ovl):
            if wr.end <= horizon_start or wr.start >= horizon_end:
                continue
            slot = TimeRange(max(wr.start, horizon_start), min(wr.end, horizon_end))
            parts = [slot]
            for b in booked:
                next_parts: list[TimeRange] = []
                for p in parts:
                    if not _overlap(p, b):
                        next_parts.append(p)
                        continue
                    if p.start < b.start:
                        next_parts.append(TimeRange(p.start, b.start))
                    if p.end > b.end:
                        next_parts.append(TimeRange(b.end, p.end))
                parts = next_parts
            free.extend(parts)
        d += timedelta(days=1)
    return sorted(free, key=lambda r: r.start)


def _needs_setup(book: _MachineBook, batch_key: str, to_family: str) -> bool:
    if book.last_batch_key != batch_key:
        return True
    if book.interrupted_by_other:
        return True
    if book.last_family and to_family and book.last_family != to_family:
        return True
    return False


def build_daily_schedule(
    db: Session,
    req: AutoPlanRequest,
    *,
    username: str,
    plan_lines: list[PlanLine] | None = None,
) -> DailyScheduleResult:
    """Haftalik plan satirlarindan pilot gunluk segmentler uretir."""
    start = cap.week_start(req.start_week)
    end = start + timedelta(days=req.weeks * 7 - 1)
    horizon_start = datetime.combine(start, time.min)
    horizon_end = datetime.combine(end, time(23, 59, 59))

    replace_filter = and_(
        PlanOperationSegment.is_locked.is_(False),
        PlanOperationSegment.start_at >= horizon_start,
        PlanOperationSegment.start_at <= horizon_end,
    )
    if req.work_center_ids:
        replace_filter = and_(replace_filter, PlanOperationSegment.work_center_id.in_(req.work_center_ids))
    locked = (
        db.query(PlanOperationSegment)
        .filter(
            not_(replace_filter),
            PlanOperationSegment.start_at < horizon_end,
            PlanOperationSegment.end_at > horizon_start,
        )
        .all()
    )

    db.query(PlanOperationSegment).filter(replace_filter).delete(synchronize_session="fetch")

    ver = PlanScheduleVersion(
        horizon_start=start,
        horizon_end=end,
        granularity="daily_detailed",
        created_by=username,
    )
    db.add(ver)
    db.flush()

    machine_books: dict[int, _MachineBook] = {}
    crew_by_wc: dict[int, list[_CrewUse]] = {}
    for seg in locked:
        book = machine_books.setdefault(seg.machine_id, _MachineBook())
        book.segments.append(TimeRange(seg.start_at, seg.end_at))
        crew_by_wc.setdefault(seg.work_center_id, []).append(_CrewUse(seg.start_at, seg.end_at, seg.crew_size))

    if plan_lines is None:
        q = db.query(PlanLine).filter(PlanLine.week_start >= start, PlanLine.week_start <= end)
        if req.work_center_ids:
            q = q.filter(PlanLine.work_center_id.in_(req.work_center_ids))
        plan_lines = q.all()

    qty_by_op_order: dict[tuple[int, int], float] = {}
    batch_by_op_order: dict[tuple[int, int], int | None] = {}
    for pl in plan_lines:
        key = (pl.order_id, pl.operation_id)
        qty_by_op_order[key] = qty_by_op_order.get(key, 0.0) + float(pl.planned_qty or 0)
        batch_by_op_order[key] = pl.production_batch_id

    order_ids = {pl.order_id for pl in plan_lines}
    # Read predecessor requirements outside the selected centers, without replanning them.
    dependency_qty = {}
    for line in db.query(PlanLine).filter(PlanLine.order_id.in_(order_ids),
                                        PlanLine.week_start >= start, PlanLine.week_start <= end).all():
        key = (line.order_id, line.operation_id)
        dependency_qty[key] = dependency_qty.get(key, 0.0) + float(line.planned_qty or 0)
    dependency_qty.update(qty_by_op_order)
    orders = {
        o.id: o
        for o in db.query(Order).options(joinedload(Order.item)).filter(Order.id.in_(order_ids)).all()
    } if order_ids else {}

    op_ids = {pl.operation_id for pl in plan_lines}
    ops = {
        o.id: o
        for o in (
            db.query(RoutingOperation)
            .options(joinedload(RoutingOperation.alt_stations), joinedload(RoutingOperation.work_center))
            .filter(RoutingOperation.id.in_(op_ids))
            .all()
        )
    } if op_ids else {}

    machines_by_id = {m.id: m for m in db.query(Machine).filter(Machine.is_active.is_(True)).all()}

    order_list = [o for o in orders.values() if o.item]
    if req.mode == "revenue":
        order_list.sort(key=lambda o: (-(float(o.quantity or 0) * float(o.unit_price or 0)), effective_due(o), o.order_no))
    else:
        order_list.sort(key=lambda o: (effective_due(o), o.order_no, o.id))

    skipped: list[dict] = []
    remaining: list[dict] = []
    created = 0
    process_by_order: dict[tuple[int, int], list[PlanOperationSegment]] = {}
    for seg in locked:
        if seg.segment_kind == "process":
            process_by_order.setdefault((seg.order_id, seg.operation_id), []).append(seg)

    rules = scen.RuleLookup(db)
    produced = produced_qty_map(db, as_of=date.today())[0] if any(fg_has_wip_structure(o.item) for o in order_list) else {}

    for o in order_list:
        if not o.item:
            continue
        jobs = explode_order(db, o) if fg_has_wip_structure(o.item) else None
        routes = [job.item for job in jobs.wip_jobs] if jobs else []
        if o.item.operations:
            routes.append(o.item)
        # Keep each WIP route separate: equal sequence numbers do not join branches.
        predecessors = {}
        item_ops = []
        for item in routes:
            route = sorted(item.operations, key=lambda operation: operation.seq)
            for index, operation in enumerate(route):
                predecessors[operation.id] = route[index - 1] if index else None
            item_ops.extend(route)
        declared_wips = {(bl.component_code or "").strip() for bl in o.item.bom_lines
                         if is_wip_asm_link(bl.component_code, bl.source_wip, bl.recipe_seq)} if jobs else set()
        missing_wips = declared_wips - {job.semi_finished_code for job in jobs.wip_jobs} if jobs else set()
        first_finish = min(o.item.operations, key=lambda operation: operation.seq) if o.item.operations else None
        for op in item_ops:
            prev_op = predecessors[op.id]
            key = (o.id, op.id)
            preserved_qty = sum(seg.good_qty for seg in process_by_order.get(key, []))
            qty = max(0.0, qty_by_op_order.get(key, 0.0) - preserved_qty)
            if qty <= 1e-6:
                prev_op = op
                continue
            if getattr(op.work_center, "planning_mode", "labor") == "line":
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code,
                                "reason": "weekly_line_capacity_only"})
                continue
            if missing_resource_definition(op):
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "reason": "missing_resource_definition"})
                prev_op = op
                continue
            mat_earliest = _material_earliest(o)
            material_gate = material_gate_for_order(o, policy=req.material_policy)
            if material_gate.blocks_planning:
                reason = "material_date_missing" if material_gate.status == MATERIAL_EXPECTED else "material_unknown"
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "reason": reason})
                prev_op = op
                continue

            need = compute_operation_need(op, qty, setup_required=False)
            m_hours = need.machine_hours
            if m_hours is None or m_hours <= 0:
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "reason": "no_machine_hours"})
                prev_op = op
                continue

            mids = eligible_machine_ids(op)
            machines = [machines_by_id[mid] for mid in mids if mid in machines_by_id]
            if not machines:
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "reason": "no_eligible_machine"})
                prev_op = op
                continue

            earliest = horizon_start
            if mat_earliest and mat_earliest > earliest:
                earliest = mat_earliest
            releases = None
            if prev_op:
                pred_key = (o.id, prev_op.id)
                pred_required = dependency_qty.get(pred_key, 0)
                if pred_required > 1e-6:
                    rule = rules.get(op.item, prev_op, op)
                    successor_cycle = float((op.machine_cycle_time_sec if op.time_basis == "labor_seconds_per_unit" else op.cycle_time_sec) or 0) / 3600
                    successor_cycle /= conveyor_units(op)
                    releases = _successor_releases(rule, prev_op, pred_required, qty + preserved_qty, process_by_order.get(pred_key, []), successor_cycle)
                    releases = [(ready, max(0.0, amount - preserved_qty)) for ready, amount in releases if amount > preserved_qty + 1e-9]
                    if not releases:
                        remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "remaining_qty": qty, "reason": "predecessor_incomplete"})
                        prev_op = op
                        continue
                    earliest = max(earliest, releases[0][0])

            if jobs and first_finish and op.id == first_finish.id:
                if missing_wips:
                    remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "remaining_qty": qty,
                                      "reason": "missing_wip_routing", "wip_codes": sorted(missing_wips)})
                    continue
                releases = _assembly_releases(jobs, o, process_by_order, produced, horizon_start,
                                               float(produced.get(key, 0)) + preserved_qty)
                releases = [(ready, amount) for ready, amount in releases if amount > 1e-9]
                if not releases:
                    remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "remaining_qty": qty,
                                      "reason": "predecessor_incomplete"})
                    continue
                earliest = max(earliest, releases[0][0])

            wc = op.work_center or db.get(WorkCenter, op.work_center_id)
            if wc is None:
                prev_op = op
                continue
            ovl = cap.Overrides(db, wc.id)
            crew_need = op.crew_size if op.crew_size and op.crew_size > 0 else 1
            batch_id = batch_by_op_order.get(key)
            bkey = _batch_key(batch_id, o.id, op.id)
            to_family = (op.setup_family or op.operation_name or "").strip()

            # Choose using the same labor limits used for actual placement.
            candidates = []
            setup_unknown = False
            uses = crew_by_wc.setdefault(wc.id, [])
            for machine in sorted(machines, key=lambda m: m.code):
                candidate_book = machine_books.setdefault(machine.id, _MachineBook())
                sm = resolve_setup_minutes(db, op, machine.id, candidate_book.last_family or "", to_family) if _needs_setup(candidate_book, bkey, to_family) else 0.0
                if sm is None:
                    setup_unknown = True
                    continue
                openings = _labor_free_slots(db, wc, _machine_free_slots(db, machine, earliest, horizon_end, candidate_book.segments, ovl), uses, crew_need, ovl)
                # Setup is indivisible and must fit inside an open slot.
                openings = [r for r in openings if r.duration_hours() > 1e-9]
                first = next((i for i, r in enumerate(openings) if r.duration_hours() + 1e-9 >= sm / 60.0), None)
                if first is None:
                    continue
                openings = openings[first:]
                available = sum(r.duration_hours() for r in openings) - sm / 60.0
                if available <= 1e-9:
                    continue
                left = m_hours + sm / 60.0
                finish = openings[-1].end
                for r in openings:
                    if left <= r.duration_hours():
                        finish = r.start + timedelta(hours=left)
                        break
                    left -= r.duration_hours()
                candidates.append((available + 1e-9 < m_hours, finish, machine.code, machine, sm, openings[0]))
            if not candidates:
                remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "remaining_qty": qty, "reason": "setup_unknown" if setup_unknown else "insufficient_capacity"})
                prev_op = op
                continue
            _, _, _, chosen, setup_min, slot = min(candidates, key=lambda c: c[:3])
            book = machine_books.setdefault(chosen.id, _MachineBook())
            from_family = book.last_family or ""
            cursor = slot.start
            pool = work_center_crew_pool_size(wc, cursor.date(), ovl.get(cursor.date()))

            if setup_min > 0 and _needs_setup(book, bkey, to_family):
                setup_end = cursor + timedelta(minutes=setup_min)
                if _max_crew_in_window(crew_by_wc.setdefault(wc.id, []), cursor, setup_end) + crew_need > pool:
                    remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "remaining_qty": qty})
                    prev_op = op
                    continue
                seg = PlanOperationSegment(
                    schedule_version_id=ver.id,
                    order_id=o.id,
                    operation_id=op.id,
                    production_batch_id=batch_id,
                    work_center_id=wc.id,
                    machine_id=chosen.id,
                    segment_kind="setup",
                    start_at=cursor,
                    end_at=setup_end,
                    good_qty=0.0,
                    crew_size=crew_need,
                    setup_from_family=from_family,
                    setup_to_family=to_family,
                )
                db.add(seg)
                book.segments.append(TimeRange(cursor, setup_end))
                crew_by_wc[wc.id].append(_CrewUse(cursor, setup_end, crew_need))
                created += 1
                cursor = setup_end
                book.last_batch_key = bkey
                book.last_family = to_family
                book.interrupted_by_other = False

            proc_left_h = m_hours
            placed_qty = 0.0
            upc = max(int(op.units_per_cycle or 1), 1)
            cycle_h = float(op.cycle_time_sec or 0) / 3600.0
            if op.time_basis == "labor_seconds_per_unit":
                upc = 1
                cycle_h = float(op.machine_cycle_time_sec or 0) / 3600.0
            if conveyor_kind(op):
                upc = 1
                cycle_h /= conveyor_units(op)

            while proc_left_h > 1e-9 and cursor < horizon_end:
                slots = _labor_free_slots(db, wc, _machine_free_slots(db, chosen, cursor, horizon_end, book.segments, ovl), crew_by_wc[wc.id], crew_need, ovl)
                slot = next((s for s in slots if s.end > cursor), None)
                if slot is None:
                    break
                cursor = max(cursor, slot.start)
                pool = work_center_crew_pool_size(wc, cursor.date(), ovl.get(cursor.date()))
                slot_sec = (slot.end - cursor).total_seconds()
                if slot_sec <= 1e-6:
                    break
                if _max_crew_in_window(crew_by_wc.setdefault(wc.id, []), cursor, min(cursor + timedelta(seconds=slot_sec), slot.end)) + crew_need > pool:
                    break
                available_qty = qty - placed_qty
                if releases is not None:
                    released = max((amount for ready, amount in releases if ready <= cursor), default=0.0)
                    available_qty = min(available_qty, max(0.0, released - placed_qty))
                    # A full cycle needs its feed before it starts (the final short cycle may use the remaining demand).
                    minimum = min(upc, qty - placed_qty)
                    if available_qty + 1e-6 < minimum:
                        next_ready = next((ready for ready, amount in releases if ready > cursor and amount - placed_qty + 1e-6 >= minimum), None)
                        if next_ready is None:
                            break
                        cursor = next_ready
                        continue
                take_h = min(proc_left_h, slot_sec / 3600.0)
                cycles_in_seg = int(math.floor((take_h + 1e-9) / cycle_h)) if cycle_h > 1e-9 else 0
                feed_cycles = int(math.floor((available_qty + 1e-6) / upc))
                if available_qty + 1e-6 >= qty - placed_qty:
                    feed_cycles = int(math.ceil((qty - placed_qty) / upc - 1e-9))
                cycles_in_seg = min(cycles_in_seg, feed_cycles)
                if cycles_in_seg < 1:
                    cursor = slot.end
                    continue
                take_h = min(proc_left_h, cycles_in_seg * cycle_h)
                seg_end = cursor + timedelta(hours=take_h)
                good = min(cycles_in_seg * upc, qty - placed_qty)
                seg = PlanOperationSegment(
                    schedule_version_id=ver.id,
                    order_id=o.id,
                    operation_id=op.id,
                    production_batch_id=batch_id,
                    work_center_id=wc.id,
                    machine_id=chosen.id,
                    segment_kind="process",
                    start_at=cursor,
                    end_at=seg_end,
                    good_qty=round(good, 4),
                    crew_size=crew_need,
                )
                db.add(seg)
                book.segments.append(TimeRange(cursor, seg_end))
                crew_by_wc[wc.id].append(_CrewUse(cursor, seg_end, crew_need))
                created += 1
                process_by_order.setdefault(key, []).append(seg)
                placed_qty += good
                proc_left_h -= take_h
                cursor = seg_end
                book.last_batch_key = bkey
                book.last_family = to_family

            if placed_qty + 1e-6 < qty:
                remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "operation_id": op.id, "item_code": op.item.code, "remaining_qty": round(qty - placed_qty, 4),
                                  "reason": "predecessor_incomplete" if releases is not None and max(amount for _, amount in releases) + 1e-6 < qty else "insufficient_capacity"})

            prev_op = op

    db.flush()
    return DailyScheduleResult(version_id=ver.id, segments_created=created, skipped=skipped, remaining_qty=remaining)


def segments_for_gantt(
    db: Session,
    work_center_id: int,
    start: date,
    end: date,
) -> list[PlanOperationSegment]:
    horizon_start = datetime.combine(start, time.min)
    horizon_end = datetime.combine(end, time(23, 59, 59))
    return (
        db.query(PlanOperationSegment)
        .options(
            joinedload(PlanOperationSegment.order),
            joinedload(PlanOperationSegment.operation),
            joinedload(PlanOperationSegment.machine),
        )
        .filter(
            PlanOperationSegment.work_center_id == work_center_id,
            PlanOperationSegment.start_at <= horizon_end,
            PlanOperationSegment.end_at >= horizon_start,
        )
        .order_by(PlanOperationSegment.start_at, PlanOperationSegment.id)
        .all()
    )
