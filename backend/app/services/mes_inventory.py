"""Cumulative MES material movements, independent of plans and customers.

Days have no intraday ordering: all outputs of a day are available for that day's
known consumption. Pending input alternatives reserve conservative quantities;
they are never silently converted into physical consumption.
"""
from collections import defaultdict


def inventory(records, as_of=None):
    produced, consumed, held = defaultdict(float), defaultdict(float), defaultdict(float)
    movements, pending = [], []
    for record in records:
        if as_of and record["prod_date"] > as_of:
            continue
        mapping = record["mapping"]
        if mapping.get("status") not in ("mapped", "free_stock"):
            continue
        quantity = float(record["quantity"])
        common = {"detail_id": record["detail_id"], "day": record["prod_date"],
                  "machine_code": record["machine_code"], "output_code": record["material_code"]}
        if mapping.get("kind") != "finished":
            code = record["material_code"]
            produced[code] += quantity
            movements.append({**common, "material_code": code, "kind": "production", "quantity": quantity,
                              "operation_name": mapping.get("operation_name"),
                              "standard_hours": quantity * float(mapping.get("standard_unit_hours") or 0)})
        for code, ratio in mapping.get("inputs", {}).items():
            amount = quantity * ratio
            consumed[code] += amount
            movements.append({**common, "material_code": code, "kind": "consumption", "quantity": -amount,
                              "operation_name": mapping.get("operation_name"), "standard_hours": 0.})
        if mapping.get("consumption_status") == "pending" and quantity > 0:
            alternatives = mapping.get("input_candidates", [])
            possible = defaultdict(float)
            for candidate in alternatives:
                for code, ratio in candidate.items():
                    possible[code] = max(possible[code], quantity * ratio)
            for code, amount in possible.items():
                held[code] += amount
            pending.append({**common, "quantity": quantity, "input_candidates": alternatives,
                            "reason": mapping.get("reason", "Tüketim tanımı belirsiz")})
    codes = produced.keys() | consumed.keys() | held.keys()
    rows = []
    for code in sorted(codes):
        balance = produced[code] - consumed[code]
        rows.append({"material_code": code, "opening_qty": 0., "produced_qty": round(produced[code], 4),
                     "consumed_qty": round(consumed[code], 4), "balance": round(balance, 4),
                     "pending_consumption_qty": round(held[code], 4),
                     "available_qty": round(max(balance - held[code], 0), 4)})
    running = defaultdict(float)
    for move in sorted(movements, key=lambda m: (m["day"], m["kind"] != "production", m["detail_id"], m["material_code"])):
        running[move["material_code"]] += move["quantity"]
        move["balance_after"] = round(running[move["material_code"]], 4)
    movements.sort(key=lambda m: (m["day"], m["kind"] != "production", m["detail_id"], m["material_code"]))
    return {"as_of": as_of, "opening_status": "zero_confirmed", "rows": rows, "movements": movements,
            "pending": pending, "notes": ["Açılış stoğu kullanıcı kararıyla sıfır kabul edildi; bakiyeler MES başlangıcından itibaren hesaplanır.",
            "Aynı günün girişleri tüketimlerinden önce netleştirilir; gün içi saat sırası iddiası yoktur.",
            "Belirsiz tüketim ihtimalleri stoktan düşülmez; olası miktarlar planlama kullanılabilirliğinden ayrılır."]}


def planning_pool(records, as_of=None):
    return {row["material_code"]: row["available_qty"] for row in inventory(records, as_of)["rows"]}


def report(db, as_of=None):
    from app.models.mes import MesDetail
    from app.services.mes import detail_dict
    query = db.query(MesDetail)
    if as_of:
        query = query.filter(MesDetail.prod_date <= as_of)
    return inventory([detail_dict(record) for record in query.all()], as_of)
