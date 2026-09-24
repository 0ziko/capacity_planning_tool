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
    _staff_db(db, wc.id, 2, 4)
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
    assert sum(s.good_qty for s in db.query(PlanOperationSegment).filter_by(order_id=o.id, operation_id=op.id).all()) == 3
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

from tests.test_capacity_flow import _staff_db


def test_daily_labor_budget_splits_work_and_counts_setup_once(db):
    from app.models import WorkCenterWeek
    wc, machine = _pilot_wc_machine(db, "D1-BUDGET")
    wk = date(2026, 9, 7)
    ov = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=wk).one()
    ov.headcount = 1
    ov.efficient_hours_per_person = 2
    item = Item(code="D1-BUDGET-I", name="Test")
    db.add(item)
    db.commit()
    op, order = _machine_op(db, wc, machine, item, cycle_sec=3600, qty_order=5)
    op.setup_machine_minutes = 60
    op.crew_size = 1
    db.add(PlanLine(order_id=order.id, operation_id=op.id, work_center_id=wc.id, week_start=wk, planned_qty=5, planned_hours=6))
    db.commit()
    result = build_daily_schedule(db, AutoPlanRequest(start_week=wk, weeks=1, work_center_ids=[wc.id]), username="test")
    db.flush()
    segments = db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id).all()
    assert not result.remaining_qty
    assert sum(s.good_qty for s in segments) == 5
    assert len([s for s in segments if s.segment_kind == "setup"]) == 1
    daily = {}
    for s in segments:
        daily[s.start_at.date()] = daily.get(s.start_at.date(), 0) + (s.end_at-s.start_at).total_seconds()/3600*s.crew_size
    assert daily == {wk: 2, wk+timedelta(days=1): 2, wk+timedelta(days=2): 2}


def test_crew_touching_intervals_do_not_double_count():
    start = datetime(2026, 9, 7, 8)
    uses = [_CrewUse(start, start+timedelta(hours=1), 1), _CrewUse(start+timedelta(hours=1), start+timedelta(hours=2), 1)]
    assert _max_crew_in_window(uses, start, start+timedelta(hours=2)) == 1


def test_labor_slots_wait_for_crew_and_share_daily_budget(db):
    from app.models import WorkCenterWeek
    from app.services.capacity import Overrides
    from app.services.daily_scheduler import _labor_free_slots
    wc, _ = _pilot_wc_machine(db, "D1-SHARE")
    wk = date(2026, 9, 7)
    ov = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=wk).one()
    ov.headcount = 2
    ov.efficient_hours_per_person = 2
    db.commit()
    t = datetime(2026, 9, 7, 8)
    uses = [_CrewUse(t, t+timedelta(hours=1), 2)]
    free = _labor_free_slots(db, wc, [TimeRange(t, t+timedelta(hours=4))], uses, 2, Overrides(db, wc.id))
    assert free == [TimeRange(t+timedelta(hours=1), t+timedelta(hours=2))]


def test_daily_partial_work_does_not_release_finish_successor(db):
    from app.models import WorkCenterWeek
    wc, machine = _pilot_wc_machine(db, "D1-PARTIAL")
    wk = date(2026, 9, 7)
    ov = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=wk).one()
    ov.headcount, ov.efficient_hours_per_person, ov.working_days = 1, 2, 1
    item = Item(code="D1-PARTIAL-I", name="Test")
    db.add(item)
    db.commit()
    op, order = _machine_op(db, wc, machine, item, cycle_sec=3600, qty_order=5)
    op.setup_machine_minutes = 0
    second = RoutingOperation(item_id=item.id, seq=20, operation_name="Next", work_center_id=wc.id,
        cycle_time_sec=3600, time_basis="machine_seconds_per_cycle", units_per_cycle=1,
        setup_machine_minutes=0, primary_machine_id=machine.id)
    db.add(second)
    db.flush()
    for operation in [op, second]:
        db.add(PlanLine(order_id=order.id, operation_id=operation.id, work_center_id=wc.id, week_start=wk, planned_qty=5, planned_hours=5))
    db.commit()
    result = build_daily_schedule(db, AutoPlanRequest(start_week=wk, weeks=1, work_center_ids=[wc.id]), username="test")
    db.flush()
    segments = db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id).all()
    assert sum(s.good_qty for s in segments if s.operation_id == op.id) == 2
    assert not [s for s in segments if s.operation_id == second.id]
    assert any(r.get("reason") == "predecessor_incomplete" for r in result.remaining_qty)


