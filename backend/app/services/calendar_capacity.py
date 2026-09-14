"""Gunluk takvim: makine ve is gucu araliklari; istisna onceligi (FAZ 10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import Machine, MachineCalendarEntry, ResourceCalendarException, WorkCenter
from app.services.capacity import Overrides, VirtualShift, _shift_on_day, effective_shifts, is_working_day


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime

    def duration_hours(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds() / 3600.0)


def expand_shift_to_ranges(anchor: date, start: time, end: time) -> list[TimeRange]:
    """Gece vardiyasi ertesi güne tasir; iki (veya tek) gercek tarih araligi."""
    if start < end:
        return [TimeRange(datetime.combine(anchor, start), datetime.combine(anchor, end))]
    next_day = anchor + timedelta(days=1)
    return [
        TimeRange(datetime.combine(anchor, start), datetime.combine(next_day, time(0, 0))),
        TimeRange(datetime.combine(next_day, time(0, 0)), datetime.combine(next_day, end)),
    ]


def _whole_day_exception(db: Session, resource_type: str, resource_id: int, day: date, kind: str) -> bool:
    rows = (
        db.query(ResourceCalendarException)
        .filter(
            ResourceCalendarException.resource_type == resource_type,
            ResourceCalendarException.resource_id == resource_id,
            ResourceCalendarException.cal_date == day,
            ResourceCalendarException.exception_kind == kind,
        )
        .all()
    )
    for r in rows:
        if r.start_time is None and r.end_time is None:
            return True
    return False


def work_center_holiday(db: Session, wc_id: int, day: date) -> bool:
    return _whole_day_exception(db, "work_center", wc_id, day, "holiday")


def daily_labor_capacity_hours(
    db: Session,
    wc: WorkCenter,
    day: date,
    emp_count: int,
    ovl: Overrides | None = None,
) -> float:
    """Is gucu kapasitesi; gunluk tatil istisnasi haftalik override'dan onceliklidir."""
    from app.services import capacity

    ov = ovl.get(day) if ovl else None
    if work_center_holiday(db, wc.id, day):
        return 0.0
    return capacity.daily_capacity_hours(wc, day, emp_count, ov)


def _default_machine_intervals(wc: WorkCenter, day: date, ov) -> list[TimeRange]:
    out: list[TimeRange] = []
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ov):
            out.extend(expand_shift_to_ranges(day, shift.start_time, shift.end_time))
    if not out and isinstance(effective_shifts(wc)[0], VirtualShift):
        vs = effective_shifts(wc)[0]
        if _shift_on_day(vs, day, ov):
            out.extend(expand_shift_to_ranges(day, vs.start_time, vs.end_time))
    return out


def _subtract_blocked(ranges: list[TimeRange], blocks: list[TimeRange]) -> list[TimeRange]:
    if not blocks:
        return ranges
    result: list[TimeRange] = []
    for r in ranges:
        parts = [r]
        for b in blocks:
            next_parts: list[TimeRange] = []
            for p in parts:
                if p.end <= b.start or p.start >= b.end:
                    next_parts.append(p)
                    continue
                if p.start < b.start:
                    next_parts.append(TimeRange(p.start, b.start))
                if p.end > b.end:
                    next_parts.append(TimeRange(b.end, p.end))
            parts = next_parts
        result.extend(parts)
    return result


