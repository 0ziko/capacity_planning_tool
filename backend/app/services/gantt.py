"""Plan Gantt: is merkezi bazinda siparis / yarimamul operasyon zaman cizelgesi."""

from collections import defaultdict
from datetime import date, timedelta
from math import ceil

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionActual, ProductionBatch, ProductionBatchOrder, WorkCenter
from app.schemas import GanttBar, GanttOut, PlanSegmentOut
from app.core.config import get_settings
from app.services import capacity as cap
from app.services.orders import effective_due, plan_line_priority_key
from app.services.wip import resolve_wip, wip_index


def actual_hours_by_week(db: Session, wc_ids: list[int], start: date, end: date) -> dict[tuple[int, date], float]:
    """Is merkezi x hafta (Pzt) -> kazanilan saat toplami."""
    if not wc_ids:
        return {}
    if get_settings().production_source == "mes":
        from app.services.mes_actuals import measure
        out = defaultdict(float)
        for (wc, day), hours in measure(db, end + timedelta(days=6))["daily"].items():
            if wc in wc_ids and start <= cap.week_start(day) <= end:
                out[(wc, cap.week_start(day))] += hours
        return dict(out)
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


def _line_window_in_week(db: Session, wc: WorkCenter, wk: date, line_id: int, lines_in_week: list[PlanLine], *, cache: dict | None = None) -> tuple[date, date]:
    """cache: (wc_id, week) -> takvim/kapasite; ayni hafta icin satir basina tekrar sorgu yapilmaz."""
    from app.services.orders import plan_line_window
    target = next((line for line in lines_in_week if line.id == line_id), None)
    return plan_line_window(db, wc, wk, target, lines_in_week, cache=cache)


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
    # Most rows resolve directly by operation sequence. Do not load every route
    # in the database for an empty production set (or a sequence-only set).
    wip_idx = None
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

    def op_id_for(item: Item, seq: int | None, wc: int, wip: str, order_no_hint: str = "") -> int | None:
        nonlocal wip_idx
        if seq is not None:
            op = next((x for x in item.operations if x.seq == seq), None)
            if op:
                return op.id
        if wip:
            if wip_idx is None:
                wip_idx = wip_index(db)
            try:
                return resolve_wip(db, wip, item.code, wip_idx, order_no=order_no_hint or None).id
            except ValueError:
                pass
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
        oid = op_id_for(item, a.operation_seq, a.work_center_id, a.semi_finished_code or "", a.order_no or "")
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


