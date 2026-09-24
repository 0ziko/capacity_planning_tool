"""Request-local bulk inputs; discarded after both read-only merge simulations."""
from collections import defaultdict
from contextlib import contextmanager
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, func
from app.models import Item, Order, ProductionBatch, PlanLine, Shipment, Reservation
from app.services.bom_tree import is_wip_asm_link


@contextmanager
def read_snapshot(db):
    key = "_merge_read_snapshot"
    if key in db.info:
        yield
        return
    items = db.query(Item).options(selectinload(Item.operations), selectinload(Item.bom_lines)).filter(or_(
        Item.id.in_(db.query(Order.item_id).filter(Order.status == "open")),
        Item.id.in_(db.query(ProductionBatch.item_id).filter(ProductionBatch.status == "open")),
    )).all()
    codes = {b.component_code.strip().upper() for i in items for b in i.bom_lines
             if is_wip_asm_link(b.component_code, b.source_wip, b.recipe_seq)}
    if codes:
        items += db.query(Item).options(selectinload(Item.operations), selectinload(Item.bom_lines)).filter(func.upper(Item.code).in_(codes)).all()
    by_order, by_batch, reservations = (defaultdict(list) for _ in range(3))
    shipped = defaultdict(float)
    for p in db.query(PlanLine).filter(PlanLine.mode.in_(["auto", "manual"])).all():
        by_order[p.order_id].append(p)
        by_batch[p.production_batch_id].append(p)
    for s in db.query(Shipment).all():
        shipped[s.order_id] += s.quantity
    for r in db.query(Reservation).all():
        reservations[r.order_id].append(r)
    db.info[key] = dict(items={i.code.upper(): i for i in items}, by_order=by_order,
                        by_batch=by_batch, shipped=shipped, reservations=reservations)
    try:
        yield
    finally:
        db.info.pop(key, None)
