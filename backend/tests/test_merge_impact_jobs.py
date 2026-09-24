from datetime import date
from types import SimpleNamespace
import pytest
from app.schemas import MergeImpactRequest, MergePreviewGroup
from app.services import merge_impact_jobs as jobs
from app.services import durable_jobs


def test_recovery_bounds_and_ownership(monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: submitted.append(args)))
    req = MergeImpactRequest(start_week=date(2026, 9, 21), merge_groups=[MergePreviewGroup(order_ids=[1, 2])])
    key = "a" * 32
    first = jobs.start(1, req, key)
    assert jobs.start(1, req)["id"] == first["id"]
    assert jobs.get(2, key) is None
    for owner in (2, 3, 4):
        jobs.start(owner, req)
    with pytest.raises(ValueError):
        jobs.start(5, req)
    durable_jobs.run(key, lambda db: {"merge_count": 1}, readonly=True)
    assert jobs.start(1, req, key)["result"] == {"merge_count": 1}
    assert len(submitted) == 4
    with pytest.raises(ValueError):
        jobs.start(2, req, key)
    with pytest.raises(ValueError):
        jobs.start(1, req.model_copy(update={"weeks": 2}), key)
    assert jobs.get(1, key)["elapsed_seconds"] >= 0
