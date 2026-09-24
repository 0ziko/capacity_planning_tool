"""Crash-boundary tests use real SQLite transactions, not in-memory job mocks."""
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys
import pytest
from app.models import BackgroundJob, Item
from app.services import durable_jobs as jobs


def enqueue(key="a" * 32, kind="mes_import"):
    return jobs.start(kind, 1, b"approved-input", SimpleNamespace(submit=lambda *a: None),
                      lambda *a: None, request_id=key)


def restart(monkeypatch):
    monkeypatch.setattr(jobs, "INSTANCE_ID", "new-server-instance")
    jobs.recover_interrupted()


def test_success_and_identity_survive_restart(db, monkeypatch):
    job = enqueue()
    def write(session):
        session.add(Item(code="DURABLE-SUCCESS", name="Persisted once"))
        return {"created": 1}
    jobs.run(job["id"], write)
    restart(monkeypatch)
    assert jobs.get("mes_import", 1, job["id"])["result"] == {"created": 1}
    assert db.query(Item).filter_by(code="DURABLE-SUCCESS").count() == 1
    assert enqueue()["status"] == "done"
    jobs.run(job["id"], lambda session: pytest.fail("must never execute twice"))
    assert jobs.get("mes_import", 2, job["id"]) is None
    assert jobs.get("auto_plan", 1, job["id"]) is None


@pytest.mark.parametrize("kind", ["auto_plan", "mes_import"])
def test_crash_before_commit_restores_data_and_explains_interruption(db, monkeypatch, kind):
    job = enqueue(kind=kind)
    code = "DURABLE-CRASH-" + kind
    def interrupted(session):
        session.add(Item(code=code, name="Must not persist"))
        session.flush()
        raise SystemExit("process stopped")
    with pytest.raises(SystemExit):
        jobs.run(job["id"], interrupted)
    assert jobs.get(kind, 1, job["id"])["status"] == "running"
    restart(monkeypatch)
    state = jobs.get(kind, 1, job["id"])
    assert state["status"] == "failed" and "kaydedilmedi" in state["error"]
    assert db.query(Item).filter_by(code=code).first() is None
    jobs.run(job["id"], lambda session: pytest.fail("interrupted work must not auto-replay"))


def test_result_write_failure_rolls_back_business_changes(db, monkeypatch):
    job = enqueue()
    def broken_finish(*args):
        raise RuntimeError("receipt storage failed")
    monkeypatch.setattr(jobs, "_finish", broken_finish)
    def write(session):
        session.add(Item(code="DURABLE-RECEIPT-FAIL", name="Rollback"))
        session.flush()
        return {"created": 1}
    jobs.run(job["id"], write)
    assert jobs.get("mes_import", 1, job["id"])["status"] == "failed"
    assert db.query(Item).filter_by(code="DURABLE-RECEIPT-FAIL").first() is None


def test_failure_handler_cannot_overwrite_a_committed_success():
    job = enqueue()
    jobs.run(job["id"], lambda session: {"created": 1})
    jobs.fail(job["id"], "commit reply lost")
    assert jobs.get("mes_import", 1, job["id"])["status"] == "done"


def test_queued_job_and_readonly_result_survive_restart(monkeypatch):
    pending = enqueue(kind="mes_preview")
    completed = enqueue(key="b" * 32, kind="merge_impact")
    jobs.run(completed["id"], lambda session: {"rows": [1, 2]}, readonly=True)
    restart(monkeypatch)
    assert jobs.get("mes_preview", 1, pending["id"])["status"] == "failed"
    assert jobs.get("merge_impact", 1, completed["id"])["result"] == {"rows": [1, 2]}


@pytest.mark.parametrize("crash_before_commit", [False, True])
def test_actual_process_exit_and_fresh_process_recovery(db, crash_before_commit):
    script = '''
import os, sys
from types import SimpleNamespace
from app.models import Item
from app.services import durable_jobs as jobs
key = "7" * 32
jobs.start("mes_import", 1, b"approved", SimpleNamespace(submit=lambda *a: None), lambda *a: None, request_id=key)
def action(db):
    db.add(Item(code="PROCESS-CRASH-" + sys.argv[1], name="Transaction probe"))
    db.flush()
    if sys.argv[1] == "yes": os._exit(24)
    return {"created": 1}
jobs.run(key, action)
os._exit(23)
'''
    flag = "yes" if crash_before_commit else "no"
    process = subprocess.run([sys.executable, "-c", script, flag],
                             cwd=Path(__file__).resolve().parents[1], timeout=30, capture_output=True)
    assert process.returncode == (24 if crash_before_commit else 23), process.stderr.decode(errors="replace")
    jobs.recover_interrupted()
    recovered = jobs.get("mes_import", 1, "7" * 32)
    assert recovered["status"] == ("failed" if crash_before_commit else "done")
    assert (db.query(Item).filter_by(code="PROCESS-CRASH-" + flag).count() == 1) is not crash_before_commit
    if not crash_before_commit:
        assert recovered["result"] == {"created": 1}
