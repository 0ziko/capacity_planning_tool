"""Read-only acceptance timing; never apply plans or import MES data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date
from time import perf_counter
import json
from sqlalchemy import text
from app.db.session import SessionLocal
from app.schemas import AutoPlanRequest, MergePreviewGroup
from app.services import planning, mes, orders, production_batches
from app.services.merge_impact import preview_merge_impact

req = AutoPlanRequest(start_week=date(2026, 9, 21), weeks=8)
results = {}
def run(name, fn):
    with SessionLocal() as db:
        if db.get_bind().dialect.name != "postgresql":
            raise RuntimeError("This check requires PostgreSQL read-only protection")
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '120s'"))
        started = perf_counter()
        try:
            detail = fn(db)
            results[name] = {"seconds": round(perf_counter() - started, 3), **detail}
            print(json.dumps({name: results[name]}, ensure_ascii=False), flush=True)
        finally:
            db.rollback()

def plan(db):
    sim = planning.simulate(db, req)
    return {"planned_lines": len(sim.lines), "unplanned": len(sim.unplanned)}

def preview(db):
    data = Path(sys.argv[1]).read_bytes()
    out = mes.preview(db, data)
    return {"rows": len(out["rows"])}

def merge(db):
    grouped = {}
    batched = production_batches.batched_order_ids(db)
    for order in orders._open_orders(db):
        if order.id not in batched:
            grouped.setdefault(order.item_id, []).append(order.id)
    group = next((ids for ids in grouped.values() if len(ids) >= 2), None)
    if group is None:
        return {"not_run": "No eligible unbatched pair"}
    out = preview_merge_impact(db, [MergePreviewGroup(order_ids=group[:2])], req,
                              on_phase=lambda phase: print(phase, flush=True))
    return {"groups": out.merge_count, "orders": out.order_count}

run("mes_preview", preview)
run("planning_simulation", plan)
run("merge_impact_one_pair", merge)
Path("../outputs/d5-timings.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
