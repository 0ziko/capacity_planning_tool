"""D1: weekly labor, horizon warnings and revision validity share capacity inputs."""

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest

from app.models import Item, Order, ResourceCalendarException, RoutingOperation, WorkCenter, WorkCenterWeek
from app.schemas import AutoPlanRequest
from app.services.capacity import week_capacity_hours, week_profile
from app.services.plan_input_fingerprint import compute_plan_input_fingerprint
from app.services.plan_preflight import plan_preflight
from app.services.planning import simulate
from app.services.planning import _schedule_op
from app.services.calendar_capacity import work_center_crew_pool_size

WEEK = date(2026, 9, 14)


@pytest.fixture
def labor_case(db):
    suffix = uuid4().hex[:8]
    wc = WorkCenter(code=f"LAB-{suffix}", name="Lazer", is_planned=True, is_active=True)
    item = Item(code=f"LAB-ITEM-{suffix}", name="Deneme")
    db.add_all([wc, item])
    db.flush()
    db.add(RoutingOperation(item_id=item.id, seq=10, operation_name="Kesim", work_center_id=wc.id, cycle_time_sec=3600))
    db.add(Order(order_no=f"LAB-O-{suffix}", item_id=item.id, quantity=150, due_date=date(2026, 9, 30), status="open"))
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=WEEK, headcount=5, efficient_hours_per_person=5, working_days=5))
    db.commit()
    return wc


def test_125_hours_without_employee_records_and_all_horizon_warnings(db, labor_case):
    wc = labor_case
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=WEEK + timedelta(weeks=1), headcount=0, efficient_hours_per_person=5, working_days=5))
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=WEEK + timedelta(weeks=2), headcount=5, working_days=5))
    db.commit()
    assert week_capacity_hours(db, wc, WEEK) == 125
    assert week_profile(db, wc, WEEK)["capacity_hours"] == 125
    result = plan_preflight(db, AutoPlanRequest(start_week=WEEK, weeks=4, work_center_ids=[wc.id]))
    assert result.needs_capacity_ack
    missing = {r.week_start: r.missing_fields for r in result.missing_labor_weeks}
    assert WEEK not in missing
    assert WEEK + timedelta(weeks=1) not in missing  # explicit zero is defined
    assert missing[WEEK + timedelta(weeks=2)] == ["efficient_hours_per_person"]
    assert len(missing[WEEK + timedelta(weeks=3)]) == 3
    zero = next(r for r in result.no_capacity if r.week_start == WEEK + timedelta(weeks=1))
    assert zero.headcount == 0 and "sıfır" in zero.detail
    assert not any(r.week_start == WEEK for r in result.no_capacity)


def test_week_profile_uses_the_same_holiday_capacity_as_planning(db, labor_case):
    wc = labor_case
    db.add(ResourceCalendarException(resource_type="work_center", resource_id=wc.id,
                                     cal_date=WEEK + timedelta(days=2), exception_kind="holiday"))
    db.commit()
    assert week_capacity_hours(db, wc, WEEK) == 100
    assert week_profile(db, wc, WEEK)["capacity_hours"] == 100


def test_all_centers_fingerprint_detects_weekly_labor_and_holiday_changes(db, labor_case):
    wc = labor_case
    req = AutoPlanRequest(start_week=WEEK, weeks=2, work_center_ids=[])
    before = compute_plan_input_fingerprint(db, req)
    row = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    row.headcount = 4
    db.commit()
    after = compute_plan_input_fingerprint(db, req)
    assert before != after
    db.add(ResourceCalendarException(resource_type="work_center", resource_id=wc.id,
                                     cal_date=WEEK, exception_kind="holiday"))
    db.commit()
    assert after != compute_plan_input_fingerprint(db, req)


def test_weekly_input_is_used_when_placing_150_units_across_weeks(db, labor_case):
    wc = labor_case
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=WEEK + timedelta(weeks=1),
                          headcount=5, efficient_hours_per_person=5, working_days=5))
    db.commit()
    result = simulate(db, AutoPlanRequest(start_week=WEEK, weeks=2, work_center_ids=[wc.id]))
    assert not result.unplanned
    assert sum(line.planned_qty for line in result.lines) == pytest.approx(150)
    by_week = {week: sum(line.planned_hours for line in result.lines if line.week_start == week)
               for week in result.weeks}
    assert by_week == {WEEK: 125, WEEK + timedelta(weeks=1): 25}


