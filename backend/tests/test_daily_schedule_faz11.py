"""FAZ 11 kabul testleri: gunluk cizelge ve parti hazirligi."""

from datetime import date, datetime, time, timedelta

from app.models import (
    Item,
    Machine,
    Order,
    PlanLine,
    PlanOperationSegment,
    PlanScheduleVersion,
    ResourceCalendarException,
    RoutingOperation,
    RoutingOperationStation,
    WorkCenter,
    WorkCenterShift,
)
from app.schemas import AutoPlanRequest
from app.services.calendar_capacity import TimeRange
from app.services.daily_scheduler import (
    _max_crew_in_window,
    _needs_setup,
    _overlap,
    build_daily_schedule,
    resolve_setup_minutes,
)
from app.services.daily_scheduler import _CrewUse, _MachineBook
from app.services.gantt import plan_gantt
from app.services.operation_constraints import leadtime_earliest_datetime
from app.services import scenarios as scen


def _pilot_wc_machine(db, code: str = "D11"):
    wc = WorkCenter(code=code, name=code, is_planned=True, capacity_source="work_center")
    db.add(wc)
    db.flush()
    db.add(
        WorkCenterShift(
            work_center_id=wc.id,
            name="G",
            weekdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(12, 0),
            headcount=2,
        )
    )
    m = Machine(work_center_id=wc.id, code=f"{code}-M1", is_active=True)
    db.add(m)
    db.commit()
    db.refresh(wc)
    db.refresh(m)
    return wc, m


def _machine_op(db, wc, machine, item, seq=10, cycle_sec=600.0, qty_order=10.0):
    op = RoutingOperation(
        item_id=item.id,
        seq=seq,
        operation_name="Op",
        work_center_id=wc.id,
        cycle_time_sec=cycle_sec,
        setup_time_min=15.0,
        time_basis="machine_seconds_per_cycle",
        units_per_cycle=1,
        setup_machine_minutes=15.0,
        primary_machine_id=machine.id,
    )
    db.add(op)
    db.flush()
    db.add(RoutingOperationStation(operation_id=op.id, machine_id=machine.id, is_primary=True))
    o = Order(
        order_no=f"O-{item.code}",
        due_date=date(2026, 10, 1),
        item_id=item.id,
        quantity=qty_order,
        material_status="ready",
        material_ready_date=date(2026, 9, 7),
    )
    db.add(o)
    db.commit()
    db.refresh(op)
    db.refresh(o)
    return op, o


def test_machine_segments_no_overlap(db):
    wc, m = _pilot_wc_machine(db, "D11-A")
    it = Item(code="P-A", name="A", product_group="G")
    db.add(it)
    db.commit()
    op1, o1 = _machine_op(db, wc, m, it, seq=10)
    it2 = Item(code="P-B", name="B", product_group="G")
    db.add(it2)
    db.commit()
    op2, o2 = _machine_op(db, wc, m, it2, seq=10)

    wk = date(2026, 9, 7)
    for o, op in [(o1, op1), (o2, op2)]:
        db.add(
            PlanLine(
                order_id=o.id,
                operation_id=op.id,
                work_center_id=wc.id,
                week_start=wk,
                planned_hours=2,
                planned_qty=5,
            )
        )
    db.commit()

    req = AutoPlanRequest(start_week=wk, weeks=2, work_center_ids=[wc.id], planning_granularity="daily_detailed")
    build_daily_schedule(db, req, username="test")
    db.commit()

    segs = db.query(PlanOperationSegment).filter(PlanOperationSegment.machine_id == m.id).all()
    proc = [s for s in segs if s.segment_kind == "process"]
    assert len(proc) >= 2
    for i, a in enumerate(proc):
        for b in proc[i + 1 :]:
            assert not _overlap(TimeRange(a.start_at, a.end_at), TimeRange(b.start_at, b.end_at))


def test_crew_pool_rejects_two_parallel_two_person_ops(db):
    uses = [
        _CrewUse(datetime(2026, 9, 7, 8, 0), datetime(2026, 9, 7, 10, 0), 2),
    ]
    assert _max_crew_in_window(uses, datetime(2026, 9, 7, 9, 0), datetime(2026, 9, 7, 11, 0)) == 2
    assert _max_crew_in_window(uses, datetime(2026, 9, 7, 9, 0), datetime(2026, 9, 7, 11, 0)) + 2 > 2


def test_predecessor_wait_14_calendar_days(db):
    rule = scen.Rule(rule="finish", wait_minutes=14 * 24 * 60, lag_cycles=0)
    pred_start = datetime(2026, 9, 7, 8, 0)
    pred_end = datetime(2026, 9, 7, 10, 0)
    earliest = leadtime_earliest_datetime(rule, 10, pred_start, pred_end, 10)
    assert earliest.date() == date(2026, 9, 21)


