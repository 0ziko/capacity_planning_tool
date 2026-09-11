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
    ForecastSummaryOut,
    ForecastLoadDetail,
    ManualPlanLineIn,
    PlanLineOut,
    WeekLoad,
    WeeklyOutputOut,
    WeeklyOutputWc,
    WorkCenterLoad,
)
from app.services import capacity as cap
from app.services import scenarios as scen
from app.services.bom_tree import explode_order, has_wip_structure
from app.services.gantt import actual_hours_by_week
from app.services.plan_draft import DraftLine


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
    co_shipment_results: list[dict] | None = None
    co_shipment_exceptions: list[dict] | None = None

    @property
    def planned_hours(self) -> float:
        return sum(l.planned_hours for l in self.lines)


def _open_orders_with_ops(db: Session) -> list[Order]:
    in_batch = pbatches.batched_order_ids(db)
    rows = (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations), joinedload(Order.item).joinedload(Item.bom_lines))
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
    *,
    item: Item | None = None,
    semi_finished_code: str = "",
    min_start_idx: int = 0,
    ops: list[RoutingOperation] | None = None,
    qty_by_op: dict[int, float] | None = None,
) -> tuple[list[DraftLine], list[dict], int]:
    """Siparis veya uretim partisi miktari icin operasyonlari yerlestirir. Son hafta indeksini dondurur."""
    route_item = item or anchor.item
    route_ops = list(ops) if ops is not None else list(route_item.operations or [])
    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    prev_first_idx = min_start_idx
    prev_last_idx = min_start_idx
    prev_op: RoutingOperation | None = None
    last_idx = min_start_idx
    for op in route_ops:
        if op.work_center_id not in wc_by_id:
            continue
        op_qty = qty_by_op.get(op.id, quantity) if qty_by_op is not None else quantity
        total_hours = op.hours_for(op_qty)
        hours_left = total_hours
        if prev_op is None:
            idx = min_start_idx
        else:
            rule = rules.get(route_item, prev_op, op) if rules else scen.Rule()
            idx = prev_first_idx if rule.rule == "cycles" else prev_last_idx
        first_idx = None
        op_last_idx = None
        while hours_left > 1e-6 and idx < len(weeks):
            wk = weeks[idx]
            avail = remaining[(op.work_center_id, wk)]
            if avail > 1e-6:
                take = min(avail, hours_left)
                qty = op_qty * (take / total_hours) if total_hours > 0 else 0
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
                        semi_finished_code=semi_finished_code or op.semi_finished_code or "",
                    )
                )
                remaining[(op.work_center_id, wk)] = avail - take
                hours_left -= take
                if first_idx is None:
                    first_idx = idx
                op_last_idx = idx
                last_idx = max(last_idx, idx)
            if hours_left > 1e-6:
                idx += 1
        if first_idx is not None:
            prev_first_idx = first_idx
            prev_last_idx = op_last_idx if op_last_idx is not None else first_idx
            prev_op = op
        if hours_left > 1e-6:
            unplanned.append(
                {
                    "order_no": label or anchor.order_no,
                    "item_code": route_item.code,
                    "semi_finished_code": semi_finished_code,
                    "operation_seq": op.seq,
                    "work_center_code": wc_by_id[op.work_center_id].code,
                    "hours": round(hours_left, 2),
                }
            )
    return lines, unplanned, last_idx


def _place_order(
    db: Session,
    o: Order,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None = None,
) -> tuple[list[DraftLine], list[dict]]:
    if has_wip_structure(o):
        jobs = explode_order(db, o)
        all_lines: list[DraftLine] = []
        all_unplanned: list[dict] = []
        wip_end = 0
        for job in jobs.wip_jobs:
            lbl = f"{o.order_no}/{job.label_suffix}" if job.label_suffix else o.order_no
            ls, un, end_idx = _place_quantity(
                o,
                job.quantity,
                lbl,
                None,
                wc_by_id,
                weeks,
                remaining,
                rules,
                item=job.item,
                semi_finished_code=job.semi_finished_code,
                min_start_idx=0,
            )
            all_lines.extend(ls)
            all_unplanned.extend(un)
            wip_end = max(wip_end, end_idx)
        if jobs.finish_job:
            ls, un, _ = _place_quantity(
                o,
                jobs.finish_job.quantity,
                o.order_no,
                None,
                wc_by_id,
                weeks,
                remaining,
                rules,
                item=jobs.finish_job.item,
                semi_finished_code="",
                min_start_idx=wip_end,
            )
            all_lines.extend(ls)
            all_unplanned.extend(un)
        return all_lines, all_unplanned
    ls, un, _ = _place_quantity(o, o.quantity, o.order_no, None, wc_by_id, weeks, remaining, rules)
    return ls, un


