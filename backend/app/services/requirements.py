"""Siparis / stok kodu bazli is gucu ihtiyaci (saat)."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, RoutingOperation, WorkCenter
from app.schemas import RequirementLine, RequirementQuery
from app.services import capacity as cap
from app.services.bom_tree import flatten_fg_operations
from app.services.remaining_work import SchedulingContext, build_work_map_for_order, operation_run_hours, produced_qty_map


def _shared_order_inputs(db: Session, orders: list[Order], produced: dict) -> dict:
    """Acik siparisler icin ortak, bir kez yuklenen girdiler (orders.order_schedule ile ayni desen)."""
    from sqlalchemy import func

    from app.models import PlanLine, Reservation, Shipment
    from app.services.bom_tree import _load_wip_items, is_wip_asm_link
    from app.services.order_finished_netting import compute_order_demand_netting

    order_ids = [o.id for o in orders]
    if not order_ids:
        return {"netting": {}, "wip_items": {}, "lines": {}}
    shipped = dict(db.query(Shipment.order_id, func.sum(Shipment.quantity)).filter(Shipment.order_id.in_(order_ids)).group_by(Shipment.order_id).all())
    reservations: dict[int, list] = defaultdict(list)
    for r in db.query(Reservation).filter(Reservation.order_id.in_(order_ids)).all():
        reservations[r.order_id].append(r)
    netting = {
        o.id: compute_order_demand_netting(db, o, produced_map=produced, shipped_qty=shipped.get(o.id, 0), reservations=reservations[o.id])
        for o in orders
    }
    wip_codes = {
        bl.component_code
        for o in orders
        if o.item
        for bl in (o.item.bom_lines or [])
        if is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))
    }
    wip_items = _load_wip_items(db, list(wip_codes)) if wip_codes else {}
    lines: dict[tuple[int, int], list] = defaultdict(list)
    for pl in db.query(PlanLine).filter(PlanLine.order_id.in_(order_ids), PlanLine.mode.in_(["auto", "manual"])).all():
        if pl.production_batch_id is None:
            lines[(pl.order_id, pl.operation_id)].append(pl)
    return {"netting": netting, "wip_items": wip_items, "lines": lines}


def _work_map_for_order(db: Session, order: Order, ctx: SchedulingContext, produced: dict, shared: dict, prod_warnings: list[str]) -> dict:
    """build_work_map_for_order ile ayni sonuc; netleme/WIP/plan satirlari paylasilan girdilerden gelir."""
    from app.services.remaining_work import operation_remaining, required_qty_by_operation

    req = required_qty_by_operation(db, order, produced=produced, netting_cache=shared["netting"], wip_items=shared["wip_items"])
    netting = shared["netting"].get(order.id)
    # build_work_map_for_order: 'produced' disaridan verildiginde uretim uyarilari op uyarilarina eklenmez.
    warnings: list[str] = []
    if netting is not None and getattr(netting, "warnings", None):
        warnings.extend(netting.warnings)
    return {
        op_id: operation_remaining(
            db, order.id, op_id, rq, ctx,
            production_batch_id=None, produced=produced,
            plan_lines=shared["lines"].get((order.id, op_id), []), warnings=warnings,
        )
        for op_id, rq in req.items()
    }


def requirement_lines(db: Session, q: RequirementQuery) -> list[RequirementLine]:
    """Secilen stok kodlari / is merkezleri icin operasyon bazli saat ihtiyaci.

    Miktar kaynagi:
      - q.quantities verilmisse: stok kodu -> miktar
      - degilse: acik siparisler (termin filtresi uygulanir) toplanir
    """
    wc_filter = set(q.work_center_ids) if q.work_center_ids else None

    # miktarlar
    qty_by_code: dict[str, float] = defaultdict(float)
    remaining_by_key: dict[tuple[int, str, int], float] = defaultdict(float)
    if q.quantities:
        for code, qty in q.quantities.items():
            qty_by_code[code] += float(qty)
    else:
        orders = db.query(Order).options(joinedload(Order.item).joinedload(Item.operations)).filter(Order.status == "open")
        if q.due_from:
            orders = orders.filter(Order.due_date >= q.due_from)
        if q.due_to:
            orders = orders.filter(Order.due_date <= q.due_to)
        order_rows = orders.all()
        today = cap.week_start(date.today())
        broad_ctx = SchedulingContext(
            horizon_start=today,
            horizon_end_exclusive=today + timedelta(days=365 * 5),
            replace_existing=False,
        )
        produced, prod_warnings = produced_qty_map(db)
        # Siparis basina ayri sorgu yerine ortak girdiler bir kez yuklenir (sevk, rezervasyon, WIP kartlari,
        # plan satirlari); hesap kurallari build_work_map_for_order ile birebir aynidir.
        shared = _shared_order_inputs(db, order_rows, produced)
        flat_cache: dict[int, list] = {}
        for o in order_rows:
            if q.item_codes and o.item.code not in q.item_codes:
                continue
            qty_by_code[o.item.code] += o.quantity
            if not o.item:
                continue
            wm = _work_map_for_order(db, o, broad_ctx, produced, shared, prod_warnings)
            flats = flat_cache.get(o.item_id)
            if flats is None:
                flats = flat_cache[o.item_id] = flatten_fg_operations(db, o.item)
            for flat in flats:
                op = flat.operation
                w = wm.get(op.id)
                if not w or w.qty_to_schedule <= 1e-6:
                    continue
                key = (op.work_center_id, o.item.code, flat.display_seq)
                remaining_by_key[key] += operation_run_hours(op, w.qty_to_schedule, setup_required=w.setup_required)

    if not qty_by_code:
        return []

    items = (
        db.query(Item)
        .options(
            joinedload(Item.operations).joinedload(RoutingOperation.work_center),
            joinedload(Item.bom_lines),
        )
        .filter(Item.code.in_(list(qty_by_code.keys())))
        .all()
    )
    lines: list[RequirementLine] = []
    for item in items:
        qty = qty_by_code[item.code]
        for flat in flatten_fg_operations(db, item):
            op = flat.operation
            if (wc_filter and op.work_center_id not in wc_filter) or op.work_center is None:
                continue
            gross_h = round(op.hours_for(qty), 3)
            rem_h = round(remaining_by_key.get((op.work_center_id, item.code, flat.display_seq), gross_h if q.quantities else 0.0), 3)
            lines.append(
                RequirementLine(
                    work_center_id=op.work_center_id,
                    work_center_code=op.work_center.code,
                    item_code=item.code,
                    operation_seq=flat.display_seq,
                    operation_name=op.operation_name,
                    quantity=qty,
                    hours=gross_h,
                    remaining_hours=rem_h if not q.quantities else gross_h,
                )
            )
    lines.sort(key=lambda l: (l.work_center_code, l.item_code, l.operation_seq))
    return lines


def summarize_by_work_center(lines: list[RequirementLine]) -> list[dict]:
    agg: dict[int, dict] = {}
    for l in lines:
        a = agg.setdefault(l.work_center_id, {"work_center_id": l.work_center_id, "work_center_code": l.work_center_code, "hours": 0.0})
        a["hours"] += l.hours
    return [dict(v, hours=round(v["hours"], 2)) for v in sorted(agg.values(), key=lambda x: x["work_center_code"])]


def item_total_hours(db: Session, item_code: str, quantity: float) -> dict:
    item = (
        db.query(Item)
        .options(
            joinedload(Item.operations).joinedload(RoutingOperation.work_center),
            joinedload(Item.bom_lines),
        )
        .filter(Item.code == item_code)
        .first()
    )
    if not item:
        return {"item_code": item_code, "quantity": quantity, "total_hours": 0.0, "operations": []}
    from app.services.routing_resource import is_line_operation
    ops = []
    for flat in flatten_fg_operations(db, item):
        op = flat.operation
        line = is_line_operation(op)
        missing = line and (op.line_interval_sec is None or op.line_interval_sec <= 0 or not op.cycle_time_sec or op.cycle_time_sec <= 0)
        ops.append({
            "seq": flat.display_seq,
            "operation_name": op.operation_name,
            "work_center_code": op.work_center.code if op.work_center else "(silinmiş İM)",
            "cycle_time_sec": op.cycle_time_sec,
            "line_interval_sec": op.line_interval_sec,
            "planning_mode": "line" if line else "labor",
            "setup_time_min": op.setup_time_min,
            "wip_code": flat.wip_code,
            "hours": None if missing else op.hours_for(quantity),
            "missing_reason": "Çevrim süresi veya dizilim çıkış aralığı eksik" if missing else None,
        })
    def total(rows):
        return None if any(o["hours"] is None for o in rows) else sum(o["hours"] for o in rows)
    return {
        "item_code": item.code,
        "item_name": item.name,
        "quantity": quantity,
        "total_hours": total(ops),
        "line_hours": total([o for o in ops if o["planning_mode"] == "line"]),
        "labor_hours": total([o for o in ops if o["planning_mode"] == "labor"]),
        "has_line_operations": any(o["planning_mode"] == "line" for o in ops),
        "missing_operation_count": sum(o["hours"] is None for o in ops),
        "operations": ops,
    }
