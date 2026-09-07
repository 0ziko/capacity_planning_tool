"""Siparis odakli servisler: tekil CRUD, plan sonucu bitis tarihleri,
siparis bazli ilerleme ve ayni stok kodlu siparisleri birlestirme."""

from collections import defaultdict
from datetime import date, timedelta
from math import ceil

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionActual, RoutingOperation, WorkCenter
from app.schemas import (
    MergeGroup,
    MergeRequest,
    OrderIn,
    OrderOut,
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
        .order_by(Order.due_date, Order.order_no, Order.id)
        .all()
    )


# ---------------- Tekil siparis CRUD ----------------

def create_order(db: Session, data: OrderIn) -> Order:
    item = db.query(Item).filter(Item.code == data.item_code.strip()).first()
    if not item:
        item = db.query(Item).filter(Item.code.ilike(data.item_code.strip())).first()
    if not item:
        raise ValueError(f"Stok kodu bulunamadi: {data.item_code} (once Stok/BOM/Rota ekranindan tanimlayin)")
    dup = db.query(Order).filter(Order.order_no.ilike(data.order_no.strip()), Order.item_id == item.id).first()
    if dup:
        raise ValueError(f"{data.order_no} / {item.code} siparisi zaten var (id={dup.id}); duzenlemek icin mevcut satiri kullanin")
    o = Order(
        order_no=data.order_no.strip(),
        customer=data.customer.strip(),
        due_date=data.due_date,
        item_id=item.id,
        quantity=data.quantity,
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
    o.customer = data.customer.strip()
    o.due_date = data.due_date
    o.item_id = item.id
    o.quantity = data.quantity
    o.note = data.note.strip()
    if item_changed:
        # rota degisti; eski plan satirlari gecersiz
        db.query(PlanLine).filter(PlanLine.order_id == o.id).delete(synchronize_session=False)
    db.commit()
    db.refresh(o)
    return o


# ---------------- Plan sonucu: siparis bitis tarihleri ----------------

def _end_day_in_week(db: Session, wc: WorkCenter, wk: date, order_id: int, lines_in_week: list[PlanLine]) -> date:
    """Haftadaki plan satirlari (termin sirasiyla) kumulatif doldurulur; bu siparisin
    payi bittigi noktadaki calisma gununu dondurur."""
    wdays = cap.working_days(wc, wk, wk + timedelta(days=6))
    if not wdays:
        return wk + timedelta(days=4)
    capacity = cap.week_capacity_hours(db, wc, wk)
    ordered = sorted(lines_in_week, key=lambda p: (p.order.due_date, p.order.order_no, p.order_id, p.id))
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


def order_schedule(db: Session, wc_ids: list[int] | None) -> list[OrderScheduleOut]:
    wcs = _planned_wcs(db, wc_ids)
    wc_by_id = {w.id: w for w in wcs}
    orders = _open_orders(db)
    if not orders:
        return []
    lines = (
        db.query(PlanLine)
        .options(joinedload(PlanLine.order))
        .filter(PlanLine.order_id.in_([o.id for o in orders]))
        .all()
    )
    if wc_by_id:
        lines = [p for p in lines if p.work_center_id in wc_by_id]
    by_order: dict[int, list[PlanLine]] = defaultdict(list)
    by_wc_week: dict[tuple[int, date], list[PlanLine]] = defaultdict(list)
    for p in lines:
        by_order[p.order_id].append(p)
        by_wc_week[(p.work_center_id, p.week_start)].append(p)

    out: list[OrderScheduleOut] = []
    for o in orders:
        ops = [op for op in o.item.operations if op.work_center_id in wc_by_id]
        required = sum(op.hours_for(o.quantity) for op in ops)
        pls = by_order.get(o.id, [])
        planned = sum(p.planned_hours for p in pls)
        start = min((p.week_start for p in pls), default=None)
        end_week = max((p.week_start for p in pls), default=None)
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
                d = _end_day_in_week(db, wc, end_week, o.id, by_wc_week[(wc.id, end_week)])
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
                customer=o.customer,
                item_code=o.item.code,
                item_name=o.item.name,
                quantity=o.quantity,
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

    # operasyon anahtari: (item_id, seq) ; seq yoksa is merkezine gore ilk operasyon
    def op_key(item: Item, seq: int | None, wc_id: int) -> int | None:
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
        op_id = op_key(item, a.operation_seq, a.work_center_id)
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
                    work_center_code=op.work_center.code,
                    required_hours=round(req, 2),
                    planned_hours=round(planned.get((o.id, op.id), 0.0), 2),
                    produced_qty=round(pq, 2),
                    earned_hours=round(eh, 2),
                    pct=round(min(pq / o.quantity * 100, 100.0), 1) if o.quantity else 0.0,
                )
            )
        final_qty = op_rows[-1].produced_qty if op_rows else 0.0
        pct = min(earned_total / req_total * 100, 100.0) if req_total > 0 else 0.0
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