def _order_hours(db: Session, o: Order, wc_by_id: dict[int, WorkCenter]) -> float:
    if has_wip_structure(o):
        jobs = explode_order(db, o)
        total = 0.0
        for job in jobs.wip_jobs:
            total += sum(op.hours_for(job.quantity) for op in job.item.operations if op.work_center_id in wc_by_id)
        if jobs.finish_job:
            total += sum(op.hours_for(jobs.finish_job.quantity) for op in jobs.finish_job.item.operations if op.work_center_id in wc_by_id)
        return total
    return sum(op.hours_for(o.quantity) for op in o.item.operations if op.work_center_id in wc_by_id)


def _batch_hours(batch: ProductionBatch, wc_by_id: dict[int, WorkCenter]) -> float:
    if not batch.orders:
        return 0.0
    item = batch.item
    return sum(op.hours_for(batch.quantity) for op in item.operations if op.work_center_id in wc_by_id)


def simulate(db: Session, req: AutoPlanRequest, extra_batches: list | None = None) -> Simulation:
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
    extra_batches = extra_batches or []
    extra_order_ids = {link.order_id for b in extra_batches for link in b.orders}
    orders = [o for o in _open_orders_with_ops(db) if o.id not in extra_order_ids]
    batches = list(pbatches.open_batches_with_ops(db)) + list(extra_batches)
    all_orders = (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations), joinedload(Order.item).joinedload(Item.bom_lines))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
        .all()
    )
    if not wcs:
        return Simulation(req.mode, start, weeks, [], [], [], [], 0.0, all_orders, [], [])

    wc_ids = [w.id for w in wcs]
    wc_by_id = {w.id: w for w in wcs}

    # kalan kapasite = planlanabilir kapasite - mevcut plan (manuel + tahmin + otomatik)
    manual = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="manual")
    forecast = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="forecast")
    auto = planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="auto")
    remaining: dict[tuple[int, date], float] = {}
    for w in wcs:
        for wk in weeks:
            plan_cap = cap.planning_capacity_hours(db, w, wk)
            used = manual.get((w.id, wk), 0.0) + forecast.get((w.id, wk), 0.0)
            if not req.replace_existing:
                used += auto.get((w.id, wk), 0.0)
            remaining[(w.id, wk)] = max(plan_cap - used, 0.0)
    capacity_total = sum(remaining.values())

    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    skipped: list[dict] = []
    rules = scen.RuleLookup(db)
    co_results: list[dict] = []
    co_exceptions: list[dict] = []
    co_handled: set[int] = set()

    cs_opts = req.co_shipment
    if cs_opts and cs_opts.enabled and cs_opts.selections and req.mode == "due_date":
        from app.services import co_shipment as co_ship

        co_out = co_ship.apply_co_shipment(db, cs_opts, weeks, start, wc_by_id, remaining, rules)
        lines.extend(co_out.lines)
        co_results = co_out.results
        co_exceptions = co_out.exceptions
        co_handled = co_out.handled_order_ids
        orders = [o for o in orders if o.id not in co_handled]

    if req.mode == "revenue":
        def density(o: Order) -> float:
            h = _order_hours(db, o, wc_by_id)
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
            ls, un, _ = _place_quantity(anchor, batch.quantity, batch.batch_no, batch.id, wc_by_id, weeks, trial, rules)
            if un:
                continue
            remaining = trial
            lines.extend(ls)
        for o in ranked:
            if _order_hours(db, o, wc_by_id) <= 1e-6:
                continue  # bu is merkezlerinde operasyonu yok
            trial = dict(remaining)
            ls, un = _place_order(db, o, wc_by_id, weeks, trial, rules)
            if un:
                leftover.append(o)
                continue
            remaining = trial
            lines.extend(ls)
        # kalan kapasiteyi termin sirasiyla kismen doldur
        for o in sorted(leftover, key=lambda o: (effective_due(o), o.order_no)):
            ls, un = _place_order(db, o, wc_by_id, weeks, remaining, rules)
            lines.extend(ls)
            unplanned.extend(un)
            if not ls:
                skipped.append({"order_no": o.order_no, "item_code": o.item.code, "revenue": round(o.quantity * (o.unit_price or 0.0), 2), "hours": round(_order_hours(db, o, wc_by_id), 2)})
    else:
        for batch in batches:
            if not batch.orders:
                continue
            anchor = sorted(batch.orders, key=lambda l: (effective_due(l.order) if l.order else date.max, l.order.order_no if l.order else "", l.order_id))[0].order
            if not anchor:
                continue
            ls, un, _ = _place_quantity(anchor, batch.quantity, batch.batch_no, batch.id, wc_by_id, weeks, remaining, rules)
            lines.extend(ls)
            unplanned.extend(un)
        for o in orders:
            ls, un = _place_order(db, o, wc_by_id, weeks, remaining, rules)
            lines.extend(ls)
            unplanned.extend(un)

    return Simulation(
        req.mode, start, weeks, wcs, lines, unplanned, skipped, capacity_total, all_orders, co_results, co_exceptions
    )


