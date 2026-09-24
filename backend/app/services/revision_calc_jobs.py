"""Plan revizyonu yeniden hesabı arka plan işi (otomatik plan işi ile aynı kalıcı iş altyapısı).

Canlı veride hesap dakikalar sürebilir; HTTP isteği beklemek yerine iş kuyruğa alınır,
arayüz durumu sorgular. Sonuç revizyon kaydına (snapshot) yazılır; iş sonucu da PlanRevisionOut'tur.
"""
from concurrent.futures import ThreadPoolExecutor

from app.services import durable_jobs

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="revision-calc")
KIND = "revision_calculate"


def start(owner_id: int, username: str, revision_id: int, request_id: str | None = None):
    payload = f"revision:{revision_id}"
    return durable_jobs.start(KIND, owner_id, payload.encode(), _executor, _run,
                              (revision_id, username), request_id=request_id)


def get(owner_id: int, job_id: str):
    return durable_jobs.get(KIND, owner_id, job_id)


def _run(job_id, revision_id, username):
    from app.services import plan_revisions

    def action(db):
        # DIKKAT: durable_jobs.run is satirini FOR UPDATE ile kilitler; burada phase() cagirmak
        # (ayri oturumdan UPDATE) PostgreSQL'de kendi kendini kilitler. Asama metni run() tarafindan yazilir.
        return plan_revisions.calculate(db, revision_id, username, commit=False).model_dump(mode="json")

    durable_jobs.run(job_id, action)