def test_leadtime_skips_zero_week_and_holiday(db, labor_case):
    wc = labor_case
    row = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    row.headcount = 0
    next_week = WEEK + timedelta(weeks=1)
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=next_week,
                          headcount=5, efficient_hours_per_person=5, working_days=5))
    db.add(ResourceCalendarException(resource_type="work_center", resource_id=wc.id,
                                     cal_date=next_week, exception_kind="holiday"))
    db.commit()
    result = _schedule_op(db, wc, 10, datetime.combine(WEEK, time(8)), {}, next_week + timedelta(days=6))
    assert result.status == "scheduled"
    assert result.start.date() == next_week + timedelta(days=1)
    assert result.scheduled_hours == 10


def test_daily_crew_obeys_weekly_zero_and_override(db, labor_case):
    from app.models import WorkCenterShift
    wc = labor_case
    db.add(WorkCenterShift(work_center_id=wc.id, name="Gündüz", weekdays="0,1,2,3,4",
                           start_time=time(8), end_time=time(18), headcount=20))
    db.commit()
    db.refresh(wc)
    ov = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    assert work_center_crew_pool_size(wc, WEEK, ov) == 5
    ov.headcount = 0
    assert work_center_crew_pool_size(wc, WEEK, ov) == 0


def test_preflight_includes_wip_work_center(db, labor_case):
    from app.models import BomLine
    from app.services.plan_preflight import _needed_hours_by_wc
    wc = labor_case
    order = db.query(Order).one()
    order.item.code = "699" + str(order.item.id).zfill(7)
    wip = Item(code="599" + str(order.item.id).zfill(7), name="Yarımamul")
    other = WorkCenter(code=f"WIP-{wc.id}", name="Yarımamul merkezi", is_planned=True)
    db.add_all([wip, other])
    db.flush()
    db.add(RoutingOperation(item_id=wip.id, seq=10, operation_name="WIP kesim", work_center_id=other.id, cycle_time_sec=3600))
    db.add(BomLine(item_id=order.item_id, component_code=wip.code, source_wip=wip.code, recipe_seq=0, quantity=2))
    db.commit()
    db.expire_all()
    need = _needed_hours_by_wc(db, {wc.id, other.id})
    assert need[wc.id] == 150
    assert need[other.id] == 300
    db.query(RoutingOperation).filter_by(item_id=wip.id).delete()
    db.commit()
    db.expire_all()
    result = plan_preflight(db, AutoPlanRequest(start_week=WEEK, weeks=1, work_center_ids=[wc.id, other.id]))
    assert not result.can_plan
    assert any(row.item_code == wip.code for row in result.no_routing)


