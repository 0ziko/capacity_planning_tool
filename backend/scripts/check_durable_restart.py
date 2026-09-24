"""Local acceptance: only job metadata is written; MES preview is read-only."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.models import User
from app.services import durable_jobs, mes_preview_jobs

receipt = Path(__file__).resolve().parents[2] / "outputs" / "durable-restart-check.json"
if sys.argv[1] == "prepare":
    with SessionLocal() as db:
        owner = db.query(User).filter(User.role == "admin").first().id
    job = mes_preview_jobs.start(owner, Path(sys.argv[2]).read_bytes())
    mes_preview_jobs._executor.shutdown(wait=True)
    done = mes_preview_jobs.get(owner, job["id"])
    assert done["status"] == "done", done
    pending = durable_jobs.start("mes_preview", owner, b"restart acceptance pending probe",
        SimpleNamespace(submit=lambda *args: None), lambda *args: None, max_pending=4)
    receipt.write_text(json.dumps(dict(owner=owner, completed_id=done["id"], pending_id=pending["id"],
        rows=len(done["result"]["rows"]), token=done["result"]["token"])), encoding="utf-8")
    print("Prepared: completed preview and queued metadata probe. No MES import.")
elif sys.argv[1] == "verify":
    expected = json.loads(receipt.read_text(encoding="utf-8"))
    done = mes_preview_jobs.get(expected["owner"], expected["completed_id"])
    pending = mes_preview_jobs.get(expected["owner"], expected["pending_id"])
    assert done["status"] == "done"
    assert done["result"]["token"] == expected["token"]
    assert len(done["result"]["rows"]) == expected["rows"]
    assert pending["status"] == "failed" and "kaydedilmedi" in pending["error"]
    expected["verified_after_restart"] = True
    receipt.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print(f"Verified after backend restart: {expected['rows']} preview rows preserved; interrupted queue explicitly failed.")
else:
    raise SystemExit("Use prepare <MES.xlsx>, restart backend, then verify")
