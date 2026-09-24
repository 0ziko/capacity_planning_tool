from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi.encoders import jsonable_encoder
from app.services import mes_preview_jobs as jobs, mes
from app.models.mes import MesDetail
from tests.test_mes import workbook, DAY


def test_preview_queue_deduplicates_and_bounds(monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: submitted.append(args)))
    first = jobs.start(1, b"file")
    assert jobs.start(1, b"file")["id"] == first["id"]
    assert jobs.get(2, first["id"]) is None
    with pytest.raises(ValueError):
        jobs.start(1, b"other file")
    for owner in (2, 3, 4):
        jobs.start(owner, b"file")
    with pytest.raises(ValueError, match="sırası dolu"):
        jobs.start(5, b"file")
    assert len(submitted) == 4


def test_tracked_preview_matches_sync_without_importing(client, auth, db, monkeypatch):
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: None))
    data = workbook([["preview-job-1", DAY, "5999998-12", 10, "UNKNOWN-MACHINE"]])
    before = db.query(MesDetail).count()
    expected = mes.preview(db, data)
    response = client.post("/api/mes/preview/jobs", headers=auth,
                           files={"file": ("mes.xlsx", data)})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    jobs._run(job_id, data)
    result = client.get(f"/api/mes/preview/jobs/{job_id}", headers=auth).json()
    assert result["status"] == "done"
    assert result["result"] == jsonable_encoder(expected)
    assert db.query(MesDetail).count() == before
    assert client.get(f"/api/mes/preview/jobs/{job_id}").status_code in (401, 403)


def test_preview_failure_is_visible_and_releases_queue(monkeypatch):
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: None))
    def fail(*args):
        raise ValueError("Excel sütunları eksik")
    monkeypatch.setattr(mes, "preview", fail)
    job = jobs.start(1, b"bad")
    jobs._run(job["id"], b"bad")
    assert jobs.get(1, job["id"])["error"] == "Excel sütunları eksik"
    assert jobs.start(1, b"fixed")["status"] == "queued"