def plan_gantt(db: Session, work_center_id: int, start: date, end: date, as_of: date | None = None, selection_kind: str | None = None, selection_codes: list[str] | None = None) -> GanttOut:
    from app.services.material_schedule import recorded_material_note
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
            joinedload(PlanLine.production_batch).joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order),
            joinedload(PlanLine.operation),
        )
        .filter(
            PlanLine.work_center_id == work_center_id,
            PlanLine.week_start >= wk_start,
            PlanLine.week_start <= wk_end,
        )
        .all()
    )

    selected_ids = None
    if selection_kind:
        if selection_kind not in {"order", "item", "wip"}:
            raise ValueError("Gecersiz Gantt arama turu")
        codes = {c.strip() for c in (selection_codes or []) if c.strip()}
        def selected(pl):
            if selection_kind == "item":
                return pl.order.item.code in codes
            if selection_kind == "wip":
                return bool(pl.operation and pl.operation.semi_finished_code in codes)
            orders = [l.order for l in pl.production_batch.orders if l.order] if pl.production_batch else [pl.order]
            return (bool(pl.production_batch and pl.production_batch.batch_no in codes) or
                    any(o.order_no in codes or f"{o.order_no}/{o.position_no or ''}" in codes for o in orders))
        selected_ids = {pl.id for pl in lines if selected(pl)}
        if not selected_ids:
            return GanttOut(work_center_id=wc.id, work_center_code=wc.code, range_start=start,
                            range_end=end, as_of=as_of, timeline_days=[], bars=[])

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

    mes_matches = None
    if get_settings().production_source == "mes":
        from app.services.mes_actuals import measure
        mes_matches = measure(db, as_of)["matches"]
    prod_map = {} if mes_matches is not None else _production_map(db, work_center_id, orders_by_id, as_of)
    by_week: dict[date, list[PlanLine]] = defaultdict(list)
    for pl in lines:
        by_week[pl.week_start].append(pl)

    wip_codes = {pl.operation.semi_finished_code for pl in lines if pl.operation and pl.operation.semi_finished_code}
    wip_names = dict(db.query(Item.code, Item.name).filter(Item.code.in_(wip_codes)).all()) if wip_codes else {}

    bars: list[GanttBar] = []
    window_cache: dict = {}
    for wk in sorted(by_week):
        week_lines = by_week[wk]
        for pl in sorted(week_lines, key=plan_line_priority_key):
            if selected_ids is not None and pl.id not in selected_ids:
                continue
            ps, pe = _line_window_in_week(db, wc, wk, pl.id, week_lines, cache=window_cache)
            if pe < start or ps > end:
                continue
            if pl.production_batch:
                target_qty = pl.planned_qty if pl.planned_qty > 0 else pl.production_batch.quantity
                batch_no = pl.production_batch.batch_no
                batch_nos = [f"{l.order.order_no}{f'/{l.order.position_no}' if l.order and l.order.position_no else ''}" for l in pl.production_batch.orders if l.order]
                display_no = batch_no
                display_pos = ""
            else:
                target_qty = pl.planned_qty if pl.planned_qty > 0 else pl.order.quantity
                batch_no = ""
                batch_nos = []
                display_no = pl.order.order_no
                display_pos = pl.order.position_no or ""
            pr = (mes_matches.get(pl.id) if mes_matches is not None else prod_map.get((pl.order_id, pl.operation_id))) or {"qty": 0., "hours": 0., "last_date": None}
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
                    material_unverified=pl.material_unverified,
                    material_note=recorded_material_note(pl.material_unverified),
                    plan_line_id=pl.id,
                    order_id=pl.order_id,
                    order_no=display_no,
                    position_no=display_pos,
                    production_batch_id=pl.production_batch_id,
                    batch_no=batch_no,
                    batch_order_nos=batch_nos,
                    item_code=pl.order.item.code,
                    semi_finished_code=(op.semi_finished_code if op else "") or "",
                    semi_finished_name=wip_names.get(op.semi_finished_code, "") if op else "",
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
                    due_date=(
                        pl.production_batch.due_date
                        if pl.production_batch
                        else effective_due(pl.order)
                    ),
                    status=status,
                    last_prod_date=pr["last_date"],
                )
            )

    from app.services.daily_scheduler import segments_for_gantt

    seg_rows = [] if selection_kind else segments_for_gantt(db, work_center_id, start, end)
    segments = [
        PlanSegmentOut(
            id=s.id,
            order_id=s.order_id,
            operation_id=s.operation_id,
            production_batch_id=s.production_batch_id,
            work_center_id=s.work_center_id,
            machine_id=s.machine_id,
            machine_code=s.machine.code if s.machine else "",
            segment_kind=s.segment_kind,
            start_at=s.start_at,
            end_at=s.end_at,
            good_qty=s.good_qty,
            crew_size=s.crew_size,
            is_locked=s.is_locked,
        )
        for s in seg_rows
    ]
    note = "Haftalik plandan yaklasik gun dagilimi; kesin gunluk cizelge degildir."
    if segments:
        note = "Gunluk kaynak segmentleri (plan_operation_segments); cubuklar haftalik ozet."

    if mes_matches is not None:
        note += " MES miktari guncel plan satirina bir kez eslenir; musteri rezervasyonu degildir."
    return GanttOut(
        work_center_id=wc.id,
        work_center_code=wc.code,
        range_start=start,
        range_end=end,
        as_of=as_of,
        timeline_days=_timeline_days(db, wc, start, end),
        bars=sorted(bars, key=lambda b: (b.planned_start, b.order_no, b.operation_seq)),
        segments=segments,
        distribution_note=note,
    )