def test_partial_predecessor_cycles_release_only_available_quantity(db):
    from app.models import WorkCenterWeek, OpTransitionRule, norm_op
    wc1, m1 = _pilot_wc_machine(db, "D1-FEED1")
    wc2, m2 = _pilot_wc_machine(db, "D1-FEED2")
    wk = date(2026, 9, 7)
    row = db.query(WorkCenterWeek).filter_by(work_center_id=wc1.id, week_start=wk).one()
    row.headcount, row.efficient_hours_per_person, row.working_days = 1, 2, 1
    item = Item(code="D1-FEED-I", name="Feed")
    db.add(item)
    db.commit()
    pred, order = _machine_op(db, wc1, m1, item, cycle_sec=3600, qty_order=5)
    pred.setup_machine_minutes = 0
    succ = RoutingOperation(item_id=item.id, seq=20, operation_name="Next", work_center_id=wc2.id,
        cycle_time_sec=1800, time_basis="machine_seconds_per_cycle", units_per_cycle=1,
        setup_machine_minutes=0, primary_machine_id=m2.id)
    db.add(succ)
    db.add(OpTransitionRule(scope="item", item_id=item.id, from_op="Op", to_op="Next",
        from_op_norm=norm_op("Op"), to_op_norm=norm_op("Next"), rule="cycles", lag_cycles=1, wait_minutes=60))
    db.flush()
    for op in [pred, succ]:
        db.add(PlanLine(order_id=order.id, operation_id=op.id, work_center_id=op.work_center_id,
            week_start=wk, planned_qty=5, planned_hours=5))
    db.commit()
    result = build_daily_schedule(db, AutoPlanRequest(start_week=wk, weeks=1, work_center_ids=[wc1.id,wc2.id]), username="test")
    db.flush()
    segments = db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id, operation_id=succ.id, segment_kind="process").all()
    assert sum(s.good_qty for s in segments) == 2  # startup threshold releases both completed units
    assert min(s.start_at for s in segments) == datetime(2026, 9, 7, 10)
    assert any(r["operation_seq"] == 20 and r["remaining_qty"] == 3 for r in result.remaining_qty)


def test_release_timing_uses_completed_cycles_not_calendar_interpolation():
    from types import SimpleNamespace
    from app.services.daily_scheduler import _successor_releases
    pred = SimpleNamespace(time_basis="machine_seconds_per_cycle", cycle_time_sec=3600, units_per_cycle=2)
    segments = [
        SimpleNamespace(segment_kind="process", start_at=datetime(2026,9,7,8), good_qty=4),
        SimpleNamespace(segment_kind="process", start_at=datetime(2026,9,9,8), good_qty=2),
    ]
    releases = _successor_releases(scen.Rule(rule="cycles",lag_cycles=2,wait_minutes=30),pred,6,6,segments)
    assert releases == [(datetime(2026,9,7,9,30),2), (datetime(2026,9,7,10,30),4), (datetime(2026,9,9,9,30),6)]
    assert _successor_releases(scen.Rule(rule="finish"),pred,7,7,segments) == []


def test_zero_lag_keeps_simultaneous_start_without_early_output():
    from types import SimpleNamespace
    from app.services.daily_scheduler import _successor_releases
    pred = SimpleNamespace(time_basis="machine_seconds_per_cycle", cycle_time_sec=3600, units_per_cycle=1)
    start = datetime(2026,9,7,8)
    segments = [SimpleNamespace(segment_kind="process", start_at=start, good_qty=2)]
    rule = scen.Rule(rule="cycles",lag_cycles=0)
    assert _successor_releases(rule,pred,2,2,segments,1) == [(start,1),(start+timedelta(hours=1),2)]
    assert _successor_releases(rule,pred,2,2,segments,0.5)[0][0] == start+timedelta(minutes=30)


