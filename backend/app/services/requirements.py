"""Siparis / stok kodu bazli is gucu ihtiyaci (saat)."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, RoutingOperation, WorkCenter
from app.schemas import RequirementLine, RequirementQuery
from app.services import capacity as cap
from app.services.bom_tree import flatten_fg_operations
from app.services.remaining_work import SchedulingContext, build_work_map_for_order, operation_run_hours, produced_qty_map


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
        produced, _ = produced_qty_map(db)
        for o in order_rows:
            if q.item_codes and o.item.code not in q.item_codes:
                continue
            qty_by_code[o.item.code] += o.quantity
            if not o.item:
                continue
            wm = build_work_map_for_order(db, o, broad_ctx, produced=produced)
            for flat in flatten_fg_operations(db, o.item):
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
    ops = [
        {
            "seq": flat.display_seq,
            "operation_name": flat.operation.operation_name,
            "work_center_code": flat.operation.work_center.code if flat.operation.work_center else "(silinmiş İM)",
            "cycle_time_sec": flat.operation.cycle_time_sec,
            "setup_time_min": flat.operation.setup_time_min,
            "wip_code": flat.wip_code,
            "hours": round(flat.operation.hours_for(quantity), 3),
        }
        for flat in flatten_fg_operations(db, item)
    ]
    return {
        "item_code": item.code,
        "item_name": item.name,
        "quantity": quantity,
        "total_hours": round(sum(o["hours"] for o in ops), 3),
        "operations": ops,
    }
