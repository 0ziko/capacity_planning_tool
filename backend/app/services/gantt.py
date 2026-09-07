"""Plan Gantt: is merkezi bazinda siparis / yarimamul operasyon zaman cizelgesi."""

from collections import defaultdict
from datetime import date, timedelta
from math import ceil

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionActual, WorkCenter
from app.schemas import GanttBar, GanttOut
from app.services import capacity as cap
from app.services.wip import resolve_wip, wip_index


def actual_hours_by_week(db: Session, wc_ids: list[int], start: date, end: date) -> dict[tuple[int, date], float]:
    """Is merkezi x hafta (Pzt) -> kazanilan saat toplami."""
    if not wc_ids:
        return {}
    rows = (
        db.query(
            ProductionActual.work_center_id,
            ProductionActual.prod_date,
            func.sum(ProductionActual.earned_hours),
        )
        .filter(
            ProductionActual.work_center_id.in_(wc_ids),
            ProductionActual.prod_date >= start,
            ProductionActual.prod_date <= end + timedelta(days=6),
        )
        .group_by(ProductionActual.work_center_id, ProductionActual.prod_date)
        .all()
    )
    out: dict[tuple[int, date], float] = defaultdict(float)
    for wc_id, prod_date, hours in rows:
        out[(wc_id, cap.week_start(prod_date))] += float(hours or 0)
    return out


def _line_window_in_week(db: Session, wc: WorkCenter, wk: date, line_id: int, lines_in_week: list[PlanLine]) -> tuple[date, date]:
    """Haftadaki plan satirlari kumulatif dagitilir; hedef satirin baslangic/bitis gunu."""
    wdays = cap.working_days(wc, wk, wk + timedelta(days=6), cap.Overrides(db, wc.id))
    if not wdays:
        return wk, wk + timedelta(days=4)
    capacity = cap.week_capacity_hours(db, wc, wk)
    ordered = sorted(lines_in_week, key=lambda p: (p.order.due_date, p.order.order_no, p.order_id, p.id))
    cum = 0.0
    for p in ordered:
        if p.id == line_id:
            if capacity <= 0:
                return wdays[0], wdays[-1]
            start_frac = cum / capacity
            end_frac = min((cum + p.planned_hours) / capacity, 1.0)
            si = max(min(int(start_frac * len(wdays)), len(wdays) - 1), 0)
            ei = max(min(ceil(end_frac * len(wdays)) - 1, len(wdays) - 1), si)
            return wdays[si], wdays[ei]
        cum += p.planned_hours
    return wdays[0], wdays[-1]


def _timeline_days(db: Session, wc: WorkCenter, start: date, end: date) -> list[date]:
    days: list[date] = []
    wk = cap.week_start(start)
    ovl = cap.Overrides(db, wc.id)
    while wk <= cap.week_start(end):
        for d in cap.working_days(wc, wk, wk + timedelta(days=6), ovl):
            if start <= d <= end:
                days.append(d)
        wk += timedelta(weeks=1)
    return sorted(set(days))


def _production_map(db: Session, wc_id: int, orders_by_id: dict[int, Order], as_of: date) -> dict[tuple[int, int], dict]:
    """(order_id, operation_id) -> {qty, hours, last_date}."""
    if not orders_by_id:
        return {}
    item_ids = {o.item_id for o in orders_by_id.values()}
    items_by_id = {o.item_id: o.item for o in orders_by_id.values()}
    wip_idx = wip_index(db)
    actuals = (
        db.query(ProductionActual)
        .filter(
            ProductionActual.work_center_id == wc_id,
            ProductionActual.item_id.in_(item_ids),
            ProductionActual.prod_date <= as_of,
        )
        .order_by(ProductionActual.prod_date, ProductionActual.id)
        .all()
    )
    orders_by_no: dict[tuple[str, int], Order] = {(o.order_no.upper(), o.item_id): o for o in orders_by_id.values()}

    def op_id_for(item: Item, seq: int | None, wc: int, wip: str) -> int | None:
        if wip:
            try:
                return resolve_wip(db, wip, item.code, wip_idx).id
            except ValueError:
                pass
        if seq is not None:
            op = next((x for x in item.operations if x.seq == seq), None)
        else:
            op = next((x for x in item.operations if x.work_center_id == wc), None)
        return op.id if op else None

    out: dict[tuple[int, int], dict] = defaultdict(lambda: {"qty": 0.0, "hours": 0.0, "last_date": None})
    fifo_left: dict[tuple[int, int], float] = {}
    orders_by_item: dict[int, list[Order]] = defaultdict(list)
    for o in orders_by_id.values():
        orders_by_item[o.item_id].append(o)

    for a in actuals:
        item = items_by_id.get(a.item_id)
        if not item:
            continue
        oid = op_id_for(item, a.operation_seq, a.work_center_id, a.semi_finished_code or "")
        if oid is None:
            continue
        if a.order_no:
            target = orders_by_no.get((a.order_no.upper(), a.item_id))
            if target is None:
                continue
            key = (target.id, oid)
            out[key]["qty"] += a.quantity
            out[key]["hours"] += a.earned_hours
            out[key]["last_date"] = a.prod_date
            continue
        qty_left = a.quantity
        hpu = (a.earned_hours / a.quantity) if a.quantity else 0.0
        for o in orders_by_item[a.item_id]:
            key = (o.id, oid)
            if key not in fifo_left:
                fifo_left[key] = max(o.quantity - out[key]["qty"], 0.0)
            room = fifo_left[key]
            if room <= 1e-9:
                continue
            take = min(room, qty_left)
            out[key]["qty"] += take
            out[key]["hours"] += take * hpu
            out[key]["last_date"] = a.prod_date
            fifo_left[key] = room - take
            qty_left -= take
            if qty_left <= 1e-9:
                break
    return out