def machine_daily_capacity_hours(
    db: Session,
    machine: Machine,
    day: date,
    ovl: Overrides | None = None,
) -> float:
    """Makine-saat: personel sayisindan turetilmez."""
    wc = machine.work_center or db.get(WorkCenter, machine.work_center_id)
    if wc is None or not machine.is_active:
        return 0.0
    ov = ovl.get(day) if ovl else None
    if work_center_holiday(db, wc.id, day):
        return 0.0
    if not is_working_day(wc, day, ov):
        return 0.0

    cal_entries = (
        db.query(MachineCalendarEntry)
        .filter(MachineCalendarEntry.machine_id == machine.id, MachineCalendarEntry.cal_date == day)
        .all()
    )
    work_ranges: list[TimeRange] = []
    maint_blocks: list[TimeRange] = []

    for e in cal_entries:
        if e.entry_kind == "maintenance":
            maint_blocks.extend(expand_shift_to_ranges(day, e.start_time, e.end_time))
        else:
            work_ranges.extend(expand_shift_to_ranges(day, e.start_time, e.end_time))

    exc_rows = (
        db.query(ResourceCalendarException)
        .filter(
            ResourceCalendarException.resource_type == "machine",
            ResourceCalendarException.resource_id == machine.id,
            ResourceCalendarException.cal_date == day,
        )
        .all()
    )
    for ex in exc_rows:
        if ex.exception_kind == "holiday" and ex.start_time is None:
            return 0.0
        if ex.start_time is not None and ex.end_time is not None:
            blk = expand_shift_to_ranges(day, ex.start_time, ex.end_time)
            if ex.exception_kind in ("maintenance", "closed", "holiday"):
                maint_blocks.extend(blk)

    if not work_ranges:
        work_ranges = _default_machine_intervals(wc, day, ov)

    open_ranges = _subtract_blocked(work_ranges, maint_blocks)
    return round(sum(r.duration_hours() for r in open_ranges), 6)


def night_shift_duration_hours(start: time, end: time) -> float:
    return sum(r.duration_hours() for r in expand_shift_to_ranges(date(2000, 1, 3), start, end))


def machine_work_intervals(
    db: Session,
    machine: Machine,
    day: date,
    ovl: Overrides | None = None,
) -> list[TimeRange]:
    """Makinenin calisabilir araliklari (bakim/tatil haric)."""
    wc = machine.work_center or db.get(WorkCenter, machine.work_center_id)
    if wc is None or not machine.is_active:
        return []
    ov = ovl.get(day) if ovl else None
    if work_center_holiday(db, wc.id, day):
        return []
    if not is_working_day(wc, day, ov):
        return []

    cal_entries = (
        db.query(MachineCalendarEntry)
        .filter(MachineCalendarEntry.machine_id == machine.id, MachineCalendarEntry.cal_date == day)
        .all()
    )
    work_ranges: list[TimeRange] = []
    maint_blocks: list[TimeRange] = []

    for e in cal_entries:
        if e.entry_kind == "maintenance":
            maint_blocks.extend(expand_shift_to_ranges(day, e.start_time, e.end_time))
        else:
            work_ranges.extend(expand_shift_to_ranges(day, e.start_time, e.end_time))

    exc_rows = (
        db.query(ResourceCalendarException)
        .filter(
            ResourceCalendarException.resource_type == "machine",
            ResourceCalendarException.resource_id == machine.id,
            ResourceCalendarException.cal_date == day,
        )
        .all()
    )
    for ex in exc_rows:
        if ex.exception_kind == "holiday" and ex.start_time is None:
            return []
        if ex.start_time is not None and ex.end_time is not None:
            blk = expand_shift_to_ranges(day, ex.start_time, ex.end_time)
            if ex.exception_kind in ("maintenance", "closed", "holiday"):
                maint_blocks.extend(blk)

    if not work_ranges:
        work_ranges = _default_machine_intervals(wc, day, ov)

    return _subtract_blocked(work_ranges, maint_blocks)


def work_center_crew_pool_size(wc: WorkCenter, day: date, ovl) -> int:
    """Esanlamli is gucu havuzu: gunun vardiyalarindaki toplam kisi."""
    from app.services import capacity

    total = 0
    for shift in effective_shifts(wc):
        if _shift_on_day(shift, day, ovl):
            hc = shift.headcount if shift.headcount and shift.headcount > 0 else capacity.daily_headcount(wc, day, 0, ovl)
            total += max(hc, 0)
    return max(total, 1)