def write_simulation(
    db: Session,
    req: AutoPlanRequest,
    sim: Simulation,
    username: str,
    *,
    revision_id: int | None = None,
    commit: bool = True,
    replace_manual: bool = False,
    message_tag: str | None = None,
    keep_line_mode: bool = False,
) -> dict:
    """Simulasyon sonucunu plan satiri olarak yazar."""
    if not sim.work_centers:
        return {
            "created": 0,
            "unplanned": [],
            "skipped": [],
            "mode": req.mode,
            "message": "Planlanacak is merkezi secilmedi (is merkezinde 'Planlaniyor' isaretli olmali).",
            "co_shipment_results": [],
            "co_shipment_exceptions": [],
        }
    wc_ids = [w.id for w in sim.work_centers]
    if req.replace_existing:
        modes = ["auto"]
        if replace_manual:
            modes.append("manual")
        db.query(PlanLine).filter(
            PlanLine.work_center_id.in_(wc_ids),
            PlanLine.week_start >= sim.start,
            PlanLine.mode.in_(modes),
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
                semi_finished_code=l.semi_finished_code or "",
                mode=l.mode if keep_line_mode and l.mode in ("auto", "manual") else "auto",
                strategy=req.mode,
                revision_id=revision_id,
                created_by=username,
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()
    label = "maksimum ciro" if req.mode == "revenue" else "termine gore"
    tag = message_tag if message_tag is not None else ("revizyon" if revision_id else "revizyonsuz")
    msg = f"{len(sim.lines)} plan satiri olusturuldu ({label}, {tag})."
    if sim.co_shipment_results:
        msg += f" Birlikte sevk modu: {len(sim.co_shipment_results)} siparis grubu."
    return {
        "created": len(sim.lines),
        "unplanned": sim.unplanned,
        "skipped": sim.skipped,
        "mode": req.mode,
        "message": msg,
        "co_shipment_results": sim.co_shipment_results or [],
        "co_shipment_exceptions": sim.co_shipment_exceptions or [],
    }


def auto_plan(
    db: Session,
    req: AutoPlanRequest,
    username: str,
    *,
    revision_id: int | None = None,
    commit: bool = True,
    replace_manual: bool = False,
) -> dict:
    sim = simulate(db, req)
    return write_simulation(db, req, sim, username, revision_id=revision_id, commit=commit, replace_manual=replace_manual)


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


def plan_lines(db: Session, wc_ids: list[int] | None, start: date | None, end: date | None, mode: str | None = None) -> list[PlanLineOut]:
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
    if mode:
        q = q.filter(PlanLine.mode == mode)
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
                semi_finished_code=pl.semi_finished_code or (pl.operation.semi_finished_code if pl.operation else ""),
                mode=pl.mode,
                strategy=pl.strategy or "",
                revision_id=pl.revision_id,
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
    forecast = planned_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1], mode="forecast")
    forecast_pls = (
        db.query(PlanLine)
        .options(joinedload(PlanLine.order).joinedload(Order.item))
        .filter(
            PlanLine.work_center_id.in_([w.id for w in wcs]),
            PlanLine.week_start >= start,
            PlanLine.week_start <= wk_list[-1],
            PlanLine.mode == "forecast",
        )
        .all()
    )
    fc_detail: dict[tuple[int, date], list[ForecastLoadDetail]] = defaultdict(list)
    for pl in forecast_pls:
        if not pl.order:
            continue
        fc_detail[(pl.work_center_id, pl.week_start)].append(
            ForecastLoadDetail(
                order_no=pl.order.order_no,
                item_code=pl.order.item.code if pl.order.item else "",
                hours=round(pl.planned_hours, 2),
            )
        )
    actual = actual_hours_by_week(db, [w.id for w in wcs], start, wk_list[-1])
    result = []
    for w in wcs:
        rows = []
        unit = w.capacity_unit_hours or 1.0
        ovl = cap.Overrides(db, w.id)
        for wk in wk_list:
            c_raw = cap.week_capacity_hours(db, w, wk)
            c_plan = cap.planning_capacity_hours(db, w, wk)
            p = planned.get((w.id, wk), 0.0)
            fc = forecast.get((w.id, wk), 0.0)
            a = actual.get((w.id, wk), 0.0)
            remaining = max(p - a, 0.0)
            idle = max(c_plan - p, 0.0)
            n_days = len(cap.working_days(w, wk, wk + timedelta(days=6), ovl))
            daily_cap = c_raw / n_days if n_days else 0.0
            rem_days = remaining / daily_cap if daily_cap > 0 else 0.0
            rows.append(
                WeekLoad(
                    week_start=wk,
                    capacity_hours=round(c_raw, 2),
                    planning_capacity_hours=round(c_plan, 2),
                    planned_hours=round(p, 2),
                    forecast_hours=round(fc, 2),
                    firm_planned_hours=round(max(p - fc, 0.0), 2),
                    forecast_details=fc_detail.get((w.id, wk), []),
                    utilization=round(p / c_plan, 3) if c_plan > 0 else 0.0,
                    actual_hours=round(a, 2),
                    actual_utilization=round(a / c_raw, 3) if c_raw > 0 else 0.0,
                    remaining_hours=round(remaining, 2),
                    remaining_days=round(rem_days, 2),
                    idle_hours=round(idle, 2),
                    capacity_units=round(c_raw / unit, 2),
                    planned_units=round(p / unit, 2),
                    actual_units=round(a / unit, 2),
                )
            )
        result.append(WorkCenterLoad(work_center_id=w.id, work_center_code=w.code, weeks=rows))
    return result


