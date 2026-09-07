"""Otomatik / manuel planlama, haftalik yuk ve terminleme (lead time)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, RoutingOperation, WorkCenter
from app.schemas import (
    AutoPlanRequest,
    LeadTimeOut,
    LeadTimeRequest,
    LeadTimeStep,
    ManualPlanLineIn,
    PlanLineOut,
    WeekLoad,
    WorkCenterLoad,
)
from app.services import capacity as cap


def _selected_work_centers(db: Session, ids: list[int] | None) -> list[WorkCenter]:
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if ids:
        q = q.filter(WorkCenter.id.in_(ids))
    else:
        q = q.filter(WorkCenter.is_planned.is_(True))
    return q.order_by(WorkCenter.code).all()


def planned_hours_by_week(db: Session, wc_ids: list[int], start: date, end: date, mode: str | None = None) -> dict[tuple[int, date], float]:
    q = (
        db.query(PlanLine.work_center_id, PlanLine.week_start, func.sum(PlanLine.planned_hours))
        .filter(PlanLine.work_center_id.in_(wc_ids), PlanLine.week_start >= start, PlanLine.week_start <= end)
    )
    if mode:
        q = q.filter(PlanLine.mode == mode)
    q = q.group_by(PlanLine.work_center_id, PlanLine.week_start)
    return {(wc, wk): float(h or 0) for wc, wk, h in q.all()}


@dataclass
class DraftLine:
    """Kalici olmayan (simulasyon) plan satiri; PlanLine ile ayni alan adlari."""

    order: Order
    order_id: int
    operation_id: int
    work_center_id: int
    week_start: date
    planned_hours: float
    planned_qty: float
    mode: str = "auto"


@dataclass
class Simulation:
    mode: str
    start: date
    weeks: list[date]
    work_centers: list[WorkCenter]
    lines: list[DraftLine]
    unplanned: list[dict]  # ufka sigmayan operasyon kalanlari
    skipped: list[dict]  # ciro modunda tamamen disarida birakilan siparisler
    capacity_hours: float  # ufuk icindeki toplam (manuel dusulmus) kapasite
    orders: list[Order]

    @property
    def planned_hours(self) -> float:
        return sum(l.planned_hours for l in self.lines)


def _open_orders_with_ops(db: Session) -> list[Order]:
    return (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations))
        .filter(Order.status == "open")
        .order_by(Order.due_date, Order.order_no, Order.id)
        .all()
    )


def _place_order(o: Order, wc_by_id: dict[int, WorkCenter], weeks: list[date], remaining: dict[tuple[int, date], float]) -> tuple[list[DraftLine], list[dict]]:
    """Bir siparisin operasyonlarini kalan kapasiteye yerlestirir (remaining'i gunceller).
    Sonraki operasyon, oncekinin basladigi haftadan once baslayamaz."""
    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    prev_week_idx = 0
    for op in o.item.operations:
        if op.work_center_id not in wc_by_id:
            continue
        total_hours = op.hours_for(o.quantity)
        hours_left = total_hours
        idx = prev_week_idx
        first_idx = None
        while hours_left > 1e-6 and idx < len(weeks):
            wk = weeks[idx]
            avail = remaining[(op.work_center_id, wk)]
            if avail > 1e-6:
                take = min(avail, hours_left)
                qty = o.quantity * (take / total_hours) if total_hours > 0 else 0
                lines.append(DraftLine(order=o, order_id=o.id, operation_id=op.id, work_center_id=op.work_center_id, week_start=wk, planned_hours=round(take, 3), planned_qty=round(qty, 2)))
                remaining[(op.work_center_id, wk)] = avail - take
                hours_left -= take
                if first_idx is None:
                    first_idx = idx
            if hours_left > 1e-6:
                idx += 1
        if first_idx is not None:
            prev_week_idx = first_idx
        if hours_left > 1e-6:
            unplanned.append({"order_no": o.order_no, "item_code": o.item.code, "operation_seq": op.seq, "work_center_code": wc_by_id[op.work_center_id].code, "hours": round(hours_left, 2)})
    return lines, unplanned


def _order_hours(o: Order, wc_by_id: dict[int, WorkCenter]) -> float:
    return sum(op.hours_for(o.quantity) for op in o.item.operations if op.work_center_id in wc_by_id)


def simulate(db: Session, req: AutoPlanRequest) -> Simulation:
    """Otomatik plani hesaplar, veritabanina yazmaz.

    due_date: siparisler termin sirasiyla yerlestirilir; sigmayan kisim 'unplanned'.
    revenue : ufuk icinde maksimum ciro hedeflenir. Siparisler saat basina ciroya
              (ciro / gereken saat) gore siralanir; yalnizca ufka TAMAMEN sigan siparisler
              alinir (ciro teslimde gerceklesir), sigmayanlar atlanir. Kalan kapasite,
              atlanan siparislerle termin sirasiyla kismen doldurulur (sonraki ufka devreder).
    """
    start = cap.week_start(req.start_week)
    weeks = [start + timedelta(weeks=i) for i in range(req.weeks)]
    wcs = _selected_work_centers(db, req.work_center_ids)
    orders = _open_orders_with_ops(db)
    if not wcs:
        return Simulation(req.mode, start, weeks, [], [], [], [], 0.0, orders)
    wc_ids = [w.id for w in wcs]
    wc_by_id = {w.id: w for w in wcs}

    # kalan kapasite = kapasite - manuel yerlestirilmis saatler
    manual = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="manual")
    remaining: dict[tuple[int, date], float] = {}
    for w in wcs:
        for wk in weeks:
            remaining[(w.id, wk)] = max(cap.week_capacity_hours(db, w, wk) - manual.get((w.id, wk), 0.0), 0.0)
    capacity_total = sum(remaining.values())

    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    skipped: list[dict] = []

    if req.mode == "revenue":
        def density(o: Order) -> float:
            h = _order_hours(o, wc_by_id)
            rev = o.quantity * (o.unit_price or 0.0)
            return rev / h if h > 0 else 0.0

        ranked = sorted(orders, key=lambda o: (-density(o), o.due_date, o.order_no))
        leftover: list[Order] = []
        for o in ranked:
            if _order_hours(o, wc_by_id) <= 1e-6:
                continue  # bu is merkezlerinde operasyonu yok
            trial = dict(remaining)
            ls, un = _place_order(o, wc_by_id, weeks, trial)
            if un:
                leftover.append(o)
                continue
            remaining = trial
            lines.extend(ls)
        # kalan kapasiteyi termin sirasiyla kismen doldur
        for o in sorted(leftover, key=lambda o: (o.due_date, o.order_no)):
            ls, un = _place_order(o, wc_by_id, weeks, remaining)
            lines.extend(ls)
            unplanned.extend(un)
            if not ls:
                skipped.append({"order_no": o.order_no, "item_code": o.item.code, "revenue": round(o.quantity * (o.unit_price or 0.0), 2), "hours": round(_order_hours(o, wc_by_id), 2)})
    else:
        for o in orders:
            ls, un = _place_order(o, wc_by_id, weeks, remaining)
            lines.extend(ls)
            unplanned.extend(un)

    return Simulation(req.mode, start, weeks, wcs, lines, unplanned, skipped, capacity_total, orders)


def auto_plan(db: Session, req: AutoPlanRequest, username: str) -> dict:
    sim = simulate(db, req)
    if not sim.work_centers:
        return {"created": 0, "unplanned": [], "skipped": [], "mode": req.mode, "message": "Planlanacak is merkezi secilmedi (is merkezinde 'Planlaniyor' isaretli olmali)."}
    wc_ids = [w.id for w in sim.work_centers]
    if req.replace_existing:
        db.query(PlanLine).filter(
            PlanLine.work_center_id.in_(wc_ids), PlanLine.week_start >= sim.start, PlanLine.mode == "auto"
        ).delete(synchronize_session=False)
        db.flush()
    for l in sim.lines:
        db.add(
            PlanLine(
                order_id=l.order_id,
                operation_id=l.operation_id,
                work_center_id=l.work_center_id,
                week_start=l.week_start,
                planned_hours=l.planned_hours,
                planned_qty=l.planned_qty,
                mode="auto",
                strategy=req.mode,
                created_by=username,
            )
        )
    db.commit()
    label = "maksimum ciro" if req.mode == "revenue" else "termine gore"
    return {"created": len(sim.lines), "unplanned": sim.unplanned, "skipped": sim.skipped, "mode": req.mode, "message": f"{len(sim.lines)} plan satiri olusturuldu ({label})."}


def add_manual_line(db: Session, line: ManualPlanLineIn, username: str) -> PlanLine:
    order = db.get(Order, line.order_id)
    op = db.get(RoutingOperation, line.operation_id)
    if not order or not op:
        raise ValueError("Siparis veya operasyon bulunamadi")
    total = op.hours_for(order.quantity)
    hours = line.planned_hours if line.planned_hours is not None else total
    qty = line.planned_qty if line.planned_qty is not None else (order.quantity * hours / total if total > 0 else 0)
    pl = PlanLine(
        order_id=order.id,
        operation_id=op.id,
        work_center_id=op.work_center_id,
        week_start=cap.week_start(line.week_start),
        planned_hours=round(hours, 3),
        planned_qty=round(qty, 2),
        mode="manual",
        created_by=username,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return pl


def plan_lines(db: Session, wc_ids: list[int] | None, start: date | None, end: date | None) -> list[PlanLineOut]:
    q = db.query(PlanLine).options(
        joinedload(PlanLine.order).joinedload(Order.item),
        joinedload(PlanLine.operation),
        joinedload(PlanLine.work_center),
    )
    if wc_ids:
        q = q.filter(PlanLine.work_center_id.in_(wc_ids))
    if start:
        q = q.filter(PlanLine.week_start >= cap.week_start(start))
    if end:
        q = q.filter(PlanLine.week_start <= end)
    out = []
    for pl in q.order_by(PlanLine.week_start, PlanLine.work_center_id, PlanLine.id).all():
        out.append(
            PlanLineOut(
                id=pl.id,
                order_id=pl.order_id,
                order_no=pl.order.order_no,
                customer=pl.order.customer,
                due_date=pl.order.due_date,
                item_code=pl.order.item.code,
                operation_id=pl.operation_id,
                operation_seq=pl.operation.seq,
                work_center_id=pl.work_center_id,
                work_center_code=pl.work_center.code,
                week_start=pl.week_start,
                planned_hours=pl.planned_hours,
                planned_qty=pl.planned_qty,
                mode=pl.mode,
                strategy=pl.strategy or "",
            )
        )
    return out


def load(db: Session, wc_ids: list[int] | None, start: date, weeks: int) -> list[WorkCenterLoad]:
    start = cap.week_start(start)
    wcs = _selected_work_centers(db, wc_ids) if wc_ids else db.query(WorkCenter).filter(WorkCenter.is_active.is_(True)).order_by(WorkCenter.code).all()
    if not wcs:
        return []
    wk_list = [start + timedelta(weeks=i) for i in range(weeks)]
    planned = planned_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1])
    result = []
    for w in wcs:
        rows = []
        unit = w.capacity_unit_hours or 1.0
        for wk in wk_list:
            c = cap.week_capacity_hours(db, w, wk)
            p = planned.get((w.id, wk), 0.0)
            rows.append(
                WeekLoad(
                    week_start=wk,
                    capacity_hours=round(c, 2),
                    planned_hours=round(p, 2),
                    utilization=round(p / c, 3) if c > 0 else 0.0,
                    capacity_units=round(c / unit, 2),
                    planned_units=round(p / unit, 2),
                )
            )
        result.append(WorkCenterLoad(work_center_id=w.id, work_center_code=w.code, weeks=rows))
    return result


# ---------------- Terminleme (lead time) ----------------

def _daily_free_hours(db: Session, wc: WorkCenter, day: date, emp: int, planned_week: dict[date, float], wdays_cache: dict[date, int]) -> float:
    total = cap.daily_capacity_hours(wc, day, emp)
    if total <= 0:
        return 0.0
    wk = cap.week_start(day)
    if wk not in wdays_cache:
        wdays_cache[wk] = len(cap.working_days(wc, wk, wk + timedelta(days=6))) or 1
    used = planned_week.get(wk, 0.0) / wdays_cache[wk]
    return max(total - used, 0.0)


def lead_time(db: Session, req: LeadTimeRequest) -> LeadTimeOut:
    item = (
        db.query(Item)
        .options(joinedload(Item.operations).joinedload(RoutingOperation.work_center))
        .filter(Item.code == req.item_code)
        .first()
    )
    if not item:
        raise ValueError("Stok kodu bulunamadi")
    ops = [
        op
        for op in item.operations
        if (not req.work_center_ids or op.work_center_id in req.work_center_ids)
        and (not req.operation_seqs or op.seq in req.operation_seqs)
    ]
    if not ops:
        raise ValueError("Secime uyan operasyon yok")

    horizon_end = req.start + timedelta(days=365)
    wc_ids = list({op.work_center_id for op in ops})
    planned_all = planned_hours_by_week(db, wc_ids, cap.week_start(req.start), horizon_end)
    planned_by_wc: dict[int, dict[date, float]] = defaultdict(dict)
    for (wc_id, wk), h in planned_all.items():
        planned_by_wc[wc_id][wk] = h

    steps: list[LeadTimeStep] = []
    cursor = datetime.combine(req.start, cap.first_shift_start(ops[0].work_center, req.start))
    overall_start = None
    for op in ops:
        wc = op.work_center
        emp = cap.employee_count(db, wc.id)
        hours_left = op.hours_for(req.quantity)
        wdays_cache: dict[date, int] = {}
        day = cursor.date()
        step_start = None
        step_end = cursor
        guard = 0
        while hours_left > 1e-6 and guard < 400:
            guard += 1
            free = _daily_free_hours(db, wc, day, emp, planned_by_wc[wc.id], wdays_cache)
            if free > 1e-6:
                day_start = datetime.combine(day, cap.first_shift_start(wc, day))
                if day == cursor.date() and cursor > day_start:
                    # ayni gun icinde daha once biten operasyondan sonra basla
                    frac_used = (cursor - day_start).total_seconds() / 3600.0
                    nominal = max(cap.daily_nominal_hours(wc, day, emp) / max(cap.shift_headcount(cap.effective_shifts(wc)[0], emp), 1), 1.0)
                    free = max(free * (1 - min(frac_used / nominal, 1.0)), 0.0)
                    day_start = cursor
                if free > 1e-6:
                    take = min(free, hours_left)
                    daily_eff = cap.daily_capacity_hours(wc, day, emp)
                    nominal_per_day = cap.daily_nominal_hours(wc, day, emp) / max(cap.shift_headcount(cap.effective_shifts(wc)[0], emp), 1)
                    # verimli saatleri nominal mesai saatine oranla yay
                    elapsed_nominal = (take / daily_eff) * nominal_per_day if daily_eff > 0 else take
                    if step_start is None:
                        step_start = day_start
                    step_end = day_start + timedelta(hours=elapsed_nominal)
                    hours_left -= take
            if hours_left > 1e-6:
                day += timedelta(days=1)
                cursor = datetime.combine(day, cap.first_shift_start(wc, day))
        if step_start is None:
            step_start = cursor
        steps.append(
            LeadTimeStep(
                operation_seq=op.seq,
                operation_name=op.operation_name,
                work_center_code=wc.code,
                hours=round(op.hours_for(req.quantity), 2),
                start=step_start.strftime("%Y-%m-%d %H:%M"),
                end=step_end.strftime("%Y-%m-%d %H:%M"),
            )
        )
        overall_start = overall_start or step_start
        cursor = step_end

    return LeadTimeOut(
        item_code=item.code,
        quantity=req.quantity,
        total_hours=round(sum(s.hours for s in steps), 2),
        start=(overall_start or cursor).strftime("%Y-%m-%d %H:%M"),
        end=cursor.strftime("%Y-%m-%d %H:%M"),
        steps=steps,
    )