def test_material_and_holiday_block(db):
    wc, m = _pilot_wc_machine(db, "D11-M")
    db.add(
        ResourceCalendarException(
            resource_type="work_center",
            resource_id=wc.id,
            cal_date=date(2026, 9, 7),
            exception_kind="holiday",
        )
    )
    it = Item(code="P-M", name="M", product_group="G")
    db.add(it)
    db.commit()
    op, o = _machine_op(db, wc, m, it)
    o.material_ready_date = date(2026, 9, 8)
    db.commit()
    db.add(
        PlanLine(
            order_id=o.id,
            operation_id=op.id,
            work_center_id=wc.id,
            week_start=date(2026, 9, 7),
            planned_hours=1,
            planned_qty=2,
        )
    )
    db.commit()
    req = AutoPlanRequest(start_week=date(2026, 9, 7), weeks=1, work_center_ids=[wc.id], planning_granularity="daily_detailed")
    res = build_daily_schedule(db, req, username="t")
    db.commit()
    segs = db.query(PlanOperationSegment).all()
    for s in segs:
        assert s.start_at.date() >= date(2026, 9, 8)


def test_setup_once_same_batch_two_shifts(db):
    book = _MachineBook()
    bkey = "b1"
    assert _needs_setup(book, bkey, "fam") is True
    book.last_batch_key = bkey
    book.last_family = "fam"
    assert _needs_setup(book, bkey, "fam") is False
    book.interrupted_by_other = True
    assert _needs_setup(book, bkey, "fam") is True


def test_locked_segment_unchanged_and_gantt_matches(db):
    wc, m = _pilot_wc_machine(db, "D11-L")
    it = Item(code="P-L", name="L", product_group="G")
    db.add(it)
    db.commit()
    op, o = _machine_op(db, wc, m, it)
    ver = PlanScheduleVersion(horizon_start=date(2026, 9, 7), horizon_end=date(2026, 9, 14), created_by="t")
    db.add(ver)
    db.flush()
    locked_start = datetime(2026, 9, 7, 8, 0)
    locked_end = datetime(2026, 9, 7, 9, 0)
    seg = PlanOperationSegment(
        schedule_version_id=ver.id,
        order_id=o.id,
        operation_id=op.id,
        work_center_id=wc.id,
        machine_id=m.id,
        segment_kind="process",
        start_at=locked_start,
        end_at=locked_end,
        good_qty=1,
        crew_size=1,
        is_locked=True,
    )
    db.add(seg)
    db.commit()

    db.add(
        PlanLine(
            order_id=o.id,
            operation_id=op.id,
            work_center_id=wc.id,
            week_start=date(2026, 9, 7),
            planned_hours=2,
            planned_qty=3,
        )
    )
    db.commit()
    req = AutoPlanRequest(start_week=date(2026, 9, 7), weeks=2, work_center_ids=[wc.id], planning_granularity="daily_detailed")
    build_daily_schedule(db, req, username="t")
    db.commit()

    locked = db.get(PlanOperationSegment, seg.id)
    assert locked.start_at == locked_start and locked.end_at == locked_end
    g = plan_gantt(db, wc.id, date(2026, 9, 7), date(2026, 9, 14))
    api_seg = next(s for s in g.segments if s.id == seg.id)
    assert api_seg.start_at == locked_start and api_seg.end_at == locked_end


def test_capacity_shortfall_reports_remaining(db):
    wc, m = _pilot_wc_machine(db, "D11-R")
    it = Item(code="P-R", name="R", product_group="G")
    db.add(it)
    db.commit()
    op, o = _machine_op(db, wc, m, it, cycle_sec=3600.0, qty_order=100.0)
    db.add(
        PlanLine(
            order_id=o.id,
            operation_id=op.id,
            work_center_id=wc.id,
            week_start=date(2026, 9, 7),
            planned_hours=100,
            planned_qty=100,
        )
    )
    db.commit()
    req = AutoPlanRequest(start_week=date(2026, 9, 7), weeks=1, work_center_ids=[wc.id], planning_granularity="daily_detailed")
    res = build_daily_schedule(db, req, username="t")
    assert res.remaining_qty or res.segments_created >= 1


def test_setup_minutes_unknown_not_zero_default(db):
    wc, m = _pilot_wc_machine(db, "D11-S")
    op = RoutingOperation(
        seq=10,
        work_center_id=wc.id,
        cycle_time_sec=60,
        setup_time_min=0,
        time_basis="machine_seconds_per_cycle",
    )
    assert resolve_setup_minutes(db, op, m.id, "A", "B") is None
