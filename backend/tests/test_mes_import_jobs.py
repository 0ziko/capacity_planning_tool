from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from app.models import Item
from app.models.mes import MesDetail
from app.services import mes, mes_import_jobs as jobs
from tests.test_mes import workbook, DAY


@pytest.fixture
def queue(monkeypatch, db):
    submitted = []
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: submitted.append(args)))
    return submitted


def test_import_result_recovered_without_second_write(client, auth, db, queue):
    data = workbook([["import-job-1", DAY, "5999997-12", 10, "UNKNOWN-MACHINE"]])
    token = mes.preview(db, data)["token"]
    key = "b" * 32
    def start():
        return client.post("/api/mes/import/jobs", headers=auth,
            params={"request_id": key, "token": token}, files={"file": ("mes.xlsx", data)})
    first = start()
    assert first.status_code == 200, first.text
    jobs._run(key, data, token, "admin", "mes.xlsx")
    recovered = start()
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "done"
    assert recovered.json()["result"]["counts"]["new"] == 1
    assert len(queue) == 1
    assert db.get(MesDetail, "import-job-1").quantity == 10
    assert client.get(f"/api/mes/import/jobs/{key}").status_code in (401, 403)
    assert jobs.get(-1, key) is None


def test_conflicting_or_concurrent_import_rejected(queue):
    key = "c" * 32
    jobs.start(1, "test", key, b"data", "token", "a.xlsx")
    assert jobs.start(1, "test", key, b"data", "token", "a.xlsx")["id"] == key
    for owner, rid, data, token in [(2, key, b"data", "token"), (1, key, b"different", "token"),
                                     (1, key, b"data", "different"), (1, "d" * 32, b"data", "token")]:
        with pytest.raises(ValueError):
            jobs.start(owner, "test", rid, data, token, "a.xlsx")
    assert len(queue) == 1


def test_failed_import_rolls_back_partial_writes(db, queue, monkeypatch):
    def broken(db, *args):
        db.add(Item(code="FAILED-MES-JOB", name="Must roll back"))
        db.flush()
        raise ValueError("Aktarım reddedildi")
    monkeypatch.setattr(mes, "apply_import", broken)
    key = "e" * 32
    jobs.start(1, "test", key, b"data", "token", "a.xlsx")
    jobs._run(key, b"data", "token", "test", "a.xlsx")
    assert jobs.get(1, key)["status"] == "failed"
    assert db.query(Item).filter_by(code="FAILED-MES-JOB").first() is None


def test_stale_preview_rejected(db, queue):
    data = workbook([["import-job-stale", DAY, "5999997-12", 10, "UNKNOWN-MACHINE"]])
    key = "f" * 32
    jobs.start(1, "test", key, data, "0" * 64, "mes.xlsx")
    jobs._run(key, data, "0" * 64, "test", "mes.xlsx")
    assert jobs.get(1, key)["status"] == "failed"
    assert db.get(MesDetail, "import-job-stale") is None
