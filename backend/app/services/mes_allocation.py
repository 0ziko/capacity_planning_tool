"""Peg the common WIP pool to finished-product completion weeks, never customers."""
from collections import defaultdict
from sqlalchemy.orm import selectinload
from app.models import Item, PlanLine, norm_wip
from app.services.mes import monday, inputs_for


def allocate_pool(db, pool, as_of, orders, required, credits, items=None):
    """Mutates only request-local pool/credits; returns reviewable material allocations.

    Final-operation plan lines are capacity-limited slots, not whole-order priorities.
    Thus a split order cannot consume next week's WIP before another FG this week.
    """
    by_order = {o.id: o for o in orders}
    final_ids = {o.id: max(o.item.operations, key=lambda x: x.seq).id
                 for o in orders if o.item and o.item.operations}
    lines = db.query(PlanLine).filter(PlanLine.mode.in_(["auto", "manual"])).order_by(
        PlanLine.week_start, PlanLine.id).all()
    if items is None:
        items = db.query(Item).options(selectinload(Item.bom_lines), selectinload(Item.operations)).all()
    operations = {op.id: op for i in items for op in i.operations}
    already_finished = {oid: credits.get((oid, op_id), 0) for oid, op_id in final_ids.items()}
    result = []
    for line in lines:
        oid = line.order_id
        if oid not in by_order or line.operation_id != final_ids.get(oid):
            continue
        req = required.get(oid, {})
        final_demand = req.get(line.operation_id, 0)
        if final_demand <= 0:
            continue
        skip = min(already_finished[oid], max(line.planned_qty, 0))
        already_finished[oid] -= skip
        slot_qty = max(line.planned_qty - skip, 0)
        if slot_qty <= 0:
            continue
        ordered = sorted((operations[i] for i in req if i in operations), key=lambda o: o.seq, reverse=True)
        by_code = {norm_wip(o.semi_finished_code): o for o in ordered if o.semi_finished_code}
        slot_used = defaultdict(float)
        for op in ordered:
            code = norm_wip(op.semi_finished_code)
            global_room = max(req[op.id] - credits.get((oid, op.id), 0), 0)
            slot_room = max(slot_qty * req[op.id] / final_demand - slot_used[op.id], 0)
            take = min(pool.get(code, 0), global_room, slot_room)
            if take <= 1e-9:
                continue
            pool[code] -= take
            result.append({"material_code": code, "quantity": take,
                           "finished_item_code": by_order[oid].item.code,
                           "finish_week": max(line.week_start, monday(as_of)).isoformat(),
                           "original_finish_week": line.week_start.isoformat(),
                           "plan_line_id": line.id, "operation_name": op.operation_name, "work_center_id": op.work_center_id})
            pending, visited = [(op, take)], set()
            while pending:
                step, qty = pending.pop()
                if step.id in visited:
                    continue
                visited.add(step.id)
                key = (oid, step.id)
                credit = min(qty, max(req.get(step.id, 0) - credits.get(key, 0), 0))
                credits[key] = credits.get(key, 0) + credit
                slot_used[step.id] += credit
                try:
                    inputs = inputs_for(step.item, step)
                except ValueError:
                    inputs = {}
                for child, coefficient in inputs.items():
                    if child in by_code:
                        pending.append((by_code[child], qty * coefficient))
    return result
