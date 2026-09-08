"""Siparis odakli servisler: tekil CRUD, plan sonucu bitis tarihleri,
siparis bazli ilerleme ve ayni stok kodlu siparisleri birlestirme."""

from collections import defaultdict
from datetime import date, timedelta
from math import ceil

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionActual, Reservation, RoutingOperation, Shipment, WorkCenter
from app.schemas import (
    MergeGroup,
    MergeRequest,
    OrderIn,
    OrderOut,
    ProductionBatchCreate,
    OrderProgressOp,
    OrderProgressOut,
    OrderScheduleOut,
)
from app.services import capacity as cap


# ---------------- Yardimcilar ----------------

def order_out(o: Order) -> OrderOut:
    row = OrderOut.model_validate(o)
    row.item_code = o.item.code
    row.item_name = o.item.name
    row.revenue = round(o.quantity * (o.unit_price or 0.0), 2)
    return row


def _planned_wcs(db: Session, wc_ids: list[int] | None) -> list[WorkCenter]:
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if wc_ids:
        q = q.filter(WorkCenter.id.in_(wc_ids))
    else:
        q = q.filter(WorkCenter.is_planned.is_(True))
    return q.order_by(WorkCenter.code).all()


def _open_orders(db: Session) -> list[Order]:
    return (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations).joinedload(RoutingOperation.work_center))
        .filter(Order.status == "open")
        .order_by(Order.due_date, Order.order_no, Order.position_no, Order.id)
        .all()
    )


def _order_lookup_key(order_no: str, position_no: str, item_id: int) -> tuple:
    """Siparis eslestirme: poz no varsa (siparis,poz); yoksa geriye uyum (siparis,stok)."""
    on = order_no.strip().upper()
    pos = (position_no or "").strip().upper()
    if pos:
        return ("pos", on, pos)
    return ("item", on, item_id)


def _index_orders(orders: list[Order]) -> dict[tuple, Order]:
    idx: dict[tuple, Order] = {}
    for o in orders:
        if o.position_no:
            idx[("pos", o.order_no.upper(), o.position_no.upper())] = o
        else:
            idx[("item", o.order_no.upper(), o.item_id)] = o
    return idx


# ---------------- Tekil siparis CRUD ----------------