# ---------------- Terminleme (lead time) ----------------

_WEEK_FULL_EPS = 0.5  # haftalik plan ~%100 ise kalan saatle baslama (249.9/250 bug)


def _week_room_hours(db: Session, wc: WorkCenter, wk: date, planned_week: dict[date, float]) -> float:
    """Haftada kalan planlanabilir saat; dolu haftada 0."""
    cap_h = cap.planning_capacity_hours(db, wc, wk)
    used = planned_week.get(wk, 0.0)
    if used >= cap_h - _WEEK_FULL_EPS:
        return 0.0
    return cap_h - used


def _daily_free_hours(db: Session, wc: WorkCenter, day: date, emp: int, planned_week: dict[date, float], wdays_cache: dict[date, int], ovl: cap.Overrides) -> float:
    total = cap.daily_capacity_hours(wc, day, emp, ovl.get(day))
    if total <= 0:
        return 0.0
    wk = cap.week_start(day)
    week_room = _week_room_hours(db, wc, wk, planned_week)
    if week_room <= _WEEK_FULL_EPS:
        return 0.0
    if wk not in wdays_cache:
        wdays_cache[wk] = len(cap.working_days(wc, wk, wk + timedelta(days=6), ovl)) or 1
    used = planned_week.get(wk, 0.0) / wdays_cache[wk]
    return max(min(total - used, week_room), 0.0)


