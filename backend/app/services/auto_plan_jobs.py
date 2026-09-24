"""Durable automatic planning with an atomic business/result commit."""
from concurrent.futures import ThreadPoolExecutor
from app.schemas import AutoPlanRequest
from app.services import durable_jobs

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="auto-plan")
KIND = "auto_plan"


def start(owner_id: int, username: str, request: AutoPlanRequest, request_id: str | None = None):
    payload = request.model_dump_json()
    return durable_jobs.start(KIND, owner_id, payload.encode(), _executor, _run,
                              (payload, username), request_id=request_id)


def get(owner_id: int, job_id: str):
    return durable_jobs.get(KIND, owner_id, job_id)


def _run(job_id, payload, username):
    from app.api.planning import execute_auto_plan
    req = AutoPlanRequest.model_validate_json(payload)
    durable_jobs.run(job_id, lambda db: execute_auto_plan(req, db, username, commit=False))
