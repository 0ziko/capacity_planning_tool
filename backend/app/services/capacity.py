"""Verimli is gucu kapasitesi hesaplari.

Kapasite (saat) = haftalik toplam kisi x kisi basi gunluk verimli saat x calisma gunu.
Ornek: 10 kisi x 4 saat x 5 gun = 200 saat; birim = 10 saat => 20 birim.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, object_session

from app.models import Employee, Machine, ResourceCalendarException, WorkCenter, WorkCenterShift, WorkCenterWeek
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
    """Eski personel kayit ozeti; haftalik kapasite icin kullanilmaz."""
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
    # Missing weekly staffing is not an implicit employee/shift allocation.
    # Planning approval handles the choice to correct it or proceed with zero.
    return 0


def _eff_hours(shift, wc: WorkCenter, ov: WorkCenterWeek | None) -> float:
    if ov is not None and ov.efficient_hours_per_person is not None:
        return ov.efficient_hours_per_person
    return shift_efficient_hours(shift, wc)


def _line_rows(wc, day):
    from app.services.station_capacity import station_rows
    db = object_session(wc)
    return station_rows(db, wc, week_start(day)) if db else []


# ---- Fazla mesai (18:00-21:00) ----
OVERTIME_WINDOW = (time(18, 0), time(21, 0))
OVERTIME_MAX_HOURS = 2.5  # kisi basi, gunluk (18:00-21:00 penceresi; mola dusulmus)
WEEKEND_OVERTIME_WINDOW = (time(8, 0), time(18, 0))
WEEKEND_OVERTIME_MAX_HOURS = 8.5  # kisi basi, Cmt/Paz 08:00-18:00 (mola dusulmus)
OVERTIME_WEEKLY_PERSON_MAX = 5 * OVERTIME_MAX_HOURS + 2 * WEEKEND_OVERTIME_MAX_HOURS  # 29.5 nominal sa/kisi/hafta
OVERTIME_LEGAL_YEARLY_HOURS = 270.0  # 4857 sayili Is Kanunu md. 41 (bilgi amacli)


def overtime_hours_per_person(ov: WorkCenterWeek | None) -> float:
    """Kisi basi nominal fazla mesai saati (ust sinir 2.5)."""
    if ov is None or not ov.overtime_headcount:
        return 0.0
    h = ov.overtime_hours_per_person if ov.overtime_hours_per_person is not None else OVERTIME_MAX_HOURS
    return max(0.0, min(float(h), OVERTIME_MAX_HOURS))


def overtime_on_day(wc: WorkCenter, day: date, ov: WorkCenterWeek | None) -> bool:
    """Fazla mesai haftanin ilk N calisma gununde uygulanir (N = overtime_days, None => tum calisma gunleri)."""
    if ov is None or not ov.overtime_headcount or not is_working_day(wc, day, ov):
        return False
    if ov.overtime_days is None:
        return True
    return day.weekday() < int(ov.overtime_days)


def weekend_overtime_hours_per_person(ov: WorkCenterWeek | None) -> float:
    """Kisi basi hafta sonu fazla mesai nominal saati (ust sinir 8.5)."""
    if ov is None or not ov.weekend_overtime_headcount:
        return 0.0
    h = ov.weekend_overtime_hours_per_person if ov.weekend_overtime_hours_per_person is not None else WEEKEND_OVERTIME_MAX_HOURS
    return max(0.0, min(float(h), WEEKEND_OVERTIME_MAX_HOURS))


def weekend_candidate_days(wc: WorkCenter, wk: date, ov: WorkCenterWeek | None) -> list[date]:
    """Haftanin normalde calisilmayan hafta sonu gunleri (once Cumartesi, sonra Pazar)."""
    wk = week_start(wk)
    return [d for d in (wk + timedelta(days=5), wk + timedelta(days=6)) if not is_working_day(wc, d, ov)]


def weekend_overtime_on_day(wc: WorkCenter, day: date, ov: WorkCenterWeek | None) -> bool:
    if ov is None or not ov.weekend_overtime_headcount or getattr(wc, "planning_mode", "labor") == "line":
        return False
    n = 2 if ov.weekend_overtime_days is None else int(ov.weekend_overtime_days)
    return day in weekend_candidate_days(wc, day, ov)[:n]


def overtime_person_hours_week(wc: WorkCenter, wk: date, ov: WorkCenterWeek | None) -> float:
    """Fazla mesai yapan bir kisinin haftalik nominal fazla mesai saati (hafta ici + hafta sonu)."""
    if ov is None:
        return 0.0
    wk = week_start(wk)
    days = [wk + timedelta(days=i) for i in range(7)]
    wd = sum(1 for d in days if overtime_on_day(wc, d, ov)) * overtime_hours_per_person(ov)
    we = sum(1 for d in days if weekend_overtime_on_day(wc, d, ov)) * weekend_overtime_hours_per_person(ov)
    return round(wd + we, 2)


def overtime_person_hours_period(db: Session, wc: WorkCenter, start: date, end: date, *, exclude_week: date | None = None) -> float:
    """Donemdeki haftalarin kisi basi fazla mesai toplami (haftanin Pazartesisi donemde ise sayilir)."""
    rows = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == wc.id, WorkCenterWeek.week_start >= week_start(start), WorkCenterWeek.week_start <= end).all()
    return round(sum(overtime_person_hours_week(wc, r.week_start, r) for r in rows if r.week_start != exclude_week), 2)


def overtime_cap_violation(db: Session | None, wc: WorkCenter, wk: date, ov: WorkCenterWeek | None) -> str | None:
    """Ayarlanabilir aylik/yillik kisi basi yumusak sinirlar (0 = kapali)."""
    if db is None or ov is None:
        return None
    from app.core.config import get_settings
    st = get_settings()
    this = overtime_person_hours_week(wc, wk, ov)
    if this <= 0:
        return None
    wk = week_start(wk)
    if st.overtime_monthly_cap_hours > 0:
        m0 = wk.replace(day=1)
        m1 = (m0.replace(month=m0.month % 12 + 1, year=m0.year + (m0.month == 12))) - timedelta(days=1)
        tot = overtime_person_hours_period(db, wc, m0, m1, exclude_week=wk) + this
        if tot > st.overtime_monthly_cap_hours + 1e-9:
            return f"Kisi basi aylik fazla mesai siniri asiliyor ({tot:.1f} > {st.overtime_monthly_cap_hours:g} sa)"
    if st.overtime_yearly_cap_hours > 0:
        tot = overtime_person_hours_period(db, wc, date(wk.year, 1, 1), date(wk.year, 12, 31), exclude_week=wk) + this
        if tot > st.overtime_yearly_cap_hours + 1e-9:
            return f"Kisi basi yillik fazla mesai siniri asiliyor ({tot:.1f} > {st.overtime_yearly_cap_hours:g} sa)"
    return None


def overtime_efficiency_ratio(wc: WorkCenter, ov: WorkCenterWeek | None) -> float:
    """Normal vardiyanin verimli/nominal orani; fazla mesai saatine ayni verim uygulanir."""
    shift = effective_shifts(wc)[0]
    nominal = float(shift.nominal_hours() or 0)
    if nominal <= 0:
        return 0.0
    return max(0.0, min(_eff_hours(shift, wc, ov) / nominal, 1.0))


def overtime_violation(
    wc: WorkCenter,
    ov: WorkCenterWeek | None,
    *,
    overtime_headcount: int | None,
    overtime_days: int | None,
    overtime_hours: float | None,
    emp_count: int = 0,
    weekend_headcount: int | None = None,
    weekend_days: int | None = None,
    weekend_hours: float | None = None,
    db: Session | None = None,
) -> str | None:
    """Fazla mesai kisitlari: kisi <= haftanin kisi sayisi, gun <= calisma gunu, saat <= 2.5 (18:00-21:00);
    hafta sonu: kisi <= haftanin kisi sayisi, gun <= calisilmayan hafta sonu gunu, saat <= 8.5 (08:00-18:00);
    ayarlanabilir aylik/yillik kisi basi sinirlar (db verilirse)."""
    if not overtime_headcount and not weekend_headcount:
        return None
    if getattr(wc, "planning_mode", "labor") == "line":
        return "Dizilim (hat) modundaki is merkezinde fazla mesai haftalik istasyon saatleriyle tanimlanir"
    wk = ov.week_start if ov is not None else None
    shift = effective_shifts(wc)[0]
    hc = _headcount(shift, emp_count, wc, ov) + int(getattr(ov, "overtime_extra_headcount", None) or 0)  # ek kişi (komşu merkez)
    if overtime_headcount:
        if overtime_headcount > hc:
            return f"Fazla mesai kisi sayisi ({overtime_headcount}) haftanin kisi sayisini ({hc}) asamaz"
        if wk is not None:
            wdays = len(working_days(wc, wk, wk + timedelta(days=6), _SingleOverride(ov)))
            if overtime_days is not None and overtime_days > wdays:
                return f"Fazla mesai gun sayisi ({overtime_days}) calisma gununu ({wdays}) asamaz"
        if overtime_hours is not None and overtime_hours > OVERTIME_MAX_HOURS + 1e-9:
            return f"Kisi basi fazla mesai en fazla {OVERTIME_MAX_HOURS} saat olabilir (18:00-21:00)"
    if weekend_headcount:
        if weekend_headcount > hc:
            return f"Hafta sonu fazla mesai kisi sayisi ({weekend_headcount}) haftanin kisi sayisini ({hc}) asamaz"
        if wk is not None:
            free = len(weekend_candidate_days(wc, wk, ov))
            if weekend_days is not None and weekend_days > free:
                return f"Hafta sonu fazla mesai gun sayisi ({weekend_days}) calisilmayan hafta sonu gununu ({free}) asamaz"
        if weekend_hours is not None and weekend_hours > WEEKEND_OVERTIME_MAX_HOURS + 1e-9:
            return f"Kisi basi hafta sonu fazla mesai en fazla {WEEKEND_OVERTIME_MAX_HOURS} saat olabilir (08:00-18:00)"
    if wk is not None and db is not None and ov is not None:
        return overtime_cap_violation(db, wc, wk, ov)
    return None


class _SingleOverride:
    """working_days() icin tek haftalik override sarmalayici."""

    def __init__(self, ov: WorkCenterWeek | None):
        self.ov = ov

    def get(self, day: date):
        return self.ov


def overtime_daily_capacity_hours(wc: WorkCenter, day: date, ov: WorkCenterWeek | None) -> float:
    """Gunun fazla mesai verimli kapasitesi (kisi x nominal saat x verim orani); is gucu modunda."""
    if getattr(wc, "planning_mode", "labor") == "line":
        return 0.0
    if weekend_overtime_on_day(wc, day, ov):
        return int(ov.weekend_overtime_headcount or 0) * weekend_overtime_hours_per_person(ov) * overtime_efficiency_ratio(wc, ov)
    if not overtime_on_day(wc, day, ov):
        return 0.0
    return int(ov.overtime_headcount or 0) * overtime_hours_per_person(ov) * overtime_efficiency_ratio(wc, ov)


def daily_capacity_hours(wc: WorkCenter, day: date, emp_count: int, ov: WorkCenterWeek | None = None) -> float:
    if getattr(wc, "planning_mode", "labor") == "line":
        return sum(r["capacity_hours"] for r in _line_rows(wc, day)) / 7
    if not is_working_day(wc, day, ov):
        return overtime_daily_capacity_hours(wc, day, ov)  # hafta sonu fazla mesaisi
    shift = effective_shifts(wc)[0]
    return _headcount(shift, emp_count, wc, ov) * _eff_hours(shift, wc, ov) + overtime_daily_capacity_hours(wc, day, ov)


def daily_nominal_hours(wc: WorkCenter, day: date, emp_count: int, ov: WorkCenterWeek | None = None) -> float:
    """Nominal (mesai) adam-saat: kisi x vardiya suresi."""
    if getattr(wc, "planning_mode", "labor") == "line":
        return sum(r["required_labor_hours"] for r in _line_rows(wc, day)) / 7
    total = 0.0
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ov):
            total += _headcount(shift, emp_count, wc, ov) * shift.nominal_hours()
    if overtime_on_day(wc, day, ov):
        total += int(ov.overtime_headcount or 0) * overtime_hours_per_person(ov)  # fazla mesai nominal adam-saati
    if weekend_overtime_on_day(wc, day, ov):
        total += int(ov.weekend_overtime_headcount or 0) * weekend_overtime_hours_per_person(ov)
    return total


def daily_headcount(wc: WorkCenter, day: date, emp_count: int, ov: WorkCenterWeek | None = None) -> int:
    """Gunun ilk vardiyasindaki kisi sayisi (saat/kisi donusumleri icin)."""
    if getattr(wc, "planning_mode", "labor") == "line":
        return sum(r["required_crew_size"] or 0 for r in _line_rows(wc, day) if r["capacity_hours"] > 0)
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ov):
            return _headcount(shift, emp_count, wc, ov)
    return _headcount(effective_shifts(wc)[0], emp_count, wc, ov)


def is_working_day(wc: WorkCenter, day: date, ov: WorkCenterWeek | None = None) -> bool:
    if getattr(wc, "planning_mode", "labor") == "line":
        return daily_capacity_hours(wc, day, 0) > 0
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
    if getattr(wc, "planning_mode", "labor") == "line":
        return time(0, 0)
    starts = [s.start_time for s in effective_shifts(wc) if _shift_on_day(s, day, ov)]
    return min(starts) if starts else time(8, 0)


class LaborCapacityCalendar:
    """Request-local capacity inputs; one fetch per input over the whole horizon.

    Never retained across requests, so edits to staffing/calendars are immediately
    visible. Daily whole-day holidays still override weekly staffing exceptions.
    """

    def __init__(self, db: Session, wc: WorkCenter, start: date, end: date):
        self.wc = wc
        self.line_hours = {}
        if wc.planning_mode == "line":
            from app.models import MachineWeek
            from app.services.routing_resource import station_crew
            crewed = [m.id for m in wc.machines if m.is_active and station_crew(m)]
            rows = db.query(MachineWeek.week_start, func.sum(MachineWeek.working_hours)).filter(
                MachineWeek.machine_id.in_(crewed) if crewed else False,
                MachineWeek.week_start >= week_start(start), MachineWeek.week_start <= end).group_by(MachineWeek.week_start).all() if crewed else []
            self.line_hours = {wk: float(hours or 0) for wk, hours in rows}
        self.start, self.end = start, end
        self.emp = employee_count(db, wc)
        self.overrides = Overrides(db, wc.id)
        self.overrides.preload(start, end)
        self.holidays = {
            row[0] for row in db.query(ResourceCalendarException.cal_date).filter(
                ResourceCalendarException.resource_type == "work_center",
                ResourceCalendarException.resource_id == wc.id,
                ResourceCalendarException.cal_date >= start,
                ResourceCalendarException.cal_date <= end,
                ResourceCalendarException.exception_kind == "holiday",
                ResourceCalendarException.start_time.is_(None),
                ResourceCalendarException.end_time.is_(None),
            ).all()
        }

    def capacity(self, start: date, end: date) -> CapacityOut:
        if start < self.start or end > self.end:
            raise ValueError("Capacity range is outside the loaded calendar")
        days: list[CapacityDay] = []
        total = 0.0
        d = start
        while d <= end:
            h = 0.0 if self.wc.planning_mode == "line" or d in self.holidays else daily_capacity_hours(self.wc, d, self.emp, self.overrides.get(d))
            if self.wc.planning_mode == "line":
                h = self.line_hours.get(week_start(d), 0.0) / 7
            if h > 0:
                days.append(CapacityDay(day=d, hours=round(h, 2)))
                total += h
            d += timedelta(days=1)
        unit = self.wc.capacity_unit_hours or 1.0
        return CapacityOut(
            work_center_id=self.wc.id, work_center_code=self.wc.code,
            start=start, end=end, capacity_hours=round(total, 2),
            capacity_units=round(total / unit, 2), unit_hours=unit, days=days,
        )


def capacity_for_range(db: Session, wc: WorkCenter, start: date, end: date) -> CapacityOut:
    return LaborCapacityCalendar(db, wc, start, end).capacity(start, end)


def week_capacity_hours(db: Session, wc: WorkCenter, wk: date) -> float:
    wk = week_start(wk)
    return capacity_for_range(db, wc, wk, wk + timedelta(days=6)).capacity_hours


def planning_capacity_hours(db: Session, wc: WorkCenter, wk: date) -> float:
    """Planlama/terminleme icin kullanilabilir kapasite (atil rezerv dusulmus)."""
    raw = week_capacity_hours(db, wc, wk)
    return apply_planning_reserve(wc, raw)


def apply_planning_reserve(wc: WorkCenter, raw: float) -> float:
    pct = max(0.0, min(float(getattr(wc, "planning_reserve_pct", 0.0) or 0.0), 99.0))
    return raw * (1.0 - pct / 100.0)


def week_profile(db: Session, wc: WorkCenter, wk: date) -> dict:
    """Bir haftanin etkin is gucu ozeti: kisi, kisi basi verimli saat, calisma gunu, kapasite + istisna kaydi."""
    wk = week_start(wk)
    emp = employee_count(db, wc)
    ov = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == wc.id, WorkCenterWeek.week_start == wk).first()
    days = [wk + timedelta(days=i) for i in range(7)]
    wdays = [d for d in days if is_working_day(wc, d, ov)]
    first = wdays[0] if wdays else wk
    hours = week_capacity_hours(db, wc, wk)
    hc = daily_headcount(wc, first, emp, ov)
    eff = 0.0 if wc.planning_mode == "line" else _eff_hours(effective_shifts(wc)[0], wc, ov)
    station_data = _line_rows(wc, wk) if wc.planning_mode == "line" else []
    ot_days = [d for d in days if overtime_on_day(wc, d, ov)]
    we_days = [d for d in days if weekend_overtime_on_day(wc, d, ov)]
    ot_hours = round(sum(overtime_daily_capacity_hours(wc, d, ov) for d in days), 2)
    we_hours = round(sum(overtime_daily_capacity_hours(wc, d, ov) for d in we_days), 2)
    ytd = overtime_person_hours_period(db, wc, date(wk.year, 1, 1), wk) if wc.planning_mode != "line" else 0.0
    return {
        "weekend_overtime_headcount": int(ov.weekend_overtime_headcount or 0) if ov else 0,
        "weekend_overtime_days": len(we_days),
        "weekend_overtime_hours_per_person": weekend_overtime_hours_per_person(ov),
        "weekend_overtime_capacity_hours": we_hours,
        "overtime_person_hours_week": overtime_person_hours_week(wc, wk, ov),
        "overtime_person_hours_ytd": ytd,
        "overtime_legal_yearly_hours": OVERTIME_LEGAL_YEARLY_HOURS,
        "overtime_proposed": bool(ov.overtime_proposed) if ov else False,
        "overtime_extra_headcount": int(ov.overtime_extra_headcount or 0) if ov else 0,
        "week_start": wk,
        "planning_mode": wc.planning_mode or "labor",
        "overtime_headcount": int(ov.overtime_headcount or 0) if ov else 0,
        "overtime_days": len(ot_days),
        "overtime_hours_per_person": overtime_hours_per_person(ov),
        "overtime_efficiency_ratio": round(overtime_efficiency_ratio(wc, ov), 3) if wc.planning_mode != "line" else 0.0,
        "overtime_capacity_hours": ot_hours,
        "base_capacity_hours": round(hours - ot_hours, 2),
        "required_crew_size": sum(r["required_crew_size"] or 0 for r in station_data if r["capacity_hours"] > 0),
        "line_hours_per_day": None,
        "required_labor_hours": round(sum(r["required_labor_hours"] for r in station_data), 2),
        "headcount": hc,
        "efficient_hours_per_person": eff,
        "working_days": len(wdays),
        "capacity_hours": round(hours, 2),
        "default_headcount": daily_headcount(wc, first, emp, None),
        "default_efficient_hours": shift_efficient_hours(effective_shifts(wc)[0], wc),
        "default_working_days": len([d for d in days if is_working_day(wc, d, None)]),
        "override": ov,
    }