def create_order(db: Session, data: OrderIn) -> Order:
    item = db.query(Item).filter(Item.code == data.item_code.strip()).first()
    if not item:
        item = db.query(Item).filter(Item.code.ilike(data.item_code.strip())).first()
    if not item:
        raise ValueError(f"Stok kodu bulunamadi: {data.item_code} (once Stok/BOM/Rota ekranindan tanimlayin)")
    pos = (data.position_no or "").strip()
    key = _order_lookup_key(data.order_no, pos, item.id)
    idx = _index_orders(db.query(Order).filter(Order.status != "merged").all())
    if key in idx:
        dup = idx[key]
        label = f"{data.order_no} / poz {pos}" if pos else f"{data.order_no} / {item.code}"
        raise ValueError(f"{label} siparisi zaten var (id={dup.id}); duzenlemek icin mevcut satiri kullanin")
    o = Order(
        order_no=data.order_no.strip(),
        position_no=pos,
        customer=data.customer.strip(),
        due_date=data.due_date,
        item_id=item.id,
        quantity=data.quantity,
        unit_price=data.unit_price,
        status="open",
        note=data.note.strip(),
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def update_order(db: Session, o: Order, data: OrderIn) -> Order:
    item = db.query(Item).filter(Item.code.ilike(data.item_code.strip())).first()
    if not item:
        raise ValueError(f"Stok kodu bulunamadi: {data.item_code}")
    item_changed = item.id != o.item_id
    o.order_no = data.order_no.strip()
    o.position_no = (data.position_no or "").strip()
    o.customer = data.customer.strip()
    o.due_date = data.due_date
    o.item_id = item.id
    o.quantity = data.quantity
    o.unit_price = data.unit_price
    o.note = data.note.strip()
    if item_changed:
        # rota degisti; eski plan satirlari gecersiz
        db.query(PlanLine).filter(PlanLine.order_id == o.id).delete(synchronize_session=False)
    db.commit()
    db.refresh(o)
    return o


def bulk_delete_orders(db: Session, order_ids: list[int]) -> int:
    """Acik siparisleri toplu siler; plan, rezervasyon ve sevk baglantilarini temizler."""
    if not order_ids:
        return 0
    if db.query(Order).filter(Order.merged_into_id.in_(order_ids)).first():
        raise ValueError("Silinecek siparislerden biri birlesik siparis; once birlestirmeyi geri alin")
    db.query(PlanLine).filter(PlanLine.order_id.in_(order_ids)).delete(synchronize_session=False)
    db.query(Reservation).filter(Reservation.order_id.in_(order_ids)).delete(synchronize_session=False)
    db.query(Shipment).filter(Shipment.order_id.in_(order_ids)).delete(synchronize_session=False)
    db.query(Order).filter(Order.merged_into_id.in_(order_ids)).update({Order.merged_into_id: None}, synchronize_session=False)
    return db.query(Order).filter(Order.id.in_(order_ids)).delete(synchronize_session=False)


# ---------------- Plan sonucu: siparis bitis tarihleri ----------------

def _end_day_in_week(db: Session, wc: WorkCenter, wk: date, order_id: int, lines_in_week: list[PlanLine]) -> date:
    """Haftadaki plan satirlari (termin sirasiyla) kumulatif doldurulur; bu siparisin
    payi bittigi noktadaki calisma gununu dondurur."""
    wdays = cap.working_days(wc, wk, wk + timedelta(days=6), cap.Overrides(db, wc.id))
    if not wdays:
        return wk + timedelta(days=4)
    capacity = cap.week_capacity_hours(db, wc, wk)
    ordered = sorted(lines_in_week, key=lambda p: (p.order.due_date, p.order.order_no, p.order_id, getattr(p, "id", 0) or 0))
    cum = 0.0
    reached = False
    for p in ordered:
        cum += p.planned_hours
        if p.order_id == order_id:
            reached = True
        elif reached:
            break
    if capacity <= 0:
        return wdays[-1]
    frac = min(cum / capacity, 1.0)
    idx = max(min(ceil(frac * len(wdays)) - 1, len(wdays) - 1), 0)
    return wdays[idx]


def order_schedule(db: Session, wc_ids: list[int] | None, lines=None, orders: list[Order] | None = None) -> list[OrderScheduleOut]:
    """lines verilmezse veritabanindaki plan satirlari kullanilir; verilirse (simulasyon)
    order_id / work_center_id / week_start / planned_hours / order alanlari olan nesneler beklenir."""
    wcs = _planned_wcs(db, wc_ids)
    wc_by_id = {w.id: w for w in wcs}
    orders = orders if orders is not None else _open_orders(db)
    if not orders:
        return []
    if lines is None:
        lines = (
            db.query(PlanLine)
            .options(joinedload(PlanLine.order), joinedload(PlanLine.production_batch))
            .filter(PlanLine.order_id.in_([o.id for o in orders]))
            .all()
        )
        batch_ids = {p.production_batch_id for p in lines if p.production_batch_id}
        if batch_ids:
            extra = (
                db.query(PlanLine)
                .options(joinedload(PlanLine.order), joinedload(PlanLine.production_batch))
                .filter(PlanLine.production_batch_id.in_(batch_ids))
                .all()
            )
            seen = {p.id for p in lines}
            lines = lines + [p for p in extra if p.id not in seen]
    if wc_by_id:
        lines = [p for p in lines if p.work_center_id in wc_by_id]
    from app.services import production_batches as pb

    order_batch = pb.batch_order_map(db)
    by_order: dict[int, list[PlanLine]] = defaultdict(list)
    by_batch: dict[int, list[PlanLine]] = defaultdict(list)
    by_wc_week: dict[tuple[int, date], list[PlanLine]] = defaultdict(list)
    for p in lines:
        if p.production_batch_id:
            by_batch[p.production_batch_id].append(p)
        else:
            by_order[p.order_id].append(p)
        by_wc_week[(p.work_center_id, p.week_start)].append(p)

    out: list[OrderScheduleOut] = []
    for o in orders:
        ops = [op for op in o.item.operations if op.work_center_id in wc_by_id]
        required = sum(op.hours_for(o.quantity) for op in ops)
        batch = order_batch.get(o.id)
        if batch:
            pls = by_batch.get(batch.id, [])
            share = o.quantity / batch.quantity if batch.quantity else 0.0
            planned = sum(p.planned_hours for p in pls) * share
            start = min((p.week_start for p in pls), default=None)
            end_week = max((p.week_start for p in pls), default=None)
            schedule_order_id = sorted(batch.orders, key=lambda l: l.order_id)[0].order_id if batch.orders else o.id
        else:
            pls = by_order.get(o.id, [])
            share = 1.0
            planned = sum(p.planned_hours for p in pls)
            start = min((p.week_start for p in pls), default=None)
            end_week = max((p.week_start for p in pls), default=None)
            schedule_order_id = o.id
        planned_end = None
        last_wc_code = ""
        if end_week is not None:
            # son haftadaki satirlar birden fazla is merkezinde olabilir; en gec biteni al
            candidates = []
            for p in pls:
                if p.week_start != end_week:
                    continue
                wc = wc_by_id.get(p.work_center_id)
                if not wc:
                    continue
                d = _end_day_in_week(db, wc, end_week, schedule_order_id, by_wc_week[(wc.id, end_week)])
                candidates.append((d, wc.code))
            if candidates:
                planned_end, last_wc_code = max(candidates)
        coverage = (planned / required * 100) if required > 0 else 0.0
        if required <= 1e-6:
            status = "no_ops"
        elif planned <= 1e-6:
            status = "unplanned"
        elif coverage < 99.5:
            status = "partial"
        elif planned_end and planned_end > o.due_date:
            status = "late"
        else:
            status = "on_time"
        out.append(
            OrderScheduleOut(
                order_id=o.id,
                order_no=o.order_no,
                position_no=o.position_no or "",
                customer=o.customer,
                item_code=o.item.code,
                item_name=o.item.name,
                quantity=o.quantity,
                unit_price=o.unit_price or 0.0,
                revenue=round(o.quantity * (o.unit_price or 0.0), 2),
                due_date=o.due_date,
                required_hours=round(required, 2),
                planned_hours=round(planned, 2),
                coverage_pct=round(min(coverage, 100.0), 1),
                planned_start=start,
                planned_end_week=end_week,
                planned_end=planned_end,
                last_work_center_code=last_wc_code,
                lateness_days=(planned_end - o.due_date).days if planned_end else None,
                plan_status=status,
            )
        )
    return out


# ---------------- Siparis bazli ilerleme ----------------

def order_progress(db: Session, wc_ids: list[int] | None, as_of: date | None = None) -> list[OrderProgressOut]:
    """Gunluk uretim kayitlarini siparislere dagitir.

    Eslestirme: siparis no (buyuk/kucuk harf duyarsiz) + stok kodu + operasyon.
    Siparis no bos olan uretim satirlari, ayni stok kodunun acik siparislerine
    termin sirasiyla (FIFO) dagitilir.
    """
    as_of = as_of or date.today()
    wcs = _planned_wcs(db, wc_ids)
    wc_by_id = {w.id: w for w in wcs}
    orders = _open_orders(db)
    if not orders:
        return []
    item_ids = {o.item_id for o in orders}
    actuals = (
        db.query(ProductionActual)
        .filter(ProductionActual.item_id.in_(item_ids), ProductionActual.prod_date <= as_of)
        .order_by(ProductionActual.prod_date, ProductionActual.id)
        .all()
    )
    planned = defaultdict(float)
    for p in db.query(PlanLine).filter(PlanLine.order_id.in_([o.id for o in orders])).all():
        planned[(p.order_id, p.operation_id)] += p.planned_hours

    # operasyon anahtari: (item_id, seq) ; yarimamul kodu veya seq/is merkezi ile cozulur
    from app.services.wip import resolve_wip, wip_index

    wip_idx = wip_index(db)

    def op_key(item: Item, seq: int | None, wc_id: int, wip_code: str = "") -> int | None:
        if wip_code:
            try:
                op = resolve_wip(db, wip_code, item.code, wip_idx)
                return op.id
            except ValueError:
                pass
        if seq is not None:
            op = next((x for x in item.operations if x.seq == seq), None)
        else:
            op = next((x for x in item.operations if x.work_center_id == wc_id), None)
        return op.id if op else None

    items_by_id = {o.item_id: o.item for o in orders}
    orders_by_no: dict[tuple[str, int], Order] = {(o.order_no.upper(), o.item_id): o for o in orders}
    orders_by_item: dict[int, list[Order]] = defaultdict(list)
    for o in orders:
        orders_by_item[o.item_id].append(o)

    produced: dict[tuple[int, int], float] = defaultdict(float)  # (order_id, op_id) -> qty
    earned: dict[tuple[int, int], float] = defaultdict(float)
    dates: dict[int, list[date]] = defaultdict(list)
    fifo_left: dict[tuple[int, int], float] = {}  # (order_id, op_id) -> kalan atanabilir miktar

    for a in actuals:
        item = items_by_id.get(a.item_id)
        if not item:
            continue
        op_id = op_key(item, a.operation_seq, a.work_center_id, a.semi_finished_code or "")
        if op_id is None:
            continue
        if a.order_no:
            target = orders_by_no.get((a.order_no.upper(), a.item_id))
            if target is not None:
                produced[(target.id, op_id)] += a.quantity
                earned[(target.id, op_id)] += a.earned_hours
                dates[target.id].append(a.prod_date)
            # siparis no verilmis ama acik siparis degil (kapali/birlesik) -> dagitilmaz
            continue
        # siparis no bos: FIFO dagitim
        qty_left = a.quantity
        hours_per_unit = (a.earned_hours / a.quantity) if a.quantity else 0.0
        for o in orders_by_item[a.item_id]:
            key = (o.id, op_id)
            if key not in fifo_left:
                fifo_left[key] = max(o.quantity - produced[key], 0.0)
            room = fifo_left[key]
            if room <= 1e-9:
                continue
            take = min(room, qty_left)
            produced[key] += take
            earned[key] += take * hours_per_unit
            fifo_left[key] = room - take
            dates[o.id].append(a.prod_date)
            qty_left -= take
            if qty_left <= 1e-9:
                break
        # artan miktar: hicbir siparise sigmadi (fazla uretim) -> son siparise yaz
        if qty_left > 1e-9 and orders_by_item[a.item_id]:
            last = orders_by_item[a.item_id][-1]
            produced[(last.id, op_id)] += qty_left
            earned[(last.id, op_id)] += qty_left * hours_per_unit

    out: list[OrderProgressOut] = []
    for o in orders:
        ops = [op for op in o.item.operations if op.work_center_id in wc_by_id] if wc_by_id else list(o.item.operations)
        op_rows: list[OrderProgressOp] = []
        req_total = earned_total = 0.0
        for op in ops:
            req = op.hours_for(o.quantity)
            pq = produced.get((o.id, op.id), 0.0)
            eh = earned.get((o.id, op.id), 0.0)
            req_total += req
            earned_total += eh
            op_rows.append(
                OrderProgressOp(
                    operation_seq=op.seq,
                    operation_name=op.operation_name,
                    work_center_code=op.work_center.code if op.work_center else "(silinmiş İM)",
                    semi_finished_code=op.semi_finished_code or "",
                    required_hours=round(req, 2),
                    planned_hours=round(planned.get((o.id, op.id), 0.0), 2),
                    produced_qty=round(pq, 2),
                    earned_hours=round(eh, 2),
                    pct=round(min(pq / o.quantity * 100, 100.0), 1) if o.quantity else 0.0,
                )
            )
        final_qty = op_rows[-1].produced_qty if op_rows else 0.0
        # Ilerleme %: miktar bazli (dar bogaz operasyon); saat farki (setup vb.) tamamlanmayi dusurmez
        if op_rows:
            pct = min(r.pct for r in op_rows)
            if all(r.pct >= 99.5 for r in op_rows):
                pct = 100.0
        else:
            pct = min(final_qty / o.quantity * 100, 100.0) if o.quantity else 0.0
        if not op_rows or earned_total <= 1e-6:
            status = "not_started"
        elif all(r.pct >= 99.5 for r in op_rows):
            status = "completed"
        else:
            status = "in_progress"
        ds = dates.get(o.id, [])
        out.append(
            OrderProgressOut(
                order_id=o.id,
                order_no=o.order_no,
                position_no=o.position_no or "",
                customer=o.customer,
                item_code=o.item.code,
                quantity=o.quantity,
                due_date=o.due_date,
                required_hours=round(req_total, 2),
                earned_hours=round(earned_total, 2),
                produced_qty=final_qty,
                pct=round(pct, 1),
                status=status,
                first_prod_date=min(ds) if ds else None,
                last_prod_date=max(ds) if ds else None,
                ops=op_rows,
            )
        )
    return out


# ---------------- Birlestirme (legacy API -> uretim partisi) ----------------

def merge_suggestions(db: Session) -> list:
    from app.services import production_batches as pb

    return pb.batch_suggestions(db)


def merge_orders(db: Session, req: MergeRequest, username: str) -> Order:
    """Legacy: uretim partisi olusturur; geriye uyum icin anchor siparis dondurur."""
    from app.services import production_batches as pb

    batch = pb.create_batch(
        db,
        ProductionBatchCreate(
            order_ids=req.order_ids,
            batch_no=req.order_no,
            due_date=req.due_date,
            note=req.note or (f"Musteri: {req.customer}" if req.customer else None),
        ),
        username,
    )
    anchor_id = sorted(batch.orders, key=lambda l: l.order_id)[0].order_id
    return db.query(Order).options(joinedload(Order.item)).filter(Order.id == anchor_id).one()


def unmerge_order(db: Session, merged_id: int) -> int:
    """Legacy: merged_id artik uretim partisi id'sidir."""
    from app.services import production_batches as pb

    return pb.dissolve_batch(db, merged_id)
