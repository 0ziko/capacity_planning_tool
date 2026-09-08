"""Uretim partisi: ayni stok kodundan birden fazla siparis icin birlestirilmis uretim plani.

Siparisler her zaman acik ve ayri kalir; plan satirlari partiye baglanir.
Bitmis urun stogu ve rezervasyon siparis bazinda kalir (stock servisi).
"""

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, ProductionActual, ProductionBatch, ProductionBatchOrder
from app.schemas import (
    ProductionBatchCreate,
    ProductionBatchOrderOut,
    ProductionBatchOut,
    ProductionBatchSuggestion,
)
from app.services.orders import order_out


def batched_order_ids(db: Session) -> set[int]:
    return {oid for (oid,) in db.query(ProductionBatchOrder.order_id).join(ProductionBatch).filter(ProductionBatch.status == "open").all()}


def batch_out(b: ProductionBatch) -> ProductionBatchOut:
    links = sorted(b.orders, key=lambda x: (x.order.due_date if x.order else date.max, x.order_id))
    order_rows = []
    for link in links:
        o = link.order
        if not o:
            continue
        row = order_out(o)
        order_rows.append(ProductionBatchOrderOut(order_id=o.id, order_no=o.order_no, position_no=o.position_no or "", customer=o.customer, due_date=o.due_date, quantity=link.quantity))
    return ProductionBatchOut(
        id=b.id,
        batch_no=b.batch_no,
        item_id=b.item_id,
        item_code=b.item.code if b.item else "",
        item_name=b.item.name if b.item else "",
        due_date=b.due_date,
        quantity=b.quantity,
        note=b.note,
        status=b.status,
        orders=order_rows,
    )


def list_batches(db: Session, status: str = "open") -> list[ProductionBatchOut]:
    q = db.query(ProductionBatch).options(joinedload(ProductionBatch.item), joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order))
    if status:
        q = q.filter(ProductionBatch.status == status)
    rows = q.order_by(ProductionBatch.due_date, ProductionBatch.batch_no).all()
    return [batch_out(b) for b in rows]


def batch_suggestions(db: Session) -> list[ProductionBatchSuggestion]:
    """Ayni stok kodunda birden fazla acik siparis (partide olmayan) -> uretim birlestirme onerisi."""
    in_batch = batched_order_ids(db)
    orders = (
        db.query(Order)
        .options(joinedload(Order.item))
        .filter(Order.status == "open")
        .order_by(Order.due_date, Order.order_no, Order.position_no)
        .all()
    )
    groups: dict[int, list[Order]] = defaultdict(list)
    for o in orders:
        if o.id in in_batch:
            continue
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
            ProductionBatchSuggestion(
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


def create_batch(db: Session, req: ProductionBatchCreate, username: str) -> ProductionBatch:
    ids = list(dict.fromkeys(req.order_ids))
    orders = db.query(Order).options(joinedload(Order.item)).filter(Order.id.in_(ids)).all()
    if len(orders) != len(ids):
        raise ValueError("Siparislerden bazilari bulunamadi")
    if len(orders) < 2:
        raise ValueError("En az iki siparis secilmeli")
    if any(o.status != "open" for o in orders):
        raise ValueError("Yalnizca acik siparisler uretim partisine alinabilir")
    item_ids = {o.item_id for o in orders}
    if len(item_ids) != 1:
        raise ValueError("Partideki siparislerin stok kodu ayni olmali")
    item = orders[0].item
    in_batch = batched_order_ids(db)
    if any(o.id in in_batch for o in orders):
        raise ValueError("Secilen siparislerden biri zaten baska bir uretim partisinde")
    orders.sort(key=lambda o: (o.due_date, o.order_no, o.position_no))
    due = req.due_date or min(o.due_date for o in orders)
    batch_no = (req.batch_no or "").strip() or f"URT-{item.code}-{due.strftime('%Y%m%d')}"
    if db.query(ProductionBatch).filter(ProductionBatch.batch_no.ilike(batch_no), ProductionBatch.item_id == item.id, ProductionBatch.status == "open").first():
        raise ValueError(f"{batch_no} numarali uretim partisi zaten var; farkli bir part no verin")
    total_qty = round(sum(o.quantity for o in orders), 3)
    note = (req.note or "").strip() or "Uretim partisi: " + ", ".join(f"{o.order_no}{f'/{o.position_no}' if o.position_no else ''} ({o.quantity:g})" for o in orders) + f" — {username}"
    batch = ProductionBatch(batch_no=batch_no, item_id=item.id, due_date=due, quantity=total_qty, note=note, status="open", created_by=username)
    db.add(batch)
    db.flush()
    anchor = orders[0].id
    for o in orders:
        db.add(ProductionBatchOrder(batch_id=batch.id, order_id=o.id, quantity=o.quantity))
        if o.id != anchor:
            db.query(PlanLine).filter(PlanLine.order_id == o.id, PlanLine.production_batch_id.is_(None)).delete(synchronize_session=False)
        else:
            db.query(PlanLine).filter(PlanLine.order_id == o.id, PlanLine.production_batch_id.is_(None)).delete(synchronize_session=False)
    db.commit()
    return (
        db.query(ProductionBatch)
        .options(joinedload(ProductionBatch.item), joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order))
        .filter(ProductionBatch.id == batch.id)
        .one()
    )