def plan_gantt(db: Session, work_center_id: int, start: date, end: date, as_of: date | None = None) -> GanttOut:
    wc = db.get(WorkCenter, work_center_id)
    if not wc:
        raise ValueError("Is merkezi bulunamadi")
    as_of = as_of or date.today()
    if end < start:
        raise ValueError("Bitis tarihi baslangictan once olamaz")

    wk_start = cap.week_start(start)
    wk_end = cap.week_start(end)
    lines = (
        db.query(PlanLine)
        .options(
            joinedload(PlanLine.order).joinedload(Order.item).joinedload(Item.operations),
            joinedload(PlanLine.operation),
        )
        .filter(
            PlanLine.work_center_id == work_center_id,
            PlanLine.week_start >= wk_start,
            PlanLine.week_start <= wk_end,
        )
        .all()
    )

    order_ids = {pl.order_id for pl in lines}
    orders_by_id = {
        o.id: o
        for o in (
            db.query(Order)
            .options(joinedload(Order.item).joinedload(Item.operations))
            .filter(Order.id.in_(order_ids))
            .all()
        )
    } if order_ids else {}

    prod_map = _production_map(db, work_center_id, orders_by_id, as_of)
    by_week: dict[date, list[PlanLine]] = defaultdict(list)
    for pl in lines:
        by_week[pl.week_start].append(pl)

    bars: list[GanttBar] = []
    for wk in sorted(by_week):
        week_lines = by_week[wk]
        for pl in sorted(week_lines, key=lambda p: (p.order.due_date, p.order.order_no, p.operation.seq, p.id)):
            ps, pe = _line_window_in_week(db, wc, wk, pl.id, week_lines)
            if pe < start or ps > end:
                continue
            target_qty = pl.planned_qty if pl.planned_qty > 0 else pl.order.quantity
            pr = prod_map.get((pl.order_id, pl.operation_id), {"qty": 0.0, "hours": 0.0, "last_date": None})
            produced = float(pr["qty"])
            remaining = max(target_qty - produced, 0.0)
            if remaining <= 1e-6:
                status = "completed"
            elif produced > 0:
                status = "in_progress"
            else:
                status = "planned"
            op = pl.operation
            bars.append(
                GanttBar(
                    plan_line_id=pl.id,
                    order_id=pl.order_id,
                    order_no=pl.order.order_no,
                    item_code=pl.order.item.code,
                    semi_finished_code=(op.semi_finished_code if op else "") or "",
                    operation_seq=op.seq if op else 0,
                    operation_name=op.operation_name if op else "",
                    planned_start=max(ps, start),
                    planned_end=min(pe, end),
                    week_start=wk,
                    planned_qty=round(target_qty, 2),
                    produced_qty=round(produced, 2),
                    remaining_qty=round(remaining, 2),
                    planned_hours=round(pl.planned_hours, 2),
                    earned_hours=round(float(pr["hours"]), 2),
                    due_date=pl.order.due_date,
                    status=status,
                    last_prod_date=pr["last_date"],
                )
            )

    return GanttOut(
        work_center_id=wc.id,
        work_center_code=wc.code,
        range_start=start,
        range_end=end,
        as_of=as_of,
        timeline_days=_timeline_days(db, wc, start, end),
        bars=sorted(bars, key=lambda b: (b.planned_start, b.order_no, b.operation_seq)),
    )
