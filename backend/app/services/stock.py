"""Bitmis urun stogu ve siparise rezervasyon.

Kavramlar (urun bazinda):
  on_hand  = depo girisleri - sevkler
  reserved = acik rezervasyonlar (sevk edilince rezervasyon kapanir, sevk kaydi olusur)
  free     = on_hand - reserved                       -> 'serbest stok'
Siparis bazinda:
  remaining = siparis miktari - rezerve - sevk edilen -> hala karsilanmasi gereken miktar

Otomatik rezervasyon: her urun icin serbest stok, acik siparislere TERMIN sirasiyla dagitilir.
Manuel rezervasyon onceliklidir: serbest stok yetmezse ayni urunun OTOMATIK rezervasyonlari
(en gec terminli siparisten baslayarak) cozulur ve yer acilir. Manuel rezervasyonlara dokunulmaz.
"""

from collections import defaultdict
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, ProductionActual, Reservation, RoutingOperation, Shipment, StockReceipt
from app.schemas import (
    AutoReserveResult,
    OrderStockRow,
    ReceiptOut,
    ReservationOut,
    ShipmentOut,
    StockRow,
)


# ---------------- toplamlar ----------------

def _sum_by_item(db: Session, model, qty_col, item_ids: list[int] | None = None) -> dict[int, float]:
    q = db.query(model.item_id, func.sum(qty_col))
    if item_ids:
        q = q.filter(model.item_id.in_(item_ids))
    return {i: float(s or 0) for i, s in q.group_by(model.item_id).all()}


def _sum_by_order(db: Session, model, order_ids: list[int] | None = None) -> dict[int, float]:
    q = db.query(model.order_id, func.sum(model.quantity))
    if order_ids:
        q = q.filter(model.order_id.in_(order_ids))
    return {i: float(s or 0) for i, s in q.group_by(model.order_id).all()}


def item_free(db: Session, item_id: int) -> float:
    on_hand = (db.query(func.sum(StockReceipt.quantity)).filter(StockReceipt.item_id == item_id).scalar() or 0.0) - (
        db.query(func.sum(Shipment.quantity)).filter(Shipment.item_id == item_id).scalar() or 0.0
    )
    reserved = db.query(func.sum(Reservation.quantity)).filter(Reservation.item_id == item_id).scalar() or 0.0
    return float(on_hand - reserved)


def order_remaining(db: Session, order: Order) -> float:
    reserved = db.query(func.sum(Reservation.quantity)).filter(Reservation.order_id == order.id).scalar() or 0.0
    shipped = db.query(func.sum(Shipment.quantity)).filter(Shipment.order_id == order.id).scalar() or 0.0
    return float(order.quantity - reserved - shipped)


def stock_summary(db: Session, only_with_stock: bool = False) -> list[StockRow]:
    sync_progress_receipts(db)
    receipts = _sum_by_item(db, StockReceipt, StockReceipt.quantity)
    shipped = _sum_by_item(db, Shipment, Shipment.quantity)
    reserved = _sum_by_item(db, Reservation, Reservation.quantity)
    # acik talep: acik siparislerin (rezerve + sevk) dusulmus miktari
    open_orders = db.query(Order).filter(Order.status == "open").all()
    res_by_order = _sum_by_order(db, Reservation)
    ship_by_order = _sum_by_order(db, Shipment)
    demand: dict[int, float] = defaultdict(float)
    n_orders: dict[int, int] = defaultdict(int)
    for o in open_orders:
        rem = o.quantity - res_by_order.get(o.id, 0.0) - ship_by_order.get(o.id, 0.0)
        if rem > 1e-9:
            demand[o.item_id] += rem
            n_orders[o.item_id] += 1
    item_ids = set(receipts) | set(shipped) | set(reserved) | set(demand)
    if not item_ids:
        return []
    items = {i.id: i for i in db.query(Item).filter(Item.id.in_(item_ids)).all()}
    rows = []
    for iid in item_ids:
        it = items.get(iid)
        if not it:
            continue
        on_hand = receipts.get(iid, 0.0) - shipped.get(iid, 0.0)
        res = reserved.get(iid, 0.0)
        if only_with_stock and on_hand <= 1e-9 and res <= 1e-9:
            continue
        rows.append(
            StockRow(
                item_id=iid,
                item_code=it.code,
                item_name=it.name,
                product_group=it.product_group,
                on_hand=round(on_hand, 3),
                reserved=round(res, 3),
                free=round(on_hand - res, 3),
                shipped=round(shipped.get(iid, 0.0), 3),
                open_demand=round(demand.get(iid, 0.0), 3),
                open_orders=n_orders.get(iid, 0),
            )
        )
    rows.sort(key=lambda r: (-r.free, r.item_code))
    return rows