# ---------------- Birlestirme ----------------

def merge_suggestions(db: Session) -> list[MergeGroup]:
    orders = _open_orders(db)
    groups: dict[int, list[Order]] = defaultdict(list)
    for o in orders:
        groups[o.item_id].append(o)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    if not multi:
        return []
    nos = {o.order_no.upper() for v in multi.values() for o in v}
    with_prog = set()
    for order_no, item_id in db.query(ProductionActual.order_no, ProductionActual.item_id).filter(ProductionActual.item_id.in_(multi.keys())).distinct().all():
        if (order_no or "").upper() in nos:
            with_prog.add(((order_no or "").upper(), item_id))
    out = []
    for item_id, lst in multi.items():
        item = lst[0].item
        customers = []
        for o in lst:
            if o.customer and o.customer not in customers:
                customers.append(o.customer)
        out.append(
            MergeGroup(
                item_id=item_id,
                item_code=item.code,
                item_name=item.name,
                order_count=len(lst),
                total_qty=round(sum(o.quantity for o in lst), 2),
                earliest_due=min(o.due_date for o in lst),
                latest_due=max(o.due_date for o in lst),
                customers=customers,
                has_progress=any((o.order_no.upper(), item_id) in with_prog for o in lst),
                orders=[order_out(o) for o in lst],
            )
        )
    out.sort(key=lambda g: (g.earliest_due, g.item_code))
    return out


def merge_orders(db: Session, req: MergeRequest, username: str) -> Order:
    ids = list(dict.fromkeys(req.order_ids))
    orders = db.query(Order).options(joinedload(Order.item)).filter(Order.id.in_(ids)).all()
    if len(orders) != len(ids):
        raise ValueError("Siparislerden bazilari bulunamadi")
    if len(orders) < 2:
        raise ValueError("En az iki siparis secilmeli")
    if any(o.status != "open" for o in orders):
        raise ValueError("Yalnizca acik siparisler birlestirilebilir")
    item_ids = {o.item_id for o in orders}
    if len(item_ids) != 1:
        raise ValueError("Birlestirilecek siparislerin stok kodu ayni olmali")
    item = orders[0].item
    orders.sort(key=lambda o: (o.due_date, o.order_no))
    due = req.due_date or min(o.due_date for o in orders)
    customers = []
    for o in orders:
        if o.customer and o.customer not in customers:
            customers.append(o.customer)
    order_no = (req.order_no or "").strip() or f"BRL-{item.code}-{due.strftime('%Y%m%d')}"
    if db.query(Order).filter(Order.order_no.ilike(order_no), Order.item_id == item.id, Order.status != "merged").first():
        raise ValueError(f"{order_no} numarali siparis zaten var; farkli bir birlesik siparis no verin")
    merged = Order(
        order_no=order_no,
        customer=(req.customer or "").strip() or " + ".join(customers),
        due_date=due,
        item_id=item.id,
        quantity=round(sum(o.quantity for o in orders), 3),
        status="open",
        note="Birlestirildi: " + ", ".join(f"{o.order_no} ({o.quantity:g})" for o in orders) + f" — {username}",
    )
    db.add(merged)
    db.flush()
    for o in orders:
        o.status = "merged"
        o.merged_into_id = merged.id
        db.query(PlanLine).filter(PlanLine.order_id == o.id).delete(synchronize_session=False)
    db.commit()
    db.refresh(merged)
    return merged


def unmerge_order(db: Session, merged_id: int) -> int:
    merged = db.get(Order, merged_id)
    if not merged:
        raise ValueError("Birlesik siparis bulunamadi")
    sources = db.query(Order).filter(Order.merged_into_id == merged_id).all()
    if not sources:
        raise ValueError("Bu siparis bir birlestirme sonucu degil")
    for o in sources:
        o.status = "open"
        o.merged_into_id = None
    db.delete(merged)  # plan satirlari cascade ile silinir
    db.commit()
    return len(sources)