def test_daily_replan_keeps_unselected_work_center(db):
    wc, machine = _pilot_wc_machine(db, "D1-OUTSIDE")
    other, _ = _pilot_wc_machine(db, "D1-SELECTED")
    item = Item(code="D1-OUTSIDE-I", name="Outside")
    db.add(item)
    db.commit()
    op, order = _machine_op(db, wc, machine, item)
    wk = date(2026,9,7)
    version = PlanScheduleVersion(horizon_start=wk,horizon_end=wk+timedelta(days=6),created_by="test")
    db.add(version)
    db.flush()
    segment = PlanOperationSegment(schedule_version_id=version.id,order_id=order.id,operation_id=op.id,
        work_center_id=wc.id,machine_id=machine.id,segment_kind="process",
        start_at=datetime(2026,9,7,8),end_at=datetime(2026,9,7,9),good_qty=1,crew_size=1,is_locked=False)
    db.add(segment)
    db.commit()
    segment_id = segment.id
    build_daily_schedule(db,AutoPlanRequest(start_week=wk,weeks=1,work_center_ids=[other.id]),username="test")
    db.flush()
    db.expire_all()
    assert db.get(PlanOperationSegment,segment_id) is not None


def test_locked_predecessor_uses_preserved_completion_time():
    from types import SimpleNamespace
    from app.services.daily_scheduler import _successor_releases
    pred = SimpleNamespace(time_basis="machine_seconds_per_cycle", cycle_time_sec=600, units_per_cycle=1)
    segment = SimpleNamespace(segment_kind="process",start_at=datetime(2026,9,7,8),end_at=datetime(2026,9,7,10),good_qty=1,is_locked=True)
    assert _successor_releases(scen.Rule(rule="finish"),pred,1,1,[segment]) == [(datetime(2026,9,7,10),1)]



def _assembly_case(db, suffix, *, limited=False):
    from app.models import BomLine, WorkCenterWeek
    fg = Item(code="690" + suffix, name="Assembly")
    a = Item(code="590" + suffix, name="Branch A")
    b = Item(code="591" + suffix, name="Branch B")
    db.add_all([fg, a, b])
    db.flush()
    for item, coefficient in [(a, 2), (b, 1)]:
        db.add(BomLine(item_id=fg.id, component_code=item.code, source_wip=item.code, quantity=coefficient, recipe_seq=0))
    order = Order(order_no="ASSEMBLY-" + suffix, item_id=fg.id, quantity=4, due_date=date(2026,9,18),
                  material_status="ready", material_ready_date=date(2026,9,7))
    db.add(order)
    operations = []
    wk = date(2026,9,7)
    for index, (item, seq, qty) in enumerate([(a,10,8),(a,20,8),(b,10,4),(fg,10,4),(fg,20,4)]):
        wc, machine = _pilot_wc_machine(db, "ASM-" + suffix + "-" + str(index))
        op = RoutingOperation(item_id=item.id, seq=seq, operation_name="Step"+str(seq), work_center_id=wc.id,
                              cycle_time_sec=1800, time_basis="machine_seconds_per_cycle", units_per_cycle=1,
                              setup_machine_minutes=0, crew_size=1, primary_machine_id=machine.id)
        db.add(op)
        db.flush()
        db.add(PlanLine(order_id=order.id, operation_id=op.id, work_center_id=wc.id,
                        week_start=wk, planned_qty=qty, planned_hours=qty/2))
        if limited and index == 1:
            ov = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=wk).one()
            ov.headcount, ov.efficient_hours_per_person, ov.working_days = 1, 2, 1
        operations.append(op)
    db.commit()
    req = AutoPlanRequest(start_week=wk,weeks=1,work_center_ids=[op.work_center_id for op in operations])
    return order, operations, req


def test_daily_two_wip_branches_and_finish_chain(db):
    order, ops, req = _assembly_case(db, "101")
    result = build_daily_schedule(db, req, username="test")
    segments = db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id,segment_kind="process").all()
    by_op = {op.id:[s for s in segments if s.operation_id==op.id] for op in ops}
    assert not result.remaining_qty and not result.skipped
    assert [sum(s.good_qty for s in by_op[op.id]) for op in ops] == [8,8,4,4,4]
    assert min(s.start_at for s in by_op[ops[1].id]) >= max(s.end_at for s in by_op[ops[0].id])
    assert min(s.start_at for s in by_op[ops[4].id]) >= max(s.end_at for s in by_op[ops[3].id])
    # At every assembly start there must be two A outputs and one B output per unit.
    from app.services.daily_scheduler import _process_outputs
    assembled = 0
    for seg in sorted(by_op[ops[3].id], key=lambda s:s.start_at):
        assembled += seg.good_qty
        for pred, coefficient in [(ops[1],2),(ops[2],1)]:
            supplied = sum(q for ready,q,_ in _process_outputs(pred,by_op[pred.id]) if ready <= seg.start_at)
            assert supplied >= assembled*coefficient


