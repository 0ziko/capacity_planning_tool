"""Explicitly authorized local MES acceptance. Default is read-only snapshot."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from datetime import date
from collections import Counter
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.models.user import User
from app.services import orders, mes, mes_import_jobs, mes_actuals, mes_inventory

p = argparse.ArgumentParser()
p.add_argument("file")
p.add_argument("--apply", action="store_true")
args = p.parse_args()
outdir = Path(__file__).resolve().parents[2] / "outputs" / "mes-cutover-20260922"
outdir.mkdir(exist_ok=True)

def snapshot(name):
    with SessionLocal() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        db.execute(text("SET TRANSACTION READ ONLY"))
        schedule = orders.order_schedule(db, None)
        inventory = mes_inventory.report(db, date.today())
        result = {"source": get_settings().production_source,
                  "schedule": [r.model_dump(mode="json") for r in schedule],
                  "inventory": inventory,
                  "status_counts": dict(Counter(r.plan_status for r in schedule)),
                  "required_hours": sum(r.required_hours for r in schedule)}
        if get_settings().production_source == "mes":
            measured = mes_actuals.measure(db, date.today())
            planned = sum(l.planned_hours for l in measured["lines"])
            matched = sum(min(l.planned_hours, measured["matches"][l.id]["hours"]) for l in measured["lines"])
            result["capacity"] = {"planned_hours": planned, "matched_hours": matched,
                                  "remaining_hours": planned - matched,
                                  "standard_output_hours": sum(measured["daily"].values())}
        db.rollback()
    (outdir / (name + ".json")).write_text(json.dumps(result, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"snapshot": name, "source": result["source"], "status_counts": result["status_counts"],
                      "required_hours": result["required_hours"], "capacity": result.get("capacity")}, ensure_ascii=False), flush=True)
    return result

before = snapshot("before")
if not args.apply:
    sys.exit(0)
content = Path(args.file).read_bytes()
with SessionLocal() as db:
    preview = mes.preview(db, content)
    owner = db.query(User).filter(User.is_active.is_(True), User.role == "owner").order_by(User.id).first()
    if owner is None:
        raise RuntimeError("Active owner required")
    owner_id, username = owner.id, owner.username
    if preview["counts"]["unresolved"]:
        raise RuntimeError("Unexpected unresolved mappings; no import performed")
request_id = uuid.uuid4().hex
(outdir / "import-request.json").write_text(json.dumps({"request_id": request_id, "owner_id": owner_id}), encoding="utf-8")
mes_import_jobs.start(owner_id, username, request_id, content, preview["token"], Path(args.file).name)
while True:
    status = mes_import_jobs.get(owner_id, request_id)
    if status["status"] in ("done", "failed"):
        break
    time.sleep(1)
(outdir / "import-result.json").write_text(json.dumps(status, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(status, default=str, ensure_ascii=False), flush=True)
if status["status"] != "done":
    raise RuntimeError("Import failed; source not switched")
with SessionLocal() as db:
    repeat = mes.preview(db, content)
    assert repeat["counts"]["unchanged"] == len(repeat["rows"]), repeat["counts"]
    print(json.dumps({"repeat_preview": repeat["counts"]}), flush=True)
# Compare MES behavior in this process before changing the running server config.
get_settings().production_source = "mes"
after = snapshot("after")
before_by_id = {r["order_id"]: r for r in before["schedule"]}
changed = [{"order_id": r["order_id"], "order_no": r["order_no"], "item_code": r["item_code"],
            "before_hours": before_by_id[r["order_id"]]["required_hours"], "after_hours": r["required_hours"],
            "before_status": before_by_id[r["order_id"]]["plan_status"], "after_status": r["plan_status"]}
           for r in after["schedule"] if abs(r["required_hours"] - before_by_id[r["order_id"]]["required_hours"]) > 1e-5]
(outdir / "changed-orders.json").write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"changed_positions": len(changed), "required_hours_difference": after["required_hours"] - before["required_hours"]}), flush=True)
