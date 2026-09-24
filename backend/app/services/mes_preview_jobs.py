"""Read-only MES preview with durable status and result."""
from concurrent.futures import ThreadPoolExecutor
from app.services import mes, durable_jobs

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mes-preview")
KIND = "mes_preview"


def start(owner_id, content):
    return durable_jobs.start(KIND, owner_id, content, _executor, _run, (content,), max_pending=4)


def get(owner_id, job_id):
    return durable_jobs.get(KIND, owner_id, job_id)


def _run(job_id, content):
    durable_jobs.run(job_id, lambda db: mes.preview(db, content), readonly=True)
