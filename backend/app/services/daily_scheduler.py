"""Deterministik gunluk cizelge (FAZ 11): makine araliklari + is gucu havuzu."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal

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
from app.services.calendar_capacity import TimeRange, machine_work_intervals, work_center_crew_pool_size
from app.services.material_schedule import MATERIAL_READY, MATERIAL_UNKNOWN
from app.services.operation_constraints import leadtime_earliest_datetime
from app.services.orders import effective_due
from app.services.routing_resource import compute_operation_need, eligible_machine_ids, missing_resource_definition

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
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return peak


def _material_earliest(order: Order) -> datetime | None:
    st = (order.material_status or "unknown").lower()
    if st == MATERIAL_UNKNOWN:
        return None
    if st != MATERIAL_READY:
        return None
    if order.material_ready_date:
        return datetime.combine(order.material_ready_date, time.min)
    return datetime.combine(date.today(), time.min)


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


def _pick_machine(
    db: Session,
    machines: list[Machine],
    process_hours: float,
    setup_minutes: float,
    earliest: datetime,
    horizon_end: datetime,
    machine_books: dict[int, _MachineBook],
    ovl: cap.Overrides,
) -> tuple[Machine | None, datetime | None]:
    best_m: Machine | None = None
    best_finish: datetime | None = None
    for m in sorted(machines, key=lambda x: x.code):
        book = machine_books.setdefault(m.id, _MachineBook())
        booked = list(book.segments)
        slots = _machine_free_slots(db, m, earliest, horizon_end, booked, ovl)
        need_h = process_hours + (setup_minutes / 60.0 if setup_minutes > 0 else 0.0)
        need_sec = need_h * 3600.0
        for slot in slots:
            if slot.start < earliest:
                continue
            dur = (slot.end - slot.start).total_seconds()
            if dur + 1e-6 >= need_sec:
                finish = slot.start + timedelta(seconds=need_sec)
                if best_finish is None or finish < best_finish:
                    best_finish = finish
                    best_m = m
                break
    return best_m, best_finish


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

    locked = (
        db.query(PlanOperationSegment)
        .filter(
            PlanOperationSegment.is_locked.is_(True),
            PlanOperationSegment.start_at < horizon_end,
            PlanOperationSegment.end_at > horizon_start,
        )
        .all()
    )

    db.query(PlanOperationSegment).filter(
        PlanOperationSegment.is_locked.is_(False),
        PlanOperationSegment.start_at >= horizon_start,
        PlanOperationSegment.start_at <= horizon_end,
    ).delete(synchronize_session=False)

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
    op_start_by_order: dict[tuple[int, int], datetime] = {}
    op_end_by_order: dict[tuple[int, int], datetime] = {}

    rules = scen.RuleLookup(db)

    for o in order_list:
        if not o.item:
            continue
        item_ops = sorted(o.item.operations, key=lambda x: x.seq) if o.item.operations else []
        prev_op: RoutingOperation | None = None
        for op in item_ops:
            key = (o.id, op.id)
            qty = qty_by_op_order.get(key, 0.0)
            if qty <= 1e-6:
                prev_op = op
                continue
            if missing_resource_definition(op):
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "reason": "missing_resource_definition"})
                prev_op = op
                continue
            mat_earliest = _material_earliest(o)
            if mat_earliest is None and req.material_policy == "strict":
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "reason": "material_unknown"})
                prev_op = op
                continue

            need = compute_operation_need(op, qty, setup_required=True)
            m_hours = need.machine_hours
            if m_hours is None or m_hours <= 0:
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "reason": "no_machine_hours"})
                prev_op = op
                continue

            mids = eligible_machine_ids(op)
            machines = [machines_by_id[mid] for mid in mids if mid in machines_by_id]
            if not machines:
                skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "reason": "no_eligible_machine"})
                prev_op = op
                continue

            earliest = horizon_start
            if mat_earliest and mat_earliest > earliest:
                earliest = mat_earliest
            if prev_op:
                pred_key = (o.id, prev_op.id)
                pred_start = op_start_by_order.get(pred_key, horizon_start)
                pred_end = op_end_by_order.get(pred_key, horizon_start)
                rule = rules.get(o.item, prev_op, op)
                earliest = max(earliest, leadtime_earliest_datetime(rule, qty, pred_start, pred_end, qty))

            wc = op.work_center or db.get(WorkCenter, op.work_center_id)
            if wc is None:
                prev_op = op
                continue
            ovl = cap.Overrides(db, wc.id)
            crew_need = op.crew_size if op.crew_size and op.crew_size > 0 else 1
            batch_id = batch_by_op_order.get(key)
            bkey = _batch_key(batch_id, o.id, op.id)
            to_family = (op.setup_family or op.operation_name or "").strip()

            setup_min = 0.0
            chosen, finish = _pick_machine(db, machines, m_hours, 0.0, earliest, horizon_end, machine_books, ovl)
            if chosen is None:
                remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "remaining_qty": qty})
                prev_op = op
                continue

            book = machine_books.setdefault(chosen.id, _MachineBook())
            if book.last_batch_key and book.last_batch_key != bkey:
                book.interrupted_by_other = True
            from_family = book.last_family or ""
            if _needs_setup(book, bkey, to_family):
                sm = resolve_setup_minutes(db, op, chosen.id, from_family, to_family)
                if sm is None:
                    skipped.append({"order_no": o.order_no, "operation_seq": op.seq, "reason": "setup_unknown"})
                    prev_op = op
                    continue
                setup_min = sm

            chosen, finish = _pick_machine(db, machines, m_hours, setup_min, earliest, horizon_end, machine_books, ovl)
            if chosen is None or finish is None:
                remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "remaining_qty": qty})
                prev_op = op
                continue

            booked = list(book.segments)
            slots = _machine_free_slots(db, chosen, earliest, horizon_end, booked, ovl)
            slot = next((s for s in slots if s.start >= earliest), None)
            if slot is None:
                remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "remaining_qty": qty})
                prev_op = op
                continue

            cursor = slot.start
            pool = work_center_crew_pool_size(wc, cursor.date(), ovl.get(cursor.date()))

            if setup_min > 0 and _needs_setup(book, bkey, to_family):
                setup_end = cursor + timedelta(minutes=setup_min)
                if _max_crew_in_window(crew_by_wc.setdefault(wc.id, []), cursor, setup_end) + crew_need > pool:
                    remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "remaining_qty": qty})
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
            total_cycles = int(math.ceil(qty / upc - 1e-12))

            while proc_left_h > 1e-9 and cursor < horizon_end:
                slots = _machine_free_slots(db, chosen, cursor, horizon_end, book.segments, ovl)
                slot = next((s for s in slots if s.end > cursor), None)
                if slot is None:
                    break
                cursor = max(cursor, slot.start)
                slot_sec = (slot.end - cursor).total_seconds()
                if slot_sec <= 1e-6:
                    break
                if _max_crew_in_window(crew_by_wc.setdefault(wc.id, []), cursor, min(cursor + timedelta(seconds=slot_sec), slot.end)) + crew_need > pool:
                    break
                take_h = min(proc_left_h, slot_sec / 3600.0)
                seg_end = cursor + timedelta(hours=take_h)
                cycles_in_seg = int(take_h / cycle_h) if cycle_h > 1e-9 else 0
                if cycles_in_seg < 1 and proc_left_h > take_h - 1e-9:
                    cycles_in_seg = 1 if take_h + 1e-9 >= cycle_h else 0
                good = min(cycles_in_seg * upc, qty - placed_qty)
                if good <= 0 and take_h + 1e-9 < cycle_h:
                    cursor = slot.end
                    continue
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
                placed_qty += good
                proc_left_h -= take_h
                cursor = seg_end
                book.last_batch_key = bkey
                book.last_family = to_family

            if placed_qty + 1e-6 < qty:
                remaining.append({"order_no": o.order_no, "operation_seq": op.seq, "remaining_qty": round(qty - placed_qty, 4)})

            if key not in op_start_by_order:
                op_start_by_order[key] = earliest
            op_end_by_order[key] = cursor
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