def order_rows(db: Session, item_id: int | None = None, include_closed: bool = False, position: str | None = None) -> list[OrderStockRow]:
    q = db.query(Order).options(joinedload(Order.item))
    if not include_closed:
        q = q.filter(Order.status == "open")
    else:
        q = q.filter(Order.status.in_(["open", "closed"]))
    if item_id:
        q = q.filter(Order.item_id == item_id)
    if position and position.strip():
        q = q.filter(Order.position_no.ilike(f"%{position.strip()}%"))
    orders = q.order_by(Order.due_date, Order.order_no, Order.position_no).all()
    ids = [o.id for o in orders]
    res = _sum_by_order(db, Reservation, ids)
    shp = _sum_by_order(db, Shipment, ids)
    out = []
    for o in orders:
        r, s = res.get(o.id, 0.0), shp.get(o.id, 0.0)
        out.append(
            OrderStockRow(
                order_id=o.id,
                order_no=o.order_no,
                position_no=o.position_no or "",
                customer=o.customer,
                due_date=o.due_date,
                item_id=o.item_id,
                item_code=o.item.code if o.item else "",
                item_name=o.item.name if o.item else "",
                quantity=o.quantity,
                reserved=round(r, 3),
                shipped=round(s, 3),
                remaining=round(o.quantity - r - s, 3),
                status=o.status,
            )
        )
    return out


# ---------------- depo girisi ----------------

def _receipt_out(r: StockReceipt) -> ReceiptOut:
    return ReceiptOut(
        id=r.id, item_id=r.item_id, item_code=r.item.code if r.item else "", item_name=r.item.name if r.item else "",
        receipt_date=r.receipt_date, quantity=r.quantity, lot=r.lot, note=r.note, source=r.source, created_by=r.created_by,
    )


def list_receipts(db: Session, item_id: int | None = None, limit: int = 500) -> list[ReceiptOut]:
    sync_progress_receipts(db)
    q = db.query(StockReceipt).options(joinedload(StockReceipt.item))
    if item_id:
        q = q.filter(StockReceipt.item_id == item_id)
    return [_receipt_out(r) for r in q.order_by(StockReceipt.receipt_date.desc(), StockReceipt.id.desc()).limit(limit).all()]


def add_receipt(db: Session, item_code: str, receipt_date: date, quantity: float, lot: str, note: str, username: str, source: str = "manual") -> StockReceipt:
    item = db.query(Item).filter(Item.code == item_code.strip()).first()
    if not item:
        raise ValueError(f"Stok kodu bulunamadi: {item_code}")
    r = StockReceipt(item_id=item.id, receipt_date=receipt_date, quantity=quantity, lot=lot or "", note=note or "", source=source, created_by=username)
    db.add(r)
    db.flush()
    return r


def delete_receipt(db: Session, receipt_id: int) -> None:
    r = db.get(StockReceipt, receipt_id)
    if not r:
        raise ValueError("Depo girisi bulunamadi")
    if r.source == "progress":
        raise ValueError("Uretim beyanindan otomatik olusan depo girisi silinemez; uretim miktarini guncelleyin veya manuel duzeltme yapin")
    if item_free(db, r.item_id) - r.quantity < -1e-9:
        raise ValueError("Bu giris silinirse serbest stok eksiye duser; once rezervasyonlari kaldirin")
    db.delete(r)


# ---------------- uretim -> depo (otomatik) ----------------

def _operations(db: Session, item_id: int) -> list[RoutingOperation]:
    return (
        db.query(RoutingOperation)
        .filter(RoutingOperation.item_id == item_id)
        .order_by(RoutingOperation.seq)
        .all()
    )