def dissolve_batch(db: Session, batch_id: int) -> int:
    batch = (
        db.query(ProductionBatch)
        .options(joinedload(ProductionBatch.orders))
        .filter(ProductionBatch.id == batch_id)
        .first()
    )
    if not batch:
        raise ValueError("Uretim partisi bulunamadi")
    n = len(batch.orders)
    db.delete(batch)  # plan satirlari ve baglantilar cascade
    db.commit()
    return n


def migrate_legacy_merged_orders(db: Session) -> int:
    """Eski birlesik siparis kayitlarini uretim partisine donusturur; kaynak siparisleri yeniden acar."""
    converted = 0
    merged_orders = (
        db.query(Order)
        .options(joinedload(Order.item))
        .filter(Order.status == "open")
        .all()
    )
    for mo in merged_orders:
        sources = db.query(Order).options(joinedload(Order.item)).filter(Order.merged_into_id == mo.id).all()
        if not sources:
            continue
        batch = ProductionBatch(
            batch_no=mo.order_no,
            item_id=mo.item_id,
            due_date=mo.due_date,
            quantity=mo.quantity,
            note=mo.note or "Legacy birlestirme -> uretim partisi",
            status="open",
            created_by="migrate",
        )
        db.add(batch)
        db.flush()
        for s in sources:
            s.status = "open"
            s.merged_into_id = None
            db.add(ProductionBatchOrder(batch_id=batch.id, order_id=s.id, quantity=s.quantity))
        for pl in db.query(PlanLine).filter(PlanLine.order_id == mo.id).all():
            pl.production_batch_id = batch.id
            pl.order_id = sources[0].id
        db.delete(mo)
        converted += 1
    if converted:
        db.commit()
    return converted


def open_batches_with_ops(db: Session) -> list[ProductionBatch]:
    return (
        db.query(ProductionBatch)
        .options(joinedload(ProductionBatch.item).joinedload(Item.operations), joinedload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order))
        .filter(ProductionBatch.status == "open")
        .order_by(ProductionBatch.due_date, ProductionBatch.batch_no)
        .all()
    )


def batch_order_map(db: Session) -> dict[int, ProductionBatch]:
    """order_id -> batch"""
    rows = (
        db.query(ProductionBatchOrder, ProductionBatch)
        .join(ProductionBatch)
        .filter(ProductionBatch.status == "open")
        .all()
    )
    return {link.order_id: batch for link, batch in rows}