def _schedule_op(db: Session, wc: WorkCenter, hours: float, earliest: datetime, planned_week: dict[date, float]) -> tuple[datetime, datetime]:
    """Bir operasyonu 'earliest' anindan itibaren ilk gercek bos kapasiteye yerlestirir."""
    emp = cap.employee_count(db, wc)
    ovl = cap.Overrides(db, wc.id)
    wdays_cache: dict[date, int] = {}
    hours_left = hours
    cursor = earliest
    day = cursor.date()
    step_start: datetime | None = None
    step_end = cursor
    guard = 0
    while hours_left > _WEEK_FULL_EPS and guard < 730:
        guard += 1
        wk = cap.week_start(day)
        if _week_room_hours(db, wc, wk, planned_week) <= _WEEK_FULL_EPS:
            day = wk + timedelta(days=7)
            cursor = datetime.combine(day, cap.first_shift_start(wc, day, ovl.get(day)))
            continue
        ov = ovl.get(day)
        free = _daily_free_hours(db, wc, day, emp, planned_week, wdays_cache, ovl)
        if free > _WEEK_FULL_EPS:
            day_start = datetime.combine(day, cap.first_shift_start(wc, day, ov))
            hc = max(cap.daily_headcount(wc, day, emp, ov), 1)
            nominal_per_day = cap.daily_nominal_hours(wc, day, emp, ov) / hc
            if day == cursor.date() and cursor > day_start:
                frac_used = (cursor - day_start).total_seconds() / 3600.0
                free = max(free * (1 - min(frac_used / max(nominal_per_day, 1.0), 1.0)), 0.0)
                day_start = cursor
            week_room = _week_room_hours(db, wc, wk, planned_week)
            take = min(free, hours_left, week_room)
            if take > _WEEK_FULL_EPS:
                daily_eff = cap.daily_capacity_hours(wc, day, emp, ov)
                elapsed_nominal = (take / daily_eff) * nominal_per_day if daily_eff > 0 else take
                if step_start is None:
                    step_start = day_start
                step_end = day_start + timedelta(hours=elapsed_nominal)
                hours_left -= take
                planned_week[wk] = planned_week.get(wk, 0.0) + take
        if hours_left > _WEEK_FULL_EPS:
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


def load_detail(db: Session, work_center_id: int, week_start: date, *, firm_only: bool = False) -> LoadDetailOut:
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
    )
    if firm_only:
        pls = pls.filter(PlanLine.mode != "forecast")
    pls = pls.all()
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


def weekly_output(db: Session, week_start: date, work_center_ids: list[int] | None = None) -> WeeklyOutputOut:
    """Haftalık üretim planı — iş merkezi bazlı, tahmin hariç."""
    wk = cap.week_start(week_start)
    week_end = wk + timedelta(days=4)
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True), WorkCenter.is_planned.is_(True))
    if work_center_ids:
        q = q.filter(WorkCenter.id.in_(work_center_ids))
    wcs = q.order_by(WorkCenter.code).all()
    sections: list[WeeklyOutputWc] = []
    total_jobs = 0
    total_qty = 0.0
    for wc in wcs:
        detail = load_detail(db, wc.id, wk, firm_only=True)
        if not detail.rows:
            continue
        rows = sorted(detail.rows, key=lambda r: (r.planned_start or "", r.order_no, r.operation_seq))
        sections.append(
            WeeklyOutputWc(
                work_center_id=wc.id,
                work_center_code=wc.code,
                total_qty=detail.total_qty,
                rows=rows,
            )
        )
        total_jobs += len(rows)
        total_qty += detail.total_qty
    return WeeklyOutputOut(
        week_start=wk,
        week_end=week_end,
        work_centers=sections,
        total_jobs=total_jobs,
        total_qty=round(total_qty, 2),
    )