def _op_production_totals(db: Session, item_id: int) -> dict[int, float]:
    """Operasyon bazinda toplam uretim beyani (yarimamul/seq/is merkezi ile eslesir)."""
    from app.services.wip import resolve_wip, wip_index

    ops = _operations(db, item_id)
    if not ops:
        return {}
    totals = {op.id: 0.0 for op in ops}
    item = db.get(Item, item_id)
    wip_idx = wip_index(db)
    for a in db.query(ProductionActual).filter(ProductionActual.item_id == item_id).all():
        op_id = None
        if a.semi_finished_code:
            try:
                op_id = resolve_wip(db, a.semi_finished_code, item.code if item else None, wip_idx).id
            except ValueError:
                pass
        if op_id is None and a.operation_seq is not None:
            op = next((o for o in ops if o.seq == a.operation_seq), None)
            op_id = op.id if op else None
        if op_id is None:
            op = next((o for o in ops if o.work_center_id == a.work_center_id), None)
            op_id = op.id if op else None
        if op_id in totals:
            totals[op_id] += float(a.quantity or 0)
    return totals


def _finished_qty_from_production(db: Session, item_id: int) -> float:
    """Tum operasyonlar zincirle ilerlemeli: bitmis urun = operasyonlarin dar bogaz miktari."""
    ops = _operations(db, item_id)
    if not ops:
        return 0.0
    totals = _op_production_totals(db, item_id)
    qtys = [totals.get(op.id, 0.0) for op in ops]
    return min(qtys) if qtys else 0.0


def _progress_receipt_qty(db: Session, item_id: int) -> float:
    return float(
        db.query(func.sum(StockReceipt.quantity))
        .filter(StockReceipt.item_id == item_id, StockReceipt.source == "progress")
        .scalar()
        or 0.0
    )


def _trim_progress_receipts(db: Session, item_id: int, amount: float) -> None:
    """Uretim duzeltmesinde fazla otomatik depo girisini geri alir (rezervasyonu bozmaz)."""
    if amount <= 1e-9:
        return
    on_hand = (
        db.query(func.sum(StockReceipt.quantity)).filter(StockReceipt.item_id == item_id).scalar() or 0.0
    ) - (db.query(func.sum(Shipment.quantity)).filter(Shipment.item_id == item_id).scalar() or 0.0)
    reserved = db.query(func.sum(Reservation.quantity)).filter(Reservation.item_id == item_id).scalar() or 0.0
    max_trim = min(amount, _progress_receipt_qty(db, item_id), max(on_hand - reserved, 0.0))
    if max_trim <= 1e-9:
        return
    rows = (
        db.query(StockReceipt)
        .filter(StockReceipt.item_id == item_id, StockReceipt.source == "progress")
        .order_by(StockReceipt.receipt_date.desc(), StockReceipt.id.desc())
        .all()
    )
    left = max_trim
    for r in rows:
        if left <= 1e-9:
            break
        take = min(r.quantity, left)
        if take >= r.quantity - 1e-9:
            db.delete(r)
        else:
            r.quantity = round(r.quantity - take, 3)
        left -= take
    db.flush()


def sync_progress_receipts(db: Session, item_ids: list[int] | None = None, username: str = "system") -> dict:
    """Rota zincirindeki operasyon beyanlarinin dar bogazini bitmis urun depo girisine yansitir."""
    if item_ids:
        ids = list(item_ids)
    else:
        ids = [i for (i,) in db.query(ProductionActual.item_id).distinct().all()]
    added = adjusted = 0
    for iid in ids:
        if not _operations(db, iid):
            continue
        target = _finished_qty_from_production(db, iid)
        current = _progress_receipt_qty(db, iid)
        delta = round(target - current, 3)
        if abs(delta) < 1e-9:
            continue
        if delta > 0:
            db.add(
                StockReceipt(
                    item_id=iid,
                    receipt_date=date.today(),
                    quantity=delta,
                    lot="",
                    note="otomatik: tum operasyonlar tamam (dar bogaz)",
                    source="progress",
                    created_by=username,
                )
            )
            added += 1
        else:
            _trim_progress_receipts(db, iid, -delta)
            adjusted += 1
        db.flush()
    return {"added": added, "adjusted": adjusted}


# ---------------- rezervasyon ----------------

def _res_out(r: Reservation) -> ReservationOut:
    o = r.order
    return ReservationOut(
        id=r.id, item_id=r.item_id, item_code=r.item.code if r.item else "", item_name=r.item.name if r.item else "",
        order_id=r.order_id, order_no=o.order_no if o else "", position_no=(o.position_no or "") if o else "", customer=o.customer if o else "", due_date=o.due_date if o else date.today(),
        order_qty=o.quantity if o else 0.0, quantity=r.quantity, source=r.source, note=r.note, created_by=r.created_by, created_at=r.created_at,
    )


