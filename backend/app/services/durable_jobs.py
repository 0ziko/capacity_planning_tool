"""Durable job receipts for the local single-worker deployment.

Write jobs commit their result and business changes in the SAME transaction.
After a process restart unfinished jobs become explicit failures, never replays.
"""
from datetime import datetime, timezone
from hashlib import sha256
from threading import Lock
from uuid import uuid4
import logging
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob

INSTANCE_ID = uuid4().hex
_lock = Lock()
ACTIVE = ("queued", "running")
logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def public(job):
    end = job.updated_at if job.status not in ACTIVE else utcnow()
    return dict(id=job.id, status=job.status, phase=job.phase, result=job.result,
                error=job.error, elapsed_seconds=max(0, round((end-job.created_at).total_seconds(), 1)))


def recover_interrupted():
    """Called once on startup, after schema creation, before accepting requests."""
    with SessionLocal() as db:
        db.execute(update(BackgroundJob).where(
            BackgroundJob.status.in_(ACTIVE), BackgroundJob.instance_id != INSTANCE_ID,
        ).values(status="failed", phase="Sunucu yeniden başlatılırken işlem yarım kaldı",
                 error="Sunucu yeniden başlatıldığı için işlem tamamlanamadı. Bu işlemin tamamlanmamış değişiklikleri kaydedilmedi. Güncel veriyi kontrol ederek yeni bir işlem başlatabilirsiniz.",
                 updated_at=utcnow()))
        db.commit()


def start(kind, owner, payload, executor, worker, args=(), request_id=None, max_pending=1, deduplicate_active=True):
    fingerprint = sha256(payload).hexdigest()
    with _lock, SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(76203420)"))
        previous = db.get(BackgroundJob, request_id) if request_id else None
        if previous:
            if (previous.kind, previous.owner_id, previous.fingerprint) != (kind, owner, fingerprint):
                raise ValueError("İşlem kimliği başka bir kullanıcı veya istekle eşleşiyor.")
            return public(previous)
        active = db.query(BackgroundJob).filter(BackgroundJob.kind == kind, BackgroundJob.status.in_(ACTIVE)).all()
        for job in active:
            if job.owner_id == owner:
                if deduplicate_active and job.fingerprint == fingerprint:
                    return public(job)
                raise ValueError("Önceki işleminiz sürüyor. Sonucunu bekleyin.")
        if len(active) >= max_pending:
            raise ValueError("İşlem sırası dolu. Devam eden işlemin sonucunu bekleyin.")
        now = utcnow()
        job = BackgroundJob(id=request_id or uuid4().hex, kind=kind, owner_id=owner,
            fingerprint=fingerprint, instance_id=INSTANCE_ID, status="queued", phase="İşlem sırada",
            result=None, error=None, created_at=now, updated_at=now)
        db.add(job)
        db.commit()
        initial = public(job)
        try:
            executor.submit(worker, job.id, *args)
        except Exception:
            fail(job.id, "İşlem başlatılamadı. Yeni bir işlem başlatabilirsiniz.")
            raise
        return initial


def get(kind, owner, job_id):
    with SessionLocal() as db:
        job = db.get(BackgroundJob, job_id)
        return public(job) if job and job.kind == kind and job.owner_id == owner else None


def fail(job_id, error):
    # A lost commit acknowledgement must NEVER overwrite a committed success.
    with SessionLocal() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id,
            BackgroundJob.status.in_(ACTIVE), BackgroundJob.instance_id == INSTANCE_ID,
        ).values(status="failed", phase="İşlem tamamlanamadı", error=error, updated_at=utcnow()))
        db.commit()


def phase(job_id, label):
    with SessionLocal() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id,
            BackgroundJob.status == "running", BackgroundJob.instance_id == INSTANCE_ID,
        ).values(phase=label, updated_at=utcnow()))
        db.commit()


def _finish(db, job_id, result):
    job = db.query(BackgroundJob).filter_by(id=job_id, status="running", instance_id=INSTANCE_ID).with_for_update().one_or_none()
    if job is None:
        raise ValueError("İşlem artık bu sunucuda çalışmıyor.")
    job.status = "done"
    job.phase = "İşlem tamamlandı"
    job.error = None
    job.result = jsonable_encoder(result)
    job.updated_at = utcnow()


def run(job_id, action, *, readonly=False):
    try:
        with SessionLocal() as db:
            claimed = db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id,
                BackgroundJob.status == "queued", BackgroundJob.instance_id == INSTANCE_ID,
            ).values(status="running", phase="İşlem hesaplanıyor", updated_at=utcnow())).rowcount
            db.commit()
        if not claimed:
            return
        with SessionLocal() as db:
            if readonly and db.get_bind().dialect.name == "postgresql":
                db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                db.execute(text("SET TRANSACTION READ ONLY"))
            if not readonly:
                # Serialize duplicate workers/recovery against the business commit.
                job = db.query(BackgroundJob).filter_by(id=job_id, status="running", instance_id=INSTANCE_ID).with_for_update().one_or_none()
                if job is None:
                    return
            result = action(db)
            if readonly:
                db.rollback()
            else:
                _finish(db, job_id, result)
                db.commit()
        if readonly:
            with SessionLocal() as db:
                _finish(db, job_id, result)
                db.commit()
    except HTTPException as exc:
        fail(job_id, str(exc.detail))
    except (ValueError, IntegrityError) as exc:
        fail(job_id, str(exc) if isinstance(exc, ValueError) else "Veri değişti. Önizlemeyi yenileyin.")
    except Exception:
        logger.exception("Background job failed: %s", job_id)
        fail(job_id, "İşlem tamamlanamadı; değişiklikler geri alındı. İşlem kimliği: " + job_id)
