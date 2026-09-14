"""FAZ 10 kabul testleri: insan–makine ayrimi ve gunluk takvim altyapisi."""

from datetime import date, time

from app.models import (
    Employee,
    Machine,
    ResourceCalendarException,
    RoutingOperation,
    WorkCenter,
    WorkCenterShift,
)
from app.services.calendar_capacity import (
    daily_labor_capacity_hours,
    expand_shift_to_ranges,
    machine_daily_capacity_hours,
    night_shift_duration_hours,
)
from app.services.capacity import Overrides, employee_count
from app.services.routing_resource import compute_operation_need, legacy_hours_for, missing_resource_definition


def _wc(db, code: str = "PRS") -> WorkCenter:
    wc = WorkCenter(code=code, name=code, is_planned=True, capacity_source="work_center")
    db.add(wc)
    db.flush()
    db.add(
        WorkCenterShift(
            work_center_id=wc.id,
            name="Gunduz",
            weekdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(16, 0),
            headcount=2,
            efficient_hours_per_person=4.0,
        )
    )
    db.commit()
    db.refresh(wc)
    return wc


def test_two_crew_one_machine_hour(db):
    """2 kisi birlikte 1 saatlik makine isi -> 1 makine-saat, 2 adam-saat."""
    op = RoutingOperation(
        seq=10,
        operation_name="Pres",
        cycle_time_sec=3600.0,
        time_basis="machine_seconds_per_cycle",
        crew_size=2,
        units_per_cycle=1,
    )
    need = compute_operation_need(op, 1.0)
    assert need.machine_hours == 1.0
    assert need.labor_hours == 2.0
    assert need.elapsed_labor_hours == 1.0


def test_machine_capacity_not_from_headcount(db):
    """Tek pres 8 saat; personel iki kat -> pres kapasitesi 8 makine-saat."""
    wc = _wc(db, "ZZ-F10-MCAP")
    m = Machine(work_center_id=wc.id, code="PRS-1", is_active=True)
    db.add(m)
    db.flush()
    db.add(Employee(code="E1", name="A", work_center_id=wc.id, machine_id=m.id, is_active=True))
    db.add(Employee(code="E2", name="B", work_center_id=wc.id, machine_id=m.id, is_active=True))
    db.commit()
    db.refresh(m)
    day = date(2026, 9, 14)  # Pazartesi
    ovl = Overrides(db, wc.id)
    cap = machine_daily_capacity_hours(db, m, day, ovl)
    assert cap == 8.0
    assert employee_count(db, wc) == 2


def test_wednesday_holiday_only_that_day(db):
    """Carsamba tatil -> yalniz Carsamba kapasitesi 0."""
    wc = _wc(db, "ZZ-F10-HOL")
    db.add(
        ResourceCalendarException(
            resource_type="work_center",
            resource_id=wc.id,
            cal_date=date(2026, 9, 16),
            exception_kind="holiday",
        )
    )
    db.commit()
    emp = employee_count(db, wc)
    ovl = Overrides(db, wc.id)
    mon = daily_labor_capacity_hours(db, wc, date(2026, 9, 14), emp, ovl)
    wed = daily_labor_capacity_hours(db, wc, date(2026, 9, 16), emp, ovl)
    thu = daily_labor_capacity_hours(db, wc, date(2026, 9, 17), emp, ovl)
    assert mon > 0
    assert wed == 0.0
    assert thu > 0


def test_night_shift_two_date_ranges(db):
    """22:00-06:00 vardiyasi 8 saat ve iki tarih araligi."""
    assert night_shift_duration_hours(time(22, 0), time(6, 0)) == 8.0
    ranges = expand_shift_to_ranges(date(2026, 9, 14), time(22, 0), time(6, 0))
    assert len(ranges) == 2
    assert ranges[0].start.date() == date(2026, 9, 14)
    assert ranges[0].start.time() == time(22, 0)
    assert ranges[0].end.date() == date(2026, 9, 15)
    assert ranges[1].end.time() == time(6, 0)


def test_legacy_unchanged_and_missing_definition_warning(db):
    """Legacy rota ayni saat; ayrintili cizelge icin eksik tanim uyarisi."""
    op = RoutingOperation(
        seq=10,
        cycle_time_sec=120.0,
        setup_time_min=30.0,
        time_basis="legacy_unspecified",
    )
    qty = 5.0
    assert op.hours_for(qty) == legacy_hours_for(op, qty)
    need = compute_operation_need(op, qty)
    assert "missing_resource_definition" in need.warnings
    assert missing_resource_definition(op)