def list_reservations(db: Session, item_id: int | None = None, order_id: int | None = None, position: str | None = None) -> list[ReservationOut]:
    q = db.query(Reservation).options(joinedload(Reservation.item), joinedload(Reservation.order))
    if item_id:
        q = q.filter(Reservation.item_id == item_id)
    if order_id:
        q = q.filter(Reservation.order_id == order_id)
    rows = q.all()
    if position and position.strip():
        p = position.strip().lower()
        rows = [r for r in rows if r.order and p in (r.order.position_no or "").lower()]
    rows.sort(key=lambda r: (r.order.due_date if r.order else date.max, r.item.code if r.item else "", r.id))
    return [_res_out(r) for r in rows]


def _release_auto_for_room(db: Session, item_id: int, needed: float) -> float:
    """Manuel rezervasyona yer acmak icin otomatik rezervasyonlari (en gec terminden) cozer; acilan miktari dondurur."""
    autos = (
        db.query(Reservation).options(joinedload(Reservation.order))
        .filter(Reservation.item_id == item_id, Reservation.source == "auto")
        .all()
    )
    autos.sort(key=lambda r: (r.order.due_date if r.order else date.min, r.id), reverse=True)
    freed = 0.0
    for r in autos:
        if freed >= needed - 1e-9:
            break
        take = min(r.quantity, needed - freed)
        if take >= r.quantity - 1e-9:
            db.delete(r)
        else:
            r.quantity = round(r.quantity - take, 3)
        freed += take
    db.flush()
    return freed


def reserve_manual(db: Session, item_id: int | None, item_code: str | None, order_id: int, quantity: float, note: str, username: str) -> Reservation:
    order = db.query(Order).options(joinedload(Order.item)).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Siparis bulunamadi")
    if item_id is None:
        if item_code:
            it = db.query(Item).filter(Item.code == item_code.strip()).first()
            if not it:
                raise ValueError("Stok kodu bulunamadi")
            item_id = it.id
        else:
            item_id = order.item_id
    if item_id != order.item_id:
        raise ValueError(f"Siparisin urunu ({order.item.code}) ile rezerve edilen urun farkli")
    if order.status != "open":
        raise ValueError("Yalnizca acik siparislere rezervasyon yapilabilir")
    remaining = order_remaining(db, order)
    if quantity > remaining + 1e-9:
        raise ValueError(f"Siparisin kalan ihtiyaci {remaining:g}; daha fazlasi rezerve edilemez")
    free = item_free(db, item_id)
    if quantity > free + 1e-9:
        # manuel oncelikli: otomatik rezervasyonlari coz
        freed = _release_auto_for_room(db, item_id, quantity - free)
        free += freed
        if quantity > free + 1e-9:
            raise ValueError(f"Serbest stok yetersiz (serbest {free:g}, istenen {quantity:g}); manuel rezervasyonlar cozulmez")
    r = Reservation(item_id=item_id, order_id=order_id, quantity=round(quantity, 3), source="manual", note=note or "", created_by=username)
    db.add(r)
    db.flush()
    return r


def release(db: Session, reservation_id: int) -> None:
    r = db.get(Reservation, reservation_id)
    if not r:
        raise ValueError("Rezervasyon bulunamadi")
    db.delete(r)


def move(db: Session, reservation_id: int, new_order_id: int, username: str) -> Reservation:
    """Rezervasyonu baska bir siparise tasir (manuel karar); tasinan rezervasyon 'manual' olur."""
    r = db.get(Reservation, reservation_id)
    if not r:
        raise ValueError("Rezervasyon bulunamadi")
    target = db.get(Order, new_order_id)
    if not target or target.status != "open":
        raise ValueError("Hedef siparis bulunamadi veya acik degil")
    if target.item_id != r.item_id:
        raise ValueError("Hedef siparisin urunu farkli")
    rem = order_remaining(db, target)
    if r.quantity > rem + 1e-9:
        raise ValueError(f"Hedef siparisin kalan ihtiyaci {rem:g}; rezervasyon miktari ({r.quantity:g}) sigmiyor. Once rezervasyonu bolun/kaldirin.")
    r.order_id = new_order_id
    r.source = "manual"
    r.created_by = username
    db.flush()
    return r