def add_forecast_from_leadtime(db: Session, req: ForecastFromLeadTimeIn, username: str) -> tuple[int, int]:
    """Terminleme sonucunu tahmin plan satirlari olarak kaydeder; kapasite asimi engellenir. (order_id, satir_sayisi)"""
    item = (
        db.query(Item)
        .options(joinedload(Item.operations).joinedload(RoutingOperation.work_center))
        .filter(Item.code == req.item_code)
        .first()
    )
    if not item:
        raise ValueError("Stok kodu bulunamadi")
    if not req.steps:
        raise ValueError("Operasyon adimi yok")

    step_dates = [datetime.strptime(s.start[:10], "%Y-%m-%d").date() for s in req.steps]
    end_dt = datetime.strptime(req.steps[-1].end[:10], "%Y-%m-%d").date()
    horizon_start = cap.week_start(min(step_dates))
    horizon_end = cap.week_start(end_dt) + timedelta(weeks=16)
    weeks: list[date] = []
    wk = horizon_start
    while wk <= horizon_end:
        weeks.append(wk)
        wk += timedelta(weeks=1)

    wc_ids = list({op.work_center_id for op in item.operations})
    wcs = {w.id: w for w in db.query(WorkCenter).filter(WorkCenter.id.in_(wc_ids)).all()}
    existing = planned_hours_by_week(db, wc_ids, horizon_start, horizon_end)
    remaining: dict[tuple[int, date], float] = {}
    for wc_id in wc_ids:
        wc = wcs.get(wc_id)
        if not wc:
            continue
        for week in weeks:
            used = existing.get((wc_id, week), 0.0)
            remaining[(wc_id, week)] = _week_room_hours(db, wc, week, {week: used})

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
    unplaced: list[str] = []
    for step in req.steps:
        op = next((o for o in item.operations if o.seq == step.operation_seq), None)
        if not op:
            continue
        start_day = datetime.strptime(step.start[:10], "%Y-%m-%d").date()
        start_wk = cap.week_start(start_day)
        idx = weeks.index(start_wk) if start_wk in weeks else 0
        hours_left = float(step.hours)
        total_hours = hours_left
        while hours_left > 1e-6 and idx < len(weeks):
            week = weeks[idx]
            avail = remaining.get((op.work_center_id, week), 0.0)
            if avail > _WEEK_FULL_EPS:
                take = min(avail, hours_left)
                qty = req.quantity * (take / total_hours) if total_hours > 0 else 0.0
                db.add(
                    PlanLine(
                        order_id=order.id,
                        operation_id=op.id,
                        work_center_id=op.work_center_id,
                        week_start=week,
                        planned_hours=round(take, 3),
                        planned_qty=round(qty, 2),
                        mode="forecast",
                        created_by=username,
                    )
                )
                remaining[(op.work_center_id, week)] = avail - take
                hours_left -= take
                created += 1
            if hours_left > 1e-6:
                idx += 1
        if hours_left > _WEEK_FULL_EPS:
            wc = wcs.get(op.work_center_id)
            code = wc.code if wc else "?"
            unplaced.append(f"{code} ({round(hours_left, 1)} sa)")

    if unplaced:
        db.rollback()
        raise ValueError("Kapasite yetersiz — plana eklenemedi: " + ", ".join(unplaced))

    db.commit()
    return order.id, created


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


def list_forecasts(db: Session) -> list[ForecastSummaryOut]:
    """Plana eklenmis terminleme tahminleri (forecast siparis + plan satirlari)."""
    orders = (
        db.query(Order)
        .options(joinedload(Order.item))
        .filter(Order.status == "forecast")
        .order_by(Order.created_at.desc(), Order.id.desc())
        .all()
    )
    out: list[ForecastSummaryOut] = []
    for o in orders:
        pls = db.query(PlanLine).filter(PlanLine.order_id == o.id, PlanLine.mode == "forecast").order_by(PlanLine.week_start).all()
        if not pls:
            continue
        weeks = [p.week_start for p in pls]
        out.append(
            ForecastSummaryOut(
                order_id=o.id,
                order_no=o.order_no,
                item_code=o.item.code if o.item else "",
                item_name=o.item.name if o.item else "",
                quantity=o.quantity,
                due_date=o.due_date,
                total_hours=round(sum(p.planned_hours for p in pls), 2),
                line_count=len(pls),
                week_from=weeks[0],
                week_to=weeks[-1],
                created_at=o.created_at,
            )
        )
    return out


def delete_forecast(db: Session, order_id: int) -> int:
    order = db.get(Order, order_id)
    if not order or order.status != "forecast":
        raise ValueError("Tahmin siparisi bulunamadi")
    n = db.query(PlanLine).filter(PlanLine.order_id == order_id, PlanLine.mode == "forecast").delete(synchronize_session=False)
    db.delete(order)
    db.commit()
    return n
