"""Use a common MES material pool once when computing remaining planning work.

The allocation is an ephemeral scheduling calculation, never a reservation or
customer attribution. Actual production stays exclusively in the MES ledger.
"""
from collections import defaultdict
from datetime import date
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.models import Item, PlanLine, Reservation, Shipment, ProductionBatch, ProductionBatchOrder
from app.models.mes import MesDetail
from app.services.mes import balances, detail_dict


def add_mes_credit(db, out, orders, as_of=None):
    from app.services.remaining_work import required_qty_by_operation, required_qty_for_batch
    query = db.query(MesDetail)
    if as_of:
        query = query.filter(MesDetail.prod_date <= as_of)
    records = [detail_dict(d) for d in query.all()]
    if not records:
        return
    from app.services.mes_inventory import planning_pool
    pool = planning_pool(records)
    finished = defaultdict(float)
    for r in records:
        if r["mapping"].get("kind") == "finished" and r["mapping"]["status"] == "mapped":
            finished[r["mapping"]["item_id"]] += r["quantity"]
    final_ids = {o.id: max(o.item.operations, key=lambda op: op.seq).id for o in orders if o.item and o.item.operations}
    planned_orders = {oid for oid, op_id in db.query(PlanLine.order_id, PlanLine.operation_id).filter(
        PlanLine.mode.in_(["auto", "manual"])).all() if final_ids.get(oid) == op_id}
    orders = [o for o in orders if o.id in planned_orders or finished[o.item_id] > 0]
    if not orders:
        return []
    reservations_by_order = defaultdict(list)
    for reservation in db.query(Reservation).all():
        reservations_by_order[reservation.order_id].append(reservation)
    ship_query = db.query(Shipment.order_id, func.sum(Shipment.quantity))
    # Reservations and fulfillment are current-state balances, not historical snapshots.
    shipped_by_order = {oid: float(qty or 0) for oid, qty in ship_query.group_by(Shipment.order_id).all()}
    from app.services.order_finished_netting import compute_order_demand_netting
    netting_cache = {o.id: compute_order_demand_netting(db, o, produced_map=out,
                     shipped_qty=shipped_by_order.get(o.id, 0), reservations=reservations_by_order[o.id]) for o in orders}
    # Batch plans use their anchor's operation keys, with the whole batch demand.
    from app.services.planning_candidates import batch_anchor_order
    batches = db.query(ProductionBatch).options(selectinload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order)).filter(ProductionBatch.status == "open").all()
    batch_by_anchor, nonanchors = {}, set()
    for batch in batches:
        anchor = batch_anchor_order(batch)
        if anchor:
            batch_by_anchor[anchor.id] = batch
            nonanchors.update(link.order_id for link in batch.orders if link.order_id != anchor.id)
    # Load only the routes participating in this allocation, not the entire
    # master catalog. Include batch members before following assembly links.
    from app.services.bom_tree import is_wip_asm_link
    item_ids = {o.item_id for o in orders}
    item_ids.update(link.order.item_id for batch in batches for link in batch.orders if link.order)
    route_items = db.query(Item).options(selectinload(Item.operations), selectinload(Item.bom_lines)).filter(Item.id.in_(item_ids)).all()
    wip_codes = {(bl.component_code or '').strip().upper() for item in route_items for bl in item.bom_lines
                 if is_wip_asm_link(bl.component_code, bl.source_wip, bl.recipe_seq)}
    items = {i.code.upper(): i for i in route_items}
    if wip_codes:
        for item in db.query(Item).options(selectinload(Item.operations), selectinload(Item.bom_lines)).filter(func.upper(Item.code).in_(wip_codes)).all():
            items[item.code.upper()] = item
    orders = [o for o in orders if o.id not in nonanchors]
    # Reserved quantities already reduce demand through existing stock netting.
    for model in (Reservation, Shipment):
        q = db.query(model.item_id, func.sum(model.quantity))
        for item_id, qty in q.group_by(model.item_id).all():
            finished[item_id] = max(finished[item_id] - float(qty or 0), 0)
    required_by_order = {}
    for order in orders:
        required = required_qty_by_operation(db, order, produced=out, netting_cache=netting_cache, wip_items=items)
        if order.id in batch_by_anchor:
            required = required_qty_for_batch(db, batch_by_anchor[order.id], order, produced=out, netting_cache=netting_cache)
        required_by_order[order.id] = required
        own = sorted(order.item.operations, key=lambda o: o.seq)
        if own and finished[order.item_id] > 0:
            last = own[-1].id
            demand = required.get(last, 0)
            take = min(finished[order.item_id], max(demand - out.get((order.id, last), 0), 0))
            if demand > 0:
                for op_id, qty in required.items():
                    key = (order.id, op_id)
                    out[key] = min(qty, out.get(key, 0) + take * qty / demand)
            finished[order.item_id] -= take
    from app.services.mes_allocation import allocate_pool
    return allocate_pool(db, pool, as_of or date.today(), orders, required_by_order, out, items.values())