def auto_reserve(db: Session, item_ids: list[int] | None, username: str) -> AutoReserveResult:
    rows = stock_summary(db)
    if item_ids:
        rows = [r for r in rows if r.item_id in item_ids]
    created = 0
    total = 0.0
    touched = 0
    for row in rows:
        free = row.free
        if free <= 1e-9:
            continue
        orders = (
            db.query(Order).filter(Order.item_id == row.item_id, Order.status == "open").order_by(Order.due_date, Order.order_no, Order.id).all()
        )
        used_any = False
        for o in orders:
            if free <= 1e-9:
                break
            rem = order_remaining(db, o)
            if rem <= 1e-9:
                continue
            take = min(rem, free)
            db.add(Reservation(item_id=row.item_id, order_id=o.id, quantity=round(take, 3), source="auto", note="otomatik (termin sirasi)", created_by=username))
            db.flush()
            free -= take
            total += take
            created += 1
            used_any = True
        if used_any:
            touched += 1
    return AutoReserveResult(created=created, reserved_qty=round(total, 3), items=touched, message=f"{created} rezervasyon olusturuldu ({total:g} adet, {touched} urun).")


# ---------------- sevk ----------------

def _ship_out(s: Shipment) -> ShipmentOut:
    o = s.order
    return ShipmentOut(
        id=s.id, item_id=s.item_id, item_code=s.item.code if s.item else "", order_id=s.order_id, order_no=o.order_no if o else "", position_no=(o.position_no or "") if o else "",
        customer=o.customer if o else "", ship_date=s.ship_date, quantity=s.quantity, note=s.note, created_by=s.created_by,
    )


def list_shipments(db: Session, item_id: int | None = None, order_id: int | None = None, position: str | None = None, limit: int = 500) -> list[ShipmentOut]:
    q = db.query(Shipment).options(joinedload(Shipment.item), joinedload(Shipment.order))
    if item_id:
        q = q.filter(Shipment.item_id == item_id)
    if order_id:
        q = q.filter(Shipment.order_id == order_id)
    rows = q.order_by(Shipment.ship_date.desc(), Shipment.id.desc()).limit(limit).all()
    if position and position.strip():
        p = position.strip().lower()
        rows = [s for s in rows if s.order and p in (s.order.position_no or "").lower()]
    return [_ship_out(s) for s in rows]


def ship_reservation(db: Session, reservation_id: int, quantity: float | None, ship_date: date | None, note: str, username: str) -> Shipment:
    """Rezervasyonun tamamini/kismini sevk eder: stok duser, rezervasyon kapanir/azalir,
    siparisin tamami sevk edildiyse siparis 'closed' olur."""
    r = db.query(Reservation).options(joinedload(Reservation.order)).filter(Reservation.id == reservation_id).first()
    if not r:
        raise ValueError("Rezervasyon bulunamadi")
    qty = r.quantity if quantity is None else float(quantity)
    if qty <= 0 or qty > r.quantity + 1e-9:
        raise ValueError(f"Sevk miktari 0 ile {r.quantity:g} arasinda olmali")
    s = Shipment(item_id=r.item_id, order_id=r.order_id, ship_date=ship_date or date.today(), quantity=round(qty, 3), note=note or "", created_by=username)
    db.add(s)
    if qty >= r.quantity - 1e-9:
        db.delete(r)
    else:
        r.quantity = round(r.quantity - qty, 3)
    db.flush()
    order = db.get(Order, s.order_id)
    if order is not None:
        shipped = db.query(func.sum(Shipment.quantity)).filter(Shipment.order_id == order.id).scalar() or 0.0
        if shipped >= order.quantity - 1e-9 and order.status == "open":
            order.status = "closed"
            order.note = (order.note + " | " if order.note else "") + f"tamami sevk edildi {s.ship_date}"
    db.flush()
    return s


def undo_shipment(db: Session, shipment_id: int, username: str) -> None:
    """Sevki geri alir: stok geri gelir ve ayni siparise manuel rezervasyon olarak baglanir; kapanmis siparis acilir."""
    s = db.get(Shipment, shipment_id)
    if not s:
        raise ValueError("Sevk kaydi bulunamadi")
    order = db.get(Order, s.order_id)
    if order is not None and order.status == "closed":
        order.status = "open"
    db.add(Reservation(item_id=s.item_id, order_id=s.order_id, quantity=s.quantity, source="manual", note="sevk geri alindi", created_by=username))
    db.delete(s)
    db.flush()
