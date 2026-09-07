"""Verimli is gucu kapasitesi hesaplari.

Kapasite (saat) = toplam( vardiya kisi sayisi x kisi basi verimli saat ) calisma gunleri boyunca.
Ornek: 10 kisi x 4 saat x 5 gun = 200 saat; birim = 10 saat => 20 birim.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Employee, Machine, WorkCenter, WorkCenterShift, WorkCenterWeek
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


# ---- Haftalik is gucu istisnalari (WorkCenterWeek) ----

class Overrides:
    """Bir is merkezinin haftalik istisnalarini gun bazinda verir; sorgular hafta bazinda onbelleklenir.
    ov.get(day) -> WorkCenterWeek | None. Tum alanlari None olan kayit 'istisna yok' sayilir."""

    def __init__(self, db: Session, wc_id: int):
        self.db = db
        self.wc_id = wc_id
        self._cache: dict[date, WorkCenterWeek | None] = {}

    def get(self, day: date) -> WorkCenterWeek | None:
        wk = week_start(day)
        if wk not in self._cache:
            self._cache[wk] = (
                self.db.query(WorkCenterWeek)
                .filter(WorkCenterWeek.work_center_id == self.wc_id, WorkCenterWeek.week_start == wk)
                .first()
            )
        return self._cache[wk]

    def preload(self, start: date, end: date) -> None:
        rows = (
            self.db.query(WorkCenterWeek)
            .filter(WorkCenterWeek.work_center_id == self.wc_id, WorkCenterWeek.week_start >= week_start(start), WorkCenterWeek.week_start <= end)
            .all()
        )
        wk = week_start(start)
        while wk <= end:
            self._cache[wk] = None
            wk += timedelta(days=7)
        for r in rows:
            self._cache[r.week_start] = r


def _shift_on_day(shift, day: date, ov: WorkCenterWeek | None) -> bool:
    if ov is not None and ov.working_days is not None:
        return day.weekday() < ov.working_days  # haftanin ilk N gunu (Pzt'den)
    return day.weekday() in shift.weekday_set()


def _headcount(shift, emp_count: int, wc: WorkCenter, ov: WorkCenterWeek | None) -> int:
    if ov is not None and ov.headcount is not None:
        return ov.headcount
    return shift_headcount(shift, emp_count, wc)


def _eff_hours(shift, wc: WorkCenter, ov: WorkCenterWeek | None) -> float:
    if ov is not None and ov.efficient_hours_per_person is not None:
        return ov.efficient_hours_per_person
    return shift_efficient_hours(shift, wc)


def daily_capacity_hours(wc: WorkCenter, day: date, emp_count: int, ov: WorkCenterWeek | None = None) -> float:
    total = 0.0
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ov):
            total += _headcount(shift, emp_count, wc, ov) * _eff_hours(shift, wc, ov)
    return total


def daily_nominal_hours(wc: WorkCenter, day: date, emp_count: int, ov: WorkCenterWeek | None = None) -> float:
    """Nominal (mesai) adam-saat: kisi x vardiya suresi."""
    total = 0.0
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ov):
            total += _headcount(shift, emp_count, wc, ov) * shift.nominal_hours()
    return total


def daily_headcount(wc: WorkCenter, day: date, emp_count: int, ov: WorkCenterWeek | None = None) -> int:
    """Gunun ilk vardiyasindaki kisi sayisi (saat/kisi donusumleri icin)."""
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ov):
            return _headcount(shift, emp_count, wc, ov)
    return _headcount(effective_shifts(wc)[0], emp_count, wc, ov)


def is_working_day(wc: WorkCenter, day: date, ov: WorkCenterWeek | None = None) -> bool:
    return any(_shift_on_day(s, day, ov) for s in effective_shifts(wc))


def working_days(wc: WorkCenter, start: date, end: date, ovl: Overrides | None = None) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_working_day(wc, d, ovl.get(d) if ovl else None):
            days.append(d)
        d += timedelta(days=1)
    return days


def first_shift_start(wc: WorkCenter, day: date, ov: WorkCenterWeek | None = None) -> time:
    starts = [s.start_time for s in effective_shifts(wc) if _shift_on_day(s, day, ov)]
    return min(starts) if starts else time(8, 0)


def capacity_for_range(db: Session, wc: WorkCenter, start: date, end: date) -> CapacityOut:
    emp = employee_count(db, wc)
    ovl = Overrides(db, wc.id)
    ovl.preload(start, end)
    days: list[CapacityDay] = []
    total = 0.0
    d = start
    while d <= end:
        h = daily_capacity_hours(wc, d, emp, ovl.get(d))
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


def week_profile(db: Session, wc: WorkCenter, wk: date) -> dict:
    """Bir haftanin etkin is gucu ozeti: kisi, kisi basi verimli saat, calisma gunu, kapasite + istisna kaydi."""
    wk = week_start(wk)
    emp = employee_count(db, wc)
    ov = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == wc.id, WorkCenterWeek.week_start == wk).first()
    days = [wk + timedelta(days=i) for i in range(7)]
    wdays = [d for d in days if is_working_day(wc, d, ov)]
    first = wdays[0] if wdays else wk
    hours = sum(daily_capacity_hours(wc, d, emp, ov) for d in wdays)
    hc = daily_headcount(wc, first, emp, ov)
    eff = _eff_hours(effective_shifts(wc)[0], wc, ov)
    return {
        "week_start": wk,
        "headcount": hc,
        "efficient_hours_per_person": eff,
        "working_days": len(wdays),
        "capacity_hours": round(hours, 2),
        "default_headcount": daily_headcount(wc, first, emp, None),
        "default_efficient_hours": shift_efficient_hours(effective_shifts(wc)[0], wc),
        "default_working_days": len([d for d in days if is_working_day(wc, d, None)]),
        "override": ov,
    }
