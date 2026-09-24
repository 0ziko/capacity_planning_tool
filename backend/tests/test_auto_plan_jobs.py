from datetime import date
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.schemas import AutoPlanRequest
from app.services import auto_plan_jobs as jobs
from app.services import durable_jobs
from app.models import BackgroundJob


def test_job_deduplicates_and_hides_other_users(monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: submitted.append(args)))
    req = AutoPlanRequest(start_week=date(2026,9,21), weeks=8)
    first = jobs.start(1, "test", req)
    assert jobs.start(1, "test", req)["id"] == first["id"]
    assert len(submitted) == 1
    assert jobs.get(2, first["id"]) is None
    with pytest.raises(ValueError): jobs.start(2, "other", req)
    with pytest.raises(ValueError): jobs.start(1, "test", req.model_copy(update={"weeks": 9}))


def test_lost_start_response_can_recover_completed_job(monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: submitted.append(args)))
    req = AutoPlanRequest(start_week=date(2026, 9, 21), weeks=8)
    key = "a" * 32
    first = jobs.start(1, "test", req, request_id=key)
    assert first["id"] == key
    durable_jobs.run(key, lambda db: {"created": 42})
    recovered = jobs.start(1, "test", req, request_id=key)
    assert recovered["result"] == {"created": 42}
    assert len(submitted) == 1
    assert recovered["elapsed_seconds"] >= 0
    with pytest.raises(ValueError):
        jobs.start(2, "other", req, request_id=key)
    with pytest.raises(ValueError):
        jobs.start(1, "test", req.model_copy(update={"weeks": 9}), request_id=key)


def test_submit_failure_does_not_leave_active_job(monkeypatch, db):
    def fail(*args):
        raise RuntimeError("executor stopped")
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=fail))
    with pytest.raises(RuntimeError):
        jobs.start(1, "test", AutoPlanRequest(start_week=date(2026, 9, 21)))
    assert db.query(BackgroundJob).filter(BackgroundJob.status.in_(["queued", "running"])).count() == 0
    assert db.query(BackgroundJob).one().status == "failed"


@pytest.mark.parametrize("blocked", [False, True])
def test_worker_uses_preflight_endpoint_and_reports_outcome(monkeypatch, blocked):
    from app.api import planning
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: None))
    calls = []
    def run(req, db, username, *, commit):
        assert commit is False
        calls.append((req.missing_headcount_ack, username))
        if blocked: raise HTTPException(409, "Eksik iş gücü onayı")
        return {"created": 42, "message": "Plan kaydedildi"}
    monkeypatch.setattr(planning, "execute_auto_plan", run)
    req = AutoPlanRequest(start_week=date(2026,9,21), missing_headcount_ack="accepted")
    job = jobs.start(1, "tester", req)
    jobs._run(job["id"], req.model_dump_json(), "tester")
    result = jobs.get(1, job["id"])
    assert calls == [("accepted", "tester")]
    assert result["status"] == ("failed" if blocked else "done")
    if blocked: assert result["error"] == "Eksik iş gücü onayı"
    else: assert result["result"]["created"] == 42