def test_missing_staffing_requires_ack_and_can_be_corrected(client, auth, db, labor_case):
    from app.models import PlanLine, WorkCenterShift, Employee
    wc = labor_case
    row = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    row.headcount = None
    db.add(WorkCenterShift(work_center_id=wc.id, name="Eski", weekdays="0,1,2,3,4",
                           start_time=time(8), end_time=time(18), headcount=99))
    db.add(Employee(code=f"OLD-{wc.id}", name="Eski kayıt", work_center_id=wc.id))
    order = db.query(Order).one()
    op = db.query(RoutingOperation).filter_by(item_id=order.item_id).one()
    saved = PlanLine(order_id=order.id, operation_id=op.id, work_center_id=wc.id,
                     week_start=WEEK, planned_qty=1, planned_hours=1, mode="auto")
    db.add(saved)
    db.commit()
    saved_id = saved.id
    body = {"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc.id]}
    before = client.post("/api/plan/auto/preflight", headers=auth, json=body).json()
    assert before["missing_headcount_token"]
    assert before["missing_labor_weeks"][0]["capacity_hours"] == 0
    assert client.post("/api/plan/auto", headers=auth, json=body).status_code == 409
    db.expire_all()
    assert db.get(PlanLine, saved_id) is not None  # blocked before deleting any plan
    assert row.headcount is None  # unknown is never persisted as zero
    fixed = client.put(f"/api/workcenters/{wc.id}/weeks/{WEEK}", headers=auth,
                        json={"headcount": 5, "efficient_hours_per_person": 5, "working_days": 5})
    assert fixed.status_code == 200
    after = client.post("/api/plan/auto/preflight", headers=auth, json=body).json()
    assert after["missing_headcount_token"] is None
    assert not after["needs_capacity_ack"]
    assert client.post("/api/plan/auto", headers=auth, json=body).status_code == 200


def test_missing_staffing_ack_is_scoped_and_keeps_unknown(client, auth, db, labor_case):
    wc = labor_case
    row = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    row.headcount = None
    db.commit()
    body = {"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc.id]}
    check = client.post("/api/plan/auto/preflight", headers=auth, json=body).json()
    body["missing_headcount_ack"] = check["missing_headcount_token"]
    assert client.post("/api/plan/auto", headers=auth, json={**body, "weeks": 2}).status_code == 409
    result = client.post("/api/plan/auto", headers=auth, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 0
    db.expire_all()
    assert row.headcount is None


def test_revision_preview_rolls_back_and_approval_requires_staffing_ack(client, auth, db, labor_case):
    wc = labor_case
    row = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    row.headcount = None
    order = db.query(Order).one()
    order_id = order.id
    db.commit()
    created = client.post('/api/plan/revisions', headers=auth, json={
        'reason_codes': ['customer_postpone'], 'note': 'Eksik iş gücü',
        'start_week': WEEK.isoformat(), 'weeks': 1, 'work_center_ids': [wc.id], 'mode': 'due_date',
    })
    assert created.status_code == 200, created.text
    path = f"/api/plan/revisions/{created.json()['id']}"
    changed = client.post(path + '/changes', headers=auth, json={
        'entity_type': 'order', 'entity_id': order_id, 'field': 'revised_due_date', 'new_value': '2026-10-02',
    })
    assert changed.status_code == 200, changed.text
    calculated = client.post(path + '/calculate', headers=auth)
    assert calculated.status_code == 200, calculated.text
    check = client.post(path + '/preflight', headers=auth)
    assert check.status_code == 200, check.text
    token = check.json()['missing_headcount_token']
    assert token
    db.expire_all()
    assert db.get(Order, order_id).revised_due_date is None
    assert client.post(path + '/approve', headers=auth).status_code == 409
    db.expire_all()
    assert db.get(Order, order_id).revised_due_date is None
    applied = client.post(path + '/approve', headers=auth, params={'missing_headcount_ack': token})
    assert applied.status_code == 200, applied.text
    db.expire_all()
    assert db.get(Order, order_id).revised_due_date == date(2026, 10, 2)
    assert row.headcount is None


@pytest.mark.parametrize("second_start", [time(8), time(18)])
def test_weekly_staffing_is_total_across_shifts(db, labor_case, second_start):
    from app.models import WorkCenterShift
    wc = labor_case
    db.add_all([
        WorkCenterShift(work_center_id=wc.id, name="A", weekdays="0,1,2,3,4", start_time=time(8), end_time=time(18), headcount=99),
        WorkCenterShift(work_center_id=wc.id, name="B", weekdays="0,1,2,3,4", start_time=second_start, end_time=time(23), headcount=99),
    ])
    db.commit()
    db.refresh(wc)
    ov = db.query(WorkCenterWeek).filter_by(work_center_id=wc.id, week_start=WEEK).one()
    assert week_capacity_hours(db, wc, WEEK) == 125
    assert work_center_crew_pool_size(wc, WEEK, ov) == 5
    assert week_profile(db, wc, WEEK)["capacity_hours"] == 125


def test_preflight_shared_wip_queries_do_not_grow_per_order(db, labor_case):
    from sqlalchemy import event
    from app.models import BomLine
    from app.services.plan_preflight import _needed_hours_by_wc
    wc = labor_case
    order = db.query(Order).one()
    order.item.code = "699" + str(order.item.id).zfill(7)
    wip = Item(code="599" + str(order.item.id).zfill(7), name="Shared")
    db.add(wip)
    db.flush()
    db.add(RoutingOperation(item_id=wip.id, seq=10, operation_name="WIP", work_center_id=wc.id, cycle_time_sec=3600))
    db.add(BomLine(item_id=order.item_id, component_code=wip.code, source_wip=wip.code, recipe_seq=0, quantity=2))
    db.commit()
    engine = db.get_bind()
    def measure():
        db.expire_all()
        statements = []
        def count(*args): statements.append(1)
        event.listen(engine, "before_cursor_execute", count)
        try: result = _needed_hours_by_wc(db, {wc.id})
        finally: event.remove(engine, "before_cursor_execute", count)
        return len(statements), result[wc.id]
    before, hours = measure()
    assert hours == 450
    for i in range(20):
        db.add(Order(order_no=f"SHARED-{i}", item_id=order.item_id, quantity=1, due_date=WEEK, status="open"))
    db.commit()
    after, hours = measure()
    assert hours == 510
    assert after <= before + 2
