"""Siparis / stok kodu bazli is gucu ihtiyaci (saat)."""

from collections import defaultdict

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, RoutingOperation, WorkCenter
from app.schemas import RequirementLine, RequirementQuery


def requirement_lines(db: Session, q: RequirementQuery) -> list[RequirementLine]:
    """Secilen stok kodlari / is merkezleri icin operasyon bazli saat ihtiyaci.

    Miktar kaynagi:
      - q.quantities verilmisse: stok kodu -> miktar
      - degilse: acik siparisler (termin filtresi uygulanir) toplanir
    """
    wc_filter = set(q.work_center_ids) if q.work_center_ids else None

    # miktarlar
    qty_by_code: dict[str, float] = defaultdict(float)
    if q.quantities:
        for code, qty in q.quantities.items():
            qty_by_code[code] += float(qty)
    else:
        orders = db.query(Order).options(joinedload(Order.item)).filter(Order.status == "open")
        if q.due_from:
            orders = orders.filter(Order.due_date >= q.due_from)
        if q.due_to:
            orders = orders.filter(Order.due_date <= q.due_to)
        for o in orders:
            if q.item_codes and o.item.code not in q.item_codes:
                continue
            qty_by_code[o.item.code] += o.quantity

    if not qty_by_code:
        return []

    items = (
        db.query(Item)
        .options(joinedload(Item.operations).joinedload(RoutingOperation.work_center))
        .filter(Item.code.in_(list(qty_by_code.keys())))
        .all()
    )
    lines: list[RequirementLine] = []
    for item in items:
        qty = qty_by_code[item.code]
        for op in item.operations:
            if wc_filter and op.work_center_id not in wc_filter:
                continue
            lines.append(
                RequirementLine(
                    work_center_id=op.work_center_id,
                    work_center_code=op.work_center.code,
                    item_code=item.code,
                    operation_seq=op.seq,
                    operation_name=op.operation_name,
                    quantity=qty,
                    hours=round(op.hours_for(qty), 3),
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
    item = db.query(Item).options(joinedload(Item.operations).joinedload(RoutingOperation.work_center)).filter(Item.code == item_code).first()
    if not item:
        return {"item_code": item_code, "quantity": quantity, "total_hours": 0.0, "operations": []}
    ops = [
        {
            "seq": op.seq,
            "operation_name": op.operation_name,
            "work_center_code": op.work_center.code,
            "cycle_time_sec": op.cycle_time_sec,
            "setup_time_min": op.setup_time_min,
            "hours": round(op.hours_for(quantity), 3),
        }
        for op in item.operations
    ]
    return {
        "item_code": item.code,
        "item_name": item.name,
        "quantity": quantity,
        "total_hours": round(sum(o["hours"] for o in ops), 3),
        "operations": ops,
    }
