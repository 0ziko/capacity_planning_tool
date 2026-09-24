"""Read-only merge analysis with durable status and result."""
from concurrent.futures import ThreadPoolExecutor
from app.schemas import AutoPlanRequest, MergeImpactRequest
from app.services.merge_impact import preview_merge_impact
from app.services import durable_jobs

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="merge-impact")
KIND = "merge_impact"


def start(owner_id: int, request: MergeImpactRequest, request_id: str | None = None):
    AutoPlanRequest(start_week=request.start_week, weeks=request.weeks,
                    work_center_ids=request.work_center_ids, mode=request.mode)
    payload = request.model_dump_json()
    return durable_jobs.start(KIND, owner_id, payload.encode(), _executor, _run,
                              (payload,), request_id=request_id, max_pending=4)


def get(owner_id: int, job_id: str):
    return durable_jobs.get(KIND, owner_id, job_id)


def _run(job_id, payload):
    req = MergeImpactRequest.model_validate_json(payload)
    def analyze(db):
        auto = AutoPlanRequest(start_week=req.start_week, weeks=req.weeks,
                               work_center_ids=req.work_center_ids, mode=req.mode, replace_existing=True)
        return preview_merge_impact(db, req.merge_groups, auto,
            on_phase=lambda phase: durable_jobs.phase(job_id, phase)).model_dump(mode="json")
    durable_jobs.run(job_id, analyze, readonly=True)
