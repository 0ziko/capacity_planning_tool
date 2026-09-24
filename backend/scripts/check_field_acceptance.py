"""Read-only snapshot of live readiness; does not import, plan, or switch sources."""
import sys
import json
from pathlib import Path
from datetime import date
from collections import Counter
from sqlalchemy import text, func

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.models.planning import Order, PlanLine, ProductionActual, StockReceipt, Reservation
from app.models.mes import MesDetail
from app.services import orders, mes_inventory, planning, mes

with SessionLocal() as db:
    if db.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL read-only protection required")
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    db.execute(text("SET TRANSACTION READ ONLY"))
    db.execute(text("SET LOCAL statement_timeout = '120s'"))
    result = {"as_of": str(date.today()), "production_source": get_settings().production_source}
    result["counts"] = {model.__tablename__: db.query(model).count() for model in
                        (Order, PlanLine, ProductionActual, StockReceipt, Reservation, MesDetail)}
    result["plan_weeks"] = [str(v) for v in db.query(func.min(PlanLine.week_start), func.max(PlanLine.week_start)).one()]
    result["mes_dates"] = [str(v) for v in db.query(func.min(MesDetail.prod_date), func.max(MesDetail.prod_date)).one()]
    result["batch_plan_lines"] = db.query(PlanLine).filter(PlanLine.production_batch_id.isnot(None)).count()
    inv = mes_inventory.report(db, date.today())
    result["inventory"] = {"codes": len(inv["rows"]), "movements": len(inv["movements"]),
                           "pending": len(inv["pending"]),
                           "negative_balances": sum(r["balance"] < 0 for r in inv["rows"]),
                           "balance_equation_errors": sum(abs(r["produced_qty"] - r["consumed_qty"] - r["balance"]) > .0002 for r in inv["rows"]),
                           "consumption_with_hours": sum(m["kind"] == "consumption" and m["standard_hours"] != 0 for m in inv["movements"])}
    report = orders.order_schedule(db, None)
    result["schedule"] = {"positions": len(report), "statuses": dict(Counter(r.plan_status for r in report)),
                          "negative_required_hours": sum(r.required_hours < -1e-6 for r in report),
                          "negative_planned_hours": sum(r.planned_hours < -1e-6 for r in report),
                          "end_before_start": sum(bool(r.planned_end and r.planned_start and r.planned_end < r.planned_start) for r in report)}
    lines = planning.plan_lines(db, None, None, None)
    batches = {}
    for line in lines:
        if line.production_batch_id:
            batches.setdefault(line.production_batch_id, line)
    links = db.execute(text("SELECT batch_id, order_id FROM production_batch_orders")).all()
    expected = {}
    for batch_id, order_id in links:
        expected.setdefault(batch_id, set()).add(order_id)
    result["batch_visibility"] = {
        "planned_batches": len(batches),
        "multi_customer_batches": sum(len({m.customer for m in line.batch_members}) > 1 for line in batches.values()),
        "member_mismatches": sum({m.order_id for m in line.batch_members} != expected.get(bid, set()) for bid, line in batches.items()),
        "missing_customer_in_summary": sum(any(m.customer not in line.customer for m in line.batch_members) for line in batches.values()),
        "line_count_matches_storage": len(lines) == result["counts"]["plan_lines"],
        "sample": [{"batch_no": line.batch_no, "item_code": line.item_code,
                    "members": len(line.batch_members), "customers": len({m.customer for m in line.batch_members})}
                   for line in list(batches.values())[:3]],
    }
    if len(sys.argv) > 1:
        preview = mes.preview(db, Path(sys.argv[1]).read_bytes())
        hypothetical = mes_inventory.inventory(preview["rows"], date.today())
        result["mes_preview"] = {
            "file": Path(sys.argv[1]).name, "counts": preview["counts"],
            "standard_hours": preview["standard_hours"], "net_quantity": preview["net_quantity"],
            "shortage_codes": len(preview["shortages"]),
            "shortage_samples": sorted(preview["shortages"], key=lambda r: r["missing_qty"], reverse=True)[:5],
            "shortage_codes_without_output": sum(not any(r["material_code"] == s["material_code"] and r["quantity"] > 0 for r in preview["rows"]) for s in preview["shortages"]),
            "exceptions": [{"code": r["material_code"], "quantity": r["quantity"],
                            "category": r["preview_category"], "reason": r["mapping"].get("reason")}
                           for r in preview["rows"] if r["preview_category"] != "mapped"],
            "hypothetical_inventory_codes": len(hypothetical["rows"]),
            "hypothetical_balance_errors": sum(abs(r["produced_qty"] - r["consumed_qty"] - r["balance"]) > .0002 for r in hypothetical["rows"]),
            "hypothetical_consumption_with_hours": sum(m["kind"] == "consumption" and m["standard_hours"] != 0 for m in hypothetical["movements"]),
        }
    db.rollback()
output = Path(__file__).resolve().parents[2] / "outputs" / "field-acceptance-readiness.json"
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