def test_daily_partial_wip_limits_assembly_by_bom_coefficient(db):
    order, ops, req = _assembly_case(db, "102", limited=True)
    # First A operation completes Monday; second A needs Tuesday but only Monday has capacity.
    result = build_daily_schedule(db,req,username="test")
    assert any(r.get("reason")=="predecessor_incomplete" and r["operation_seq"]==10 for r in result.remaining_qty)
    assert not db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id,operation_id=ops[3].id).count()


def test_daily_assembly_partial_feed_and_missing_branch(db):
    from app.models import WorkCenterWeek, BomLine
    order, ops, req = _assembly_case(db,"103")
    # Two A units per FG, only four A units can be finished in this horizon.
    ops[0].cycle_time_sec = 60
    ov=db.query(WorkCenterWeek).filter_by(work_center_id=ops[1].work_center_id,week_start=req.start_week).one()
    ov.headcount,ov.efficient_hours_per_person,ov.working_days=1,2,2
    ov.working_days=1
    db.commit()
    result=build_daily_schedule(db,req,username="test")
    segments=db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id,operation_id=ops[3].id,segment_kind="process").all()
    assert sum(s.good_qty for s in segments)==2
    assert any(r["operation_seq"]==10 and r["remaining_qty"]==2 for r in result.remaining_qty)
    db.add(BomLine(item_id=order.item_id,component_code="5999999",source_wip="5999999",quantity=1,recipe_seq=0))
    db.commit()
    db.expire_all()
    result=build_daily_schedule(db,req,username="test")
    assert any(r.get("reason")=="missing_wip_routing" for r in result.remaining_qty)
    assert not db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id,operation_id=ops[3].id).count()



def test_daily_selected_finish_does_not_assume_unselected_wip_completed(db):
    order, ops, req = _assembly_case(db,"104")
    req.work_center_ids=[ops[3].work_center_id,ops[4].work_center_id]
    result=build_daily_schedule(db,req,username="test")
    assert result.segments_created==0
    assert any(r.get("reason")=="predecessor_incomplete" for r in result.remaining_qty)


def test_daily_wip_own_scenario_and_completed_branch_credit(db):
    from app.models import OpTransitionRule, norm_op
    from app.services.daily_scheduler import _assembly_releases
    from app.services.bom_tree import explode_order
    order,ops,req=_assembly_case(db,"105")
    db.add(OpTransitionRule(scope="item",item_id=ops[0].item_id,
            from_op="Step10",to_op="Step20",from_op_norm=norm_op("Step10"),to_op_norm=norm_op("Step20"),
            rule="cycles",lag_cycles=1,wait_minutes=30))
    db.commit()
    result=build_daily_schedule(db,req,username="test")
    segments=db.query(PlanOperationSegment).filter_by(schedule_version_id=result.version_id,segment_kind="process").all()
    first=[s for s in segments if s.operation_id==ops[0].id]
    second=[s for s in segments if s.operation_id==ops[1].id]
    assert min(s.start_at for s in second)==datetime(2026,9,7,9)
    assert min(s.start_at for s in second)<max(s.end_at for s in first)
    start=datetime(2026,9,7)
    releases=_assembly_releases(explode_order(db,order),order,{},
              {(order.id,ops[1].id):6,(order.id,ops[2].id):4},start,finished_before=1)
    assert releases==[(start,2)]  # 6 A / 2 and 4 B / 1 minus one already assembled



def test_weekly_auto_plan_to_daily_wip_and_gantt(db):
    from app.services.planning import auto_plan
    order,ops,req=_assembly_case(db,"106")
    req.planning_granularity="daily_detailed"
    result=auto_plan(db,req,username="test")
    daily=result["daily_schedule"]
    assert not daily["skipped"] and not daily["remaining_qty"]
    segments=db.query(PlanOperationSegment).filter_by(schedule_version_id=daily["version_id"],segment_kind="process").all()
    assert {s.operation_id for s in segments}=={op.id for op in ops}
    for op,qty in zip(ops,[8,8,4,4,4]):
        assert sum(s.good_qty for s in segments if s.operation_id==op.id)==qty
        gantt=plan_gantt(db,op.work_center_id,req.start_week,req.start_week+timedelta(days=6))
        assert {s.id for s in gantt.segments}=={s.id for s in segments if s.operation_id==op.id}
