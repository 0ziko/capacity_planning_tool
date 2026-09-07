"""Verimli is gucu kapasitesi hesaplari.

Kapasite (saat) = toplam( vardiya kisi sayisi x kisi basi verimli saat ) calisma gunleri boyunca.
Ornek: 10 kisi x 4 saat x 5 gun = 200 saat; birim = 10 saat => 20 birim.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Employee, Machine, WorkCenter, WorkCenterShift
from app.schemas import CapacityDay, CapacityOut


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass
class VirtualShift:
    """Vardiya tanimi yoksa kullanilan varsayilan: Pzt-Cum 08:00-18:00."""

    name: str = "Varsayilan"
    start_time: time = time(8, 0)
    end_time: time = time(18, 0)
    headcount: int = 0
    efficient_hours_per_person: float | None = None

    def weekday_set(self) -> set[int]:
        return {0, 1, 2, 3, 4}

    def nominal_hours(self) -> float:
        return 10.0


def effective_shifts(wc: WorkCenter) -> list:
    return list(wc.shifts) if wc.shifts else [VirtualShift()]


def wc_employee_count(db: Session, wc_id: int) -> int:
    """Is merkezine dogrudan bagli aktif personel."""
    return (
        db.query(func.count(Employee.id))
        .filter(Employee.work_center_id == wc_id, Employee.is_active.is_(True))
        .scalar()
        or 0
    )


def machine_employee_count(db: Session, wc_id: int) -> int:
    """Is merkezinin aktif makinelerine atanmis aktif personel."""
    return (
        db.query(func.count(Employee.id))
        .join(Machine, Machine.id == Employee.machine_id)
        .filter(Machine.work_center_id == wc_id, Machine.is_active.is_(True), Employee.is_active.is_(True))
        .scalar()
        or 0
    )


def employee_count(db: Session, wc: WorkCenter | int) -> int:
    """Kapasite hesabinda kullanilacak kisi sayisi (is merkezinin capacity_source ayarina gore)."""
    if isinstance(wc, int):
        wc = db.get(WorkCenter, wc)
        if wc is None:
            return 0
    if wc.capacity_source == "machines":
        return machine_employee_count(db, wc.id)
    return wc_employee_count(db, wc.id)


def shift_headcount(shift, emp_count: int, wc: WorkCenter | None = None) -> int:
    """Vardiyadaki kisi: makine modunda daima makine atamalari; aksi halde vardiya kisi
    sayisi (>0) yoksa is merkezi personeli."""
    if wc is not None and wc.capacity_source == "machines":
        return emp_count
    return shift.headcount if shift.headcount and shift.headcount > 0 else emp_count


def shift_efficient_hours(shift, wc: WorkCenter) -> float:
    return (
        shift.efficient_hours_per_person
        if shift.efficient_hours_per_person is not None
        else wc.default_efficient_hours
    )


def daily_capacity_hours(wc: WorkCenter, day: date, emp_count: int) -> float:
    total = 0.0
    for shift in effective_shifts(wc):
        if day.weekday() in shift.weekday_set():
            total += shift_headcount(shift, emp_count, wc) * shift_efficient_hours(shift, wc)
    return total


def daily_nominal_hours(wc: WorkCenter, day: date, emp_count: int) -> float:
    """Nominal (mesai) adam-saat: kisi x vardiya suresi."""
    total = 0.0
    for shift in effective_shifts(wc):
        if day.weekday() in shift.weekday_set():
            total += shift_headcount(shift, emp_count, wc) * shift.nominal_hours()
    return total


def is_working_day(wc: WorkCenter, day: date) -> bool:
    return any(day.weekday() in s.weekday_set() for s in effective_shifts(wc))


def working_days(wc: WorkCenter, start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_working_day(wc, d):
            days.append(d)
        d += timedelta(days=1)
    return days


def first_shift_start(wc: WorkCenter, day: date) -> time:
    starts = [s.start_time for s in effective_shifts(wc) if day.weekday() in s.weekday_set()]
    return min(starts) if starts else time(8, 0)


def capacity_for_range(db: Session, wc: WorkCenter, start: date, end: date) -> CapacityOut:
    emp = employee_count(db, wc)
    days: list[CapacityDay] = []
    total = 0.0
    d = start
    while d <= end:
        h = daily_capacity_hours(wc, d, emp)
        if h > 0:
            days.append(CapacityDay(day=d, hours=round(h, 2)))
            total += h
        d += timedelta(days=1)
    unit = wc.capacity_unit_hours or 1.0
    return CapacityOut(
        work_center_id=wc.id,
        work_center_code=wc.code,
        start=start,
        end=end,
        capacity_hours=round(total, 2),
        capacity_units=round(total / unit, 2),
        unit_hours=unit,
        days=days,
    )


def week_capacity_hours(db: Session, wc: WorkCenter, wk: date) -> float:
    wk = week_start(wk)
    return capacity_for_range(db, wc, wk, wk + timedelta(days=6)).capacity_hours
