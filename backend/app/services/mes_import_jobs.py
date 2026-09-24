"""Durable MES import receipt committed together with stock and production."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from app.services import mes, durable_jobs

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mes-import")
KIND = "mes_import"


def start(owner_id, username, request_id, content, token, filename):
    payload = (sha256(content).hexdigest() + ":" + token).encode()
    return durable_jobs.start(KIND, owner_id, payload, _executor, _run,
                              (content, token, username, filename), request_id=request_id, deduplicate_active=False)


def get(owner_id, request_id):
    return durable_jobs.get(KIND, owner_id, request_id)


def _run(request_id, content, token, username, filename):
    def apply(db):
        result = mes.apply_import(db, content, token, username, filename)
        return {"counts": result["counts"]}
    durable_jobs.run(request_id, apply)
