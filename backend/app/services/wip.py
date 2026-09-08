"""Yarimamul kodu (operasyon cikisi) cozumleme, plan oncelikli dagitim."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, ProductionActual, RoutingOperation, norm_wip


def wip_index(db: Session) -> dict[str, list[RoutingOperation]]:
    idx: dict[str, list[RoutingOperation]] = {}
    rows = (
        db.query(RoutingOperation)
        .options(joinedload(RoutingOperation.item), joinedload(RoutingOperation.work_center))
        .filter(RoutingOperation.semi_finished_code != "")
        .all()
    )
    for op in rows:
        k = norm_wip(op.semi_finished_code)
        if k:
            idx.setdefault(k, []).append(op)
    return idx


def _filter_ops(
    ops: list[RoutingOperation],
    *,
    item_code: str | None,
    wc_code: str | None,
    op_seq: int | None,
    wcs: dict[str, object] | None,
) -> list[RoutingOperation]:
    out = ops
    if item_code:
        hint = item_code.strip().upper()
        out = [o for o in out if o.item.code.upper() == hint]
    if wc_code and wcs:
        wc = wcs.get(wc_code.strip().upper())
        if wc:
            out = [o for o in out if o.work_center_id == wc.id]
    if op_seq is not None:
        out = [o for o in out if o.seq == op_seq]
    return out


def plan_ordered_ops(db: Session, ops: list[RoutingOperation], *, order_no: str | None = None) -> list[RoutingOperation]:
    """Acik siparis termin onceligine gore operasyonlari siralar (erken termin once)."""
    if not ops:
        return []
    item_ids = {o.item_id for o in ops}
    q = (
        db.query(Order)
        .filter(Order.status == "open", Order.item_id.in_(item_ids))
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
    )
    if order_no and order_no.strip():
        q = q.filter(Order.order_no == order_no.strip())
    orders = q.all()
    rank: dict[int, int] = {}
    for i, o in enumerate(orders):
        if o.item_id not in rank:
            rank[o.item_id] = i
    return sorted(ops, key=lambda o: (rank.get(o.item_id, 10_000), o.item.code, o.seq))


def _produced_qty_map(db: Session, *, as_of: date | None = None) -> dict[tuple[int, int], float]:
    """(order_id, operation_id) -> uretilen miktar."""
    out: dict[tuple[int, int], float] = defaultdict(float)
    q = db.query(ProductionActual)
    if as_of:
        q = q.filter(ProductionActual.prod_date <= as_of)
    actuals = q.order_by(ProductionActual.prod_date, ProductionActual.id).all()
    if not actuals:
        return out

    idx = wip_index(db)
    open_orders = (
        db.query(Order)
        .options(joinedload(Order.item).joinedload(Item.operations))
        .filter(Order.status == "open")
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
        .all()
    )
    orders_by_no = {(o.order_no.upper(), o.item_id): o for o in open_orders}
    orders_by_item: dict[int, list[Order]] = defaultdict(list)
    for o in open_orders:
        orders_by_item[o.item_id].append(o)

    def op_id_for(item, seq, wc_id, wip_code):
        if not item:
            return None
        if wip_code:
            try:
                return resolve_wip(db, wip_code, item.code, idx).id
            except ValueError:
                pass
        if seq is not None:
            op = next((x for x in item.operations if x.seq == seq), None)
            return op.id if op else None
        op = next((x for x in item.operations if x.work_center_id == wc_id), None)
        return op.id if op else None

    fifo_left: dict[tuple[int, int], float] = {}
    for a in actuals:
        item = a.item
        if not item:
            continue
        op_id = a.operation_seq and next((x.id for x in item.operations if x.seq == a.operation_seq), None)
        if op_id is None:
            op_id = op_id_for(item, a.operation_seq, a.work_center_id, a.semi_finished_code or "")
        if op_id is None:
            continue
        if a.order_no:
            target = orders_by_no.get((a.order_no.upper(), a.item_id))
            if target is not None:
                out[(target.id, op_id)] += a.quantity
            continue
        qty_left = a.quantity
        for o in orders_by_item.get(a.item_id, []):
            key = (o.id, op_id)
            if key not in fifo_left:
                fifo_left[key] = max(o.quantity - out[key], 0.0)
            room = fifo_left[key]
            if room <= 1e-9:
                continue
            take = min(room, qty_left)
            out[key] += take
            fifo_left[key] = room - take
            qty_left -= take
            if qty_left <= 1e-9:
                break
        if qty_left > 1e-9 and orders_by_item.get(a.item_id):
            last = orders_by_item[a.item_id][-1]
            out[(last.id, op_id)] += qty_left

    return dict(out)


def _allocation_targets(
    db: Session,
    ops: list[RoutingOperation],
    quantity: float,
    order_no: str,
) -> list[tuple[RoutingOperation, Order | None, float]]:
    """Plan termin onceligine gore uretim miktarini operasyon/siparis dilimlerine ayirir."""
    if quantity <= 0:
        return []
    produced = _produced_qty_map(db)
    slices: list[tuple[RoutingOperation, Order | None, float]] = []
    qty_left = quantity

    item_ids = {o.item_id for o in ops}
    q = (
        db.query(Order)
        .options(joinedload(Order.item))
        .filter(Order.status == "open", Order.item_id.in_(item_ids))
        .order_by(func.coalesce(Order.revised_due_date, Order.due_date), Order.order_no, Order.id)
    )
    if order_no.strip():
        q = q.filter(Order.order_no == order_no.strip())
    orders = q.all()

    ops_by_item = {o.item_id: o for o in plan_ordered_ops(db, ops, order_no=order_no or None)}

    for ord_row in orders:
        if qty_left <= 1e-9:
            break
        op = ops_by_item.get(ord_row.item_id)
        if not op:
            continue
        room = max(ord_row.quantity - produced.get((ord_row.id, op.id), 0.0), 0.0)
        if room <= 1e-9:
            continue
        take = min(room, qty_left)
        slices.append((op, ord_row, take))
        qty_left -= take

    if qty_left > 1e-9:
        op = plan_ordered_ops(db, ops, order_no=order_no or None)[0]
        ord_match = next((o for o in orders if o.item_id == op.item_id), None)
        slices.append((op, ord_match, qty_left))

    if not slices and ops:
        op = plan_ordered_ops(db, ops)[0]
        slices.append((op, None, quantity))
    return slices


def resolve_wip(
    db: Session,
    wip_code: str,
    item_code: str | None = None,
    index: dict[str, list[RoutingOperation]] | None = None,
    *,
    order_no: str | None = None,
    wc_code: str | None = None,
    operation_seq: int | None = None,
    wcs: dict | None = None,
) -> RoutingOperation:
    k = norm_wip(wip_code)
    if not k:
        raise ValueError("Yarimamul kodu bos")
    idx = index if index is not None else wip_index(db)
    ops = idx.get(k, [])
    if not ops:
        raise ValueError(f"Yarimamul kodu bulunamadi: {wip_code}")

    if order_no and order_no.strip() and not item_code:
        on = order_no.strip()
        ord_rows = db.query(Order).filter(Order.status == "open", Order.order_no == on).all()
        item_codes = {o.item.code.upper() for o in ord_rows if o.item}
        if len(item_codes) == 1:
            item_code = next(iter(item_codes))

    narrowed = _filter_ops(ops, item_code=item_code, wc_code=wc_code, op_seq=operation_seq, wcs=wcs)
    if len(narrowed) == 1:
        return narrowed[0]
    if len(narrowed) > 1:
        return plan_ordered_ops(db, narrowed, order_no=order_no)[0]

    if item_code:
        raise ValueError(f"Yarimamul kodu {wip_code} bu stok kodu ile eslesmiyor: {item_code}")

    return plan_ordered_ops(db, ops, order_no=order_no)[0]


def import_wip_production_row(
    db: Session,
    *,
    prod_date: date,
    wip_raw: str,
    quantity: float,
    order_no: str,
    item_code: str | None,
    wc_code: str | None,
    operation_seq: int | None,
    reported_hours: float | None,
    wip_idx: dict[str, list[RoutingOperation]],
    wcs: dict,
) -> tuple[int, int]:
    """Tek import satirini plan oncelikli dilimlere ayirip yazar. (ins, upd) doner."""
    k = norm_wip(wip_raw)
    ops = wip_idx.get(k, [])
    if not ops:
        raise ValueError(f"Yarimamul kodu bulunamadi: {wip_raw}")

    narrowed = _filter_ops(ops, item_code=item_code, wc_code=wc_code, op_seq=operation_seq, wcs=wcs)
    if len(narrowed) == 1:
        targets = [(narrowed[0], None, quantity)]
    else:
        pool = narrowed if narrowed else ops
        if order_no.strip() and not item_code:
            on = order_no.strip()
            item_ids = {o.item_id for o in db.query(Order).filter(Order.status == "open", Order.order_no == on).all()}
            pool = [o for o in pool if o.item_id in item_ids] or pool
        targets = _allocation_targets(db, pool, quantity, order_no)

    q = db.query(ProductionActual).filter(
        ProductionActual.prod_date == prod_date,
        ProductionActual.semi_finished_code == wip_raw,
    )
    if order_no.strip():
        q = q.filter(ProductionActual.order_no == order_no)
    q.delete(synchronize_session=False)

    ins = 0
    for i, (op, ord_row, slice_qty) in enumerate(targets):
        if slice_qty <= 1e-9:
            continue
        slice_order_no = order_no
        if ord_row and not order_no.strip():
            slice_order_no = ord_row.order_no
        earned = op.hours_for(slice_qty) - (op.setup_time_min / 60.0)
        rh = reported_hours if reported_hours is not None and i == 0 else None
        pa = ProductionActual(
            prod_date=prod_date,
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no=slice_order_no or "",
            semi_finished_code=wip_raw,
            quantity=slice_qty,
            earned_hours=round(max(earned, 0.0), 4),
            reported_hours=rh,
        )
        db.add(pa)
        ins += 1
    return ins, 0
