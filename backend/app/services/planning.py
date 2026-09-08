"""Otomatik / manuel planlama, haftalik yuk ve terminleme (lead time)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionBatch, ProductionBatchOrder, RoutingOperation, WorkCenter
from app.services import production_batches as pbatches
from app.services.orders import effective_due
from app.schemas import (
    AutoPlanRequest,
    ForecastFromLeadTimeIn,
    LeadTimeOut,
    LeadTimeRequest,
    LeadTimeStep,
    LoadDetailOut,
    LoadDetailParetoRow,
    LoadDetailRow,
    ManualPlanLineIn,
    PlanLineOut,
    WeekLoad,
    WorkCenterLoad,
)
from app.services import capacity as cap
from app.services import scenarios as scen
from app.services.gantt import actual_hours_by_week


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
    production_batch_id: int | None = None
    label: str = ""


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
    in_batch = pbatches.batched_order_ids(db)
    rows = (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
        .all()
    )
    return [o for o in rows if o.id not in in_batch]


def _place_quantity(
    anchor: Order,
    quantity: float,
    label: str,
    production_batch_id: int | None,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None = None,
) -> tuple[list[DraftLine], list[dict]]:
    """Siparis veya uretim partisi miktari icin operasyonlari yerlestirir."""
    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    prev_first_idx = 0
    prev_last_idx = 0
    prev_op: RoutingOperation | None = None
    for op in anchor.item.operations:
        if op.work_center_id not in wc_by_id:
            continue
        total_hours = op.hours_for(quantity)
        hours_left = total_hours
        if prev_op is None:
            idx = 0
        else:
            rule = rules.get(anchor.item, prev_op, op) if rules else scen.Rule()
            idx = prev_first_idx if rule.rule == "cycles" else prev_last_idx
        first_idx = None
        last_idx = None
        while hours_left > 1e-6 and idx < len(weeks):
            wk = weeks[idx]
            avail = remaining[(op.work_center_id, wk)]
            if avail > 1e-6:
                take = min(avail, hours_left)
                qty = quantity * (take / total_hours) if total_hours > 0 else 0
                lines.append(
                    DraftLine(
                        order=anchor,
                        order_id=anchor.id,
                        operation_id=op.id,
                        work_center_id=op.work_center_id,
                        week_start=wk,
                        planned_hours=round(take, 3),
                        planned_qty=round(qty, 2),
                        production_batch_id=production_batch_id,
                        label=label or anchor.order_no,
                    )
                )
                remaining[(op.work_center_id, wk)] = avail - take
                hours_left -= take
                if first_idx is None:
                    first_idx = idx
                last_idx = idx
            if hours_left > 1e-6:
                idx += 1
        if first_idx is not None:
            prev_first_idx = first_idx
            prev_last_idx = last_idx if last_idx is not None else first_idx
            prev_op = op
        if hours_left > 1e-6:
            unplanned.append(
                {
                    "order_no": label or anchor.order_no,
                    "item_code": anchor.item.code,
                    "operation_seq": op.seq,
                    "work_center_code": wc_by_id[op.work_center_id].code,
                    "hours": round(hours_left, 2),
                }
            )
    return lines, unplanned


def _place_order(o: Order, wc_by_id: dict[int, WorkCenter], weeks: list[date], remaining: dict[tuple[int, date], float], rules: scen.RuleLookup | None = None) -> tuple[list[DraftLine], list[dict]]:
    return _place_quantity(o, o.quantity, o.order_no, None, wc_by_id, weeks, remaining, rules)


def _order_hours(o: Order, wc_by_id: dict[int, WorkCenter]) -> float:
    return sum(op.hours_for(o.quantity) for op in o.item.operations if op.work_center_id in wc_by_id)


def _batch_hours(batch: ProductionBatch, wc_by_id: dict[int, WorkCenter]) -> float:
    if not batch.orders:
        return 0.0
    item = batch.item
    return sum(op.hours_for(batch.quantity) for op in item.operations if op.work_center_id in wc_by_id)


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
    batches = pbatches.open_batches_with_ops(db)
    all_orders = (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
        .all()
    )
    if not wcs:
        return Simulation(req.mode, start, weeks, [], [], [], [], 0.0, all_orders)
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
    rules = scen.RuleLookup(db)

    if req.mode == "revenue":
        def density(o: Order) -> float:
            h = _order_hours(o, wc_by_id)
            rev = o.quantity * (o.unit_price or 0.0)
            return rev / h if h > 0 else 0.0

        def batch_density(b: ProductionBatch) -> float:
            rev = sum(l.quantity * (l.order.unit_price or 0.0) for l in b.orders if l.order)
            h = _batch_hours(b, wc_by_id)
            return rev / h if h > 0 else 0.0

        ranked = sorted(orders, key=lambda o: (-density(o), effective_due(o), o.order_no))
        batch_ranked = sorted(batches, key=lambda b: (-batch_density(b), b.due_date, b.batch_no))
        leftover: list[Order] = []
        for batch in batch_ranked:
            if _batch_hours(batch, wc_by_id) <= 1e-6 or not batch.orders:
                continue
            anchor = sorted(batch.orders, key=lambda l: (effective_due(l.order) if l.order else date.max, l.order_id))[0].order
            if not anchor:
                continue
            trial = dict(remaining)
            ls, un = _place_quantity(anchor, batch.quantity, batch.batch_no, batch.id, wc_by_id, weeks, trial, rules)
            if un:
                continue
            remaining = trial
            lines.extend(ls)
        for o in ranked:
            if _order_hours(o, wc_by_id) <= 1e-6:
                continue  # bu is merkezlerinde operasyonu yok
            trial = dict(remaining)
            ls, un = _place_order(o, wc_by_id, weeks, trial, rules)
            if un:
                leftover.append(o)
                continue
            remaining = trial
            lines.extend(ls)
        # kalan kapasiteyi termin sirasiyla kismen doldur
        for o in sorted(leftover, key=lambda o: (effective_due(o), o.order_no)):
            ls, un = _place_order(o, wc_by_id, weeks, remaining, rules)
            lines.extend(ls)
            unplanned.extend(un)
            if not ls:
                skipped.append({"order_no": o.order_no, "item_code": o.item.code, "revenue": round(o.quantity * (o.unit_price or 0.0), 2), "hours": round(_order_hours(o, wc_by_id), 2)})
    else:
        for batch in batches:
            if not batch.orders:
                continue
            anchor = sorted(batch.orders, key=lambda l: (effective_due(l.order) if l.order else date.max, l.order.order_no if l.order else "", l.order_id))[0].order
            if not anchor:
                continue
            ls, un = _place_quantity(anchor, batch.quantity, batch.batch_no, batch.id, wc_by_id, weeks, remaining, rules)
            lines.extend(ls)
            unplanned.extend(un)
        for o in orders:
            ls, un = _place_order(o, wc_by_id, weeks, remaining, rules)
            lines.extend(ls)
            unplanned.extend(un)

    return Simulation(req.mode, start, weeks, wcs, lines, unplanned, skipped, capacity_total, all_orders)


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
                production_batch_id=l.production_batch_id,
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
        joinedload(PlanLine.production_batch).joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order),
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
        if pl.order is None or pl.operation is None or pl.work_center is None:
            continue  # bagli kayit silinmis (yetim plan satiri)
        batch_nos: list[str] = []
        batch_no = ""
        batch_id = pl.production_batch_id
        if pl.production_batch:
            batch_no = pl.production_batch.batch_no
            batch_nos = [
                f"{l.order.order_no}{f'/{l.order.position_no}' if l.order and l.order.position_no else ''}"
                for l in sorted(pl.production_batch.orders, key=lambda x: x.order_id)
                if l.order
            ]
        out.append(
            PlanLineOut(
                id=pl.id,
                order_id=pl.order_id,
                order_no=batch_no if batch_id else pl.order.order_no,
                position_no="" if batch_id else (pl.order.position_no or ""),
                production_batch_id=batch_id,
                batch_no=batch_no,
                batch_order_nos=batch_nos,
                customer=pl.order.customer,
                due_date=effective_due(pl.order) if pl.order else None,
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
    actual = actual_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1])
    result = []
    for w in wcs:
        rows = []
        unit = w.capacity_unit_hours or 1.0
        ovl = cap.Overrides(db, w.id)
        for wk in wk_list:
            c = cap.week_capacity_hours(db, w, wk)
            p = planned.get((w.id, wk), 0.0)
            a = actual.get((w.id, wk), 0.0)
            remaining = max(p - a, 0.0)
            idle = max(c - p, 0.0)
            n_days = len(cap.working_days(w, wk, wk + timedelta(days=6), ovl))
            daily_cap = c / n_days if n_days else 0.0
            rem_days = remaining / daily_cap if daily_cap > 0 else 0.0
            rows.append(
                WeekLoad(
                    week_start=wk,
                    capacity_hours=round(c, 2),
                    planned_hours=round(p, 2),
                    utilization=round(p / c, 3) if c > 0 else 0.0,
                    actual_hours=round(a, 2),
                    actual_utilization=round(a / c, 3) if c > 0 else 0.0,
                    remaining_hours=round(remaining, 2),
                    remaining_days=round(rem_days, 2),
                    idle_hours=round(idle, 2),
                    capacity_units=round(c / unit, 2),
                    planned_units=round(p / unit, 2),
                    actual_units=round(a / unit, 2),
                )
            )
        result.append(WorkCenterLoad(work_center_id=w.id, work_center_code=w.code, weeks=rows))
    return result


# ---------------- Terminleme (lead time) ----------------

def _daily_free_hours(db: Session, wc: WorkCenter, day: date, emp: int, planned_week: dict[date, float], wdays_cache: dict[date, int], ovl: cap.Overrides) -> float:
    total = cap.daily_capacity_hours(wc, day, emp, ovl.get(day))
    if total <= 0:
        return 0.0
    wk = cap.week_start(day)
    if wk not in wdays_cache:
        wdays_cache[wk] = len(cap.working_days(wc, wk, wk + timedelta(days=6), ovl)) or 1
    used = planned_week.get(wk, 0.0) / wdays_cache[wk]
    return max(total - used, 0.0)


def _schedule_op(db: Session, wc: WorkCenter, hours: float, earliest: datetime, planned_week: dict[date, float]) -> tuple[datetime, datetime]:
    """Bir operasyonu 'earliest' aninda baslayarak is merkezinin bos kapasitesine yayar; (baslangic, bitis) dondurur."""
    emp = cap.employee_count(db, wc)
    ovl = cap.Overrides(db, wc.id)
    wdays_cache: dict[date, int] = {}
    hours_left = hours
    cursor = earliest
    day = cursor.date()
    step_start: datetime | None = None
    step_end = cursor
    guard = 0
    while hours_left > 1e-6 and guard < 400:
        guard += 1
        ov = ovl.get(day)
        free = _daily_free_hours(db, wc, day, emp, planned_week, wdays_cache, ovl)
        if free > 1e-6:
            day_start = datetime.combine(day, cap.first_shift_start(wc, day, ov))
            hc = max(cap.daily_headcount(wc, day, emp, ov), 1)
            nominal_per_day = cap.daily_nominal_hours(wc, day, emp, ov) / hc
            if day == cursor.date() and cursor > day_start:
                # ayni gun icinde daha once biten operasyondan sonra basla
                frac_used = (cursor - day_start).total_seconds() / 3600.0
                free = max(free * (1 - min(frac_used / max(nominal_per_day, 1.0), 1.0)), 0.0)
                day_start = cursor
            if free > 1e-6:
                take = min(free, hours_left)
                daily_eff = cap.daily_capacity_hours(wc, day, emp, ov)
                # verimli saatleri nominal mesai saatine oranla yay
                elapsed_nominal = (take / daily_eff) * nominal_per_day if daily_eff > 0 else take
                if step_start is None:
                    step_start = day_start
                step_end = day_start + timedelta(hours=elapsed_nominal)
                hours_left -= take
                wk = cap.week_start(day)
                planned_week[wk] = planned_week.get(wk, 0.0) + take
        if hours_left > 1e-6:
            day += timedelta(days=1)
            cursor = datetime.combine(day, cap.first_shift_start(wc, day, ovl.get(day)))
    if step_start is None:
        step_start = cursor
    return step_start, max(step_end, step_start)


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

    rules = scen.RuleLookup(db)
    steps: list[LeadTimeStep] = []
    first_wc = ops[0].work_center
    cursor = datetime.combine(req.start, cap.first_shift_start(first_wc, req.start, cap.Overrides(db, first_wc.id).get(req.start)))
    overall_start: datetime | None = None
    overall_end: datetime | None = None
    prev_op: RoutingOperation | None = None
    prev_start: datetime | None = None
    prev_end: datetime | None = None
    for op in ops:
        wc = op.work_center
        hours = op.hours_for(req.quantity)
        # --- senaryo matrisi: bu operasyon en erken ne zaman baslayabilir? ---
        rule_desc = ""
        if prev_op is not None and prev_start is not None and prev_end is not None:
            rule = rules.get(item, prev_op, op)
            if rule.rule == "cycles":
                frac = min(rule.lag_cycles / req.quantity, 1.0) if req.quantity > 0 else 1.0
                earliest = prev_start + (prev_end - prev_start) * frac
            else:
                earliest = prev_end
            earliest += timedelta(minutes=rule.wait_minutes)
            rule_desc = rule.describe() + ("" if rule.source == "default" else f" ({'stok' if rule.source == 'item' else 'grup'} kuralı)")
        else:
            earliest = cursor
        step_start, step_end = _schedule_op(db, wc, hours, earliest, planned_by_wc[wc.id])
        # ic ice calisan operasyon, oncekinden once bitemez (son parca oncekinden sonra gelir)
        if prev_end is not None and step_end < prev_end:
            step_end = prev_end + timedelta(seconds=op.cycle_time_sec or 0)
        steps.append(
            LeadTimeStep(
                operation_seq=op.seq,
                operation_name=op.operation_name,
                work_center_code=wc.code,
                hours=round(hours, 2),
                start=step_start.strftime("%Y-%m-%d %H:%M"),
                end=step_end.strftime("%Y-%m-%d %H:%M"),
                start_rule=rule_desc,
            )
        )
        overall_start = overall_start or step_start
        overall_end = step_end if overall_end is None or step_end > overall_end else overall_end
        prev_op, prev_start, prev_end = op, step_start, step_end

    return LeadTimeOut(
        item_code=item.code,
        quantity=req.quantity,
        total_hours=round(sum(s.hours for s in steps), 2),
        start=(overall_start or cursor).strftime("%Y-%m-%d %H:%M"),
        end=(overall_end or cursor).strftime("%Y-%m-%d %H:%M"),
        steps=steps,
    )


def load_detail(db: Session, work_center_id: int, week_start: date) -> LoadDetailOut:
    from app.services.gantt import _line_window_in_week

    wc = db.get(WorkCenter, work_center_id)
    if not wc:
        raise ValueError("Is merkezi bulunamadi")
    wk = cap.week_start(week_start)
    pls = (
        db.query(PlanLine)
        .options(
            joinedload(PlanLine.order).joinedload(Order.item),
            joinedload(PlanLine.production_batch).joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order),
            joinedload(PlanLine.operation),
        )
        .filter(PlanLine.work_center_id == work_center_id, PlanLine.week_start == wk)
        .all()
    )
    rows: list[LoadDetailRow] = []
    for pl in sorted(pls, key=lambda p: (effective_due(p.order), p.order.order_no, p.order_id, p.operation.seq, p.id)):
        if pl.order is None or pl.operation is None:
            continue
        ps, pe = _line_window_in_week(db, wc, wk, pl.id, pls)
        op = pl.operation
        item = pl.order.item
        batch_no = ""
        batch_nos: list[str] = []
        order_no = pl.order.order_no
        position_no = pl.order.position_no or ""
        customer = pl.order.customer
        qty = pl.planned_qty if pl.planned_qty > 0 else pl.order.quantity
        if pl.production_batch:
            batch_no = pl.production_batch.batch_no
            order_no = batch_no
            position_no = ""
            batch_nos = [
                f"{l.order.order_no}{f'/{l.order.position_no}' if l.order and l.order.position_no else ''}"
                for l in sorted(pl.production_batch.orders, key=lambda x: x.order_id)
                if l.order
            ]
            customers = sorted({l.order.customer for l in pl.production_batch.orders if l.order and l.order.customer})
            customer = ", ".join(customers) if customers else "parti"
            qty = pl.planned_qty if pl.planned_qty > 0 else pl.production_batch.quantity
        rows.append(
            LoadDetailRow(
                plan_line_id=pl.id,
                item_code=item.code if item else "",
                item_name=item.name if item else "",
                semi_finished_code=(op.semi_finished_code or "").strip(),
                operation_name=op.operation_name or "",
                order_no=order_no,
                position_no=position_no,
                customer=customer,
                batch_no=batch_no,
                batch_order_nos=batch_nos,
                operation_seq=op.seq,
                planned_hours=round(pl.planned_hours, 3),
                planned_qty=round(qty, 2),
                planned_start=ps,
                planned_end=pe,
                mode=pl.mode,
            )
        )
    total_hours = round(sum(r.planned_hours for r in rows), 2)
    total_qty = round(sum(r.planned_qty for r in rows), 2)
    by_item: dict[str, float] = defaultdict(float)
    for r in rows:
        by_item[r.item_code] += r.planned_hours
    pareto: list[LoadDetailParetoRow] = []
    cum = 0.0
    for code, h in sorted(by_item.items(), key=lambda x: -x[1]):
        cum += h
        pareto.append(
            LoadDetailParetoRow(
                item_code=code,
                hours=round(h, 2),
                pct=round(h / total_hours * 100, 1) if total_hours > 0 else 0.0,
                cum_pct=round(cum / total_hours * 100, 1) if total_hours > 0 else 0.0,
            )
        )
    return LoadDetailOut(
        work_center_id=wc.id,
        work_center_code=wc.code,
        week_start=wk,
        total_hours=total_hours,
        total_qty=total_qty,
        rows=rows,
        pareto=pareto,
    )


def add_forecast_from_leadtime(db: Session, req: ForecastFromLeadTimeIn, username: str) -> int:
    """Terminleme sonucunu tahmin plan satirlari olarak kaydeder; sonraki terminlemelerde doluluk hesaba katilir."""
    item = (
        db.query(Item)
        .options(joinedload(Item.operations))
        .filter(Item.code == req.item_code)
        .first()
    )
    if not item:
        raise ValueError("Stok kodu bulunamadi")
    if not req.steps:
        raise ValueError("Operasyon adimi yok")
    end_dt = datetime.strptime(req.steps[-1].end[:10], "%Y-%m-%d").date()
    label = (req.label or "").strip() or f"TAH-{item.code}-{datetime.now():%m%d%H%M}"
    order = Order(
        order_no=label,
        customer="Tahmin",
        due_date=end_dt,
        item_id=item.id,
        quantity=req.quantity,
        status="forecast",
        note="Yeni is terminleme tahmini",
    )
    db.add(order)
    db.flush()
    created = 0
    for step in req.steps:
        op = next((o for o in item.operations if o.seq == step.operation_seq), None)
        if not op:
            continue
        start_day = datetime.strptime(step.start[:10], "%Y-%m-%d").date()
        db.add(
            PlanLine(
                order_id=order.id,
                operation_id=op.id,
                work_center_id=op.work_center_id,
                week_start=cap.week_start(start_day),
                planned_hours=round(step.hours, 3),
                planned_qty=round(req.quantity, 2),
                mode="forecast",
                created_by=username,
            )
        )
        created += 1
    db.commit()
    return created


def clear_forecast_plans(db: Session, work_center_ids: list[int] | None = None) -> int:
    q = db.query(PlanLine).filter(PlanLine.mode == "forecast")
    if work_center_ids:
        q = q.filter(PlanLine.work_center_id.in_(work_center_ids))
    forecast_order_ids = {pl.order_id for pl in q.all()}
    n = q.delete(synchronize_session=False)
    if forecast_order_ids:
        db.query(Order).filter(Order.id.in_(forecast_order_ids), Order.status == "forecast").delete(synchronize_session=False)
    db.commit()
    return n
