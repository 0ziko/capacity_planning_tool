"""Load report parity and query-growth regression (no wall-clock thresholds)."""

from datetime import date, time, timedelta

import pytest
from sqlalchemy import event

from app.models import Item, Order, PlanLine, ProductionActual, ResourceCalendarException, RoutingOperation, WorkCenter, WorkCenterShift, WorkCenterWeek
from app.services import capacity, gantt, planning
from app.services.calendar_capacity import daily_labor_capacity_hours
from app.services.kpi_units import plan_and_output_kpis_for_range, week_plan_and_output_kpis

WK = date(2026, 9, 14)


def make_wc(db, code):
    wc = WorkCenter(code=code, name=code, is_active=True, is_planned=True, planning_reserve_pct=12.5)
    db.add(wc)
    db.flush()
    db.add(WorkCenterShift(work_center_id=wc.id, name="Day", weekdays="0,1,2,3,4", start_time=time(8), end_time=time(16), headcount=3, efficient_hours_per_person=4.12345))
    db.commit()
    return wc


def test_calendar_matches_daily_reference_and_refreshes(db):
    wc = make_wc(db, "LOAD-CALENDAR")
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=WK, headcount=2, working_days=6))
    db.add_all([
        ResourceCalendarException(resource_type="work_center", resource_id=wc.id, cal_date=WK + timedelta(days=2), exception_kind="holiday"),
        # Partial-day holidays have never reduced the weekly labor model.
        ResourceCalendarException(resource_type="work_center", resource_id=wc.id, cal_date=WK, exception_kind="holiday", start_time=time(9), end_time=time(10)),
    ])
    db.commit()
    calendar = capacity.LaborCapacityCalendar(db, wc, WK, WK + timedelta(days=13))
    for start in (WK, WK + timedelta(days=7)):
        ov = capacity.Overrides(db, wc.id)
        expected = sum(daily_labor_capacity_hours(db, wc, start + timedelta(days=i), capacity.employee_count(db, wc), ov) for i in range(7))
        assert calendar.capacity(start, start + timedelta(days=6)).capacity_hours == round(expected, 2)
    before = planning.load(db, [wc.id], WK, 2)[0]
    wc.planning_reserve_pct = 25
    db.add(ResourceCalendarException(resource_type="work_center", resource_id=wc.id, cal_date=WK + timedelta(days=1), exception_kind="holiday"))
    db.commit()
    after = planning.load(db, [wc.id], WK, 2)[0]
    assert after.weeks[0].capacity_hours < before.weeks[0].capacity_hours
    assert after.weeks[0].planning_capacity_hours == round(after.weeks[0].capacity_hours * .75, 2)


def test_empty_horizon_queries_do_not_grow_with_weeks(db):
    wc = make_wc(db, "LOAD-QUERIES")
    counts = []
    for weeks in (1, 8, 52):
        db.expire_all()
        statements = []
        def record(conn, cursor, statement, *args):
            statements.append(statement)
        event.listen(db.bind, "before_cursor_execute", record)
        try:
            rows = planning.load(db, [wc.id], WK, weeks)
        finally:
            event.remove(db.bind, "before_cursor_execute", record)
        assert len(rows[0].weeks) == weeks
        counts.append(len(statements))
    assert max(counts) <= 20, counts
    assert max(counts) - min(counts) <= 1, counts


def test_bulk_kpis_preserve_week_scope_output_and_fifo(db, monkeypatch):
    wc = make_wc(db, "LOAD-KPIS")
    item = Item(code="LOAD-ITEM", name="Item")
    db.add(item)
    db.flush()
    op = RoutingOperation(item_id=item.id, seq=10, work_center_id=wc.id, operation_name="Op", cycle_time_sec=3600)
    db.add(op)
    db.flush()
    orders = [Order(order_no=f"LOAD-{i}", item_id=item.id, quantity=10, due_date=WK, status="open") for i in range(3)]
    db.add_all(orders)
    db.flush()
    for index, week in ((0, WK), (1, WK + timedelta(days=7))):
        db.add(PlanLine(order_id=orders[index].id, operation_id=op.id, work_center_id=wc.id, week_start=week, planned_hours=10, planned_qty=10, mode="auto"))
    db.add_all([
        ProductionActual(prod_date=WK, item_id=item.id, work_center_id=wc.id, operation_seq=10, order_no="", quantity=3, earned_hours=3),
        ProductionActual(prod_date=WK, item_id=item.id, work_center_id=wc.id, operation_seq=10, order_no=orders[2].order_no, quantity=7, earned_hours=7),
        ProductionActual(prod_date=WK + timedelta(days=8), item_id=item.id, work_center_id=wc.id, operation_seq=10, order_no=orders[1].order_no, quantity=2, earned_hours=2),
    ])
    db.commit()
    def unnecessary_index(*args):
        pytest.fail("Sequence-based production must not load the full WIP index")
    monkeypatch.setattr(gantt, "wip_index", unnecessary_index)
    for as_of in (WK - timedelta(days=1), WK, WK + timedelta(days=8)):
        bulk = plan_and_output_kpis_for_range(db, [wc.id], WK, WK + timedelta(days=14), as_of=as_of)
        for offset in (0, 7):
            week = WK + timedelta(days=offset)
            assert bulk[(wc.id, week)] == week_plan_and_output_kpis(db, wc.id, week, as_of=as_of)
    assert bulk[(wc.id, WK)]["standard_hour_equivalent_output"] == 10
    assert bulk[(wc.id, WK)]["plan_matched_output_hours"] == 3
    assert bulk[(wc.id, WK + timedelta(days=7))]["plan_matched_output_hours"] == 5


def test_wip_fallback_still_resolves_production(db, monkeypatch):
    wc = make_wc(db, "LOAD-WIP")
    item = Item(code="LOAD-WIP-ITEM", name="Item")
    db.add(item)
    db.flush()
    op = RoutingOperation(item_id=item.id, seq=10, work_center_id=wc.id, operation_name="Op", cycle_time_sec=3600, semi_finished_code="LOAD-WIP-CODE")
    db.add(op)
    db.flush()
    order = Order(order_no="LOAD-WIP-ORDER", item_id=item.id, quantity=10, due_date=WK, status="open")
    db.add(order)
    db.flush()
    db.add(ProductionActual(prod_date=WK, item_id=item.id, work_center_id=wc.id, operation_seq=None, semi_finished_code="LOAD-WIP-CODE", order_no=order.order_no, quantity=4, earned_hours=4))
    db.commit()
    calls = []
    original = gantt.wip_index
    def counted_index(session):
        calls.append(True)
        return original(session)
    monkeypatch.setattr(gantt, "wip_index", counted_index)
    result = gantt._production_map(db, wc.id, {order.id: order}, WK)
    assert result[(order.id, op.id)]["hours"] == 4
    assert len(calls) == 1
