"""Owner paneli — yalnizca owner rolu."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import require_owner
from app.db.session import get_db
from app.models import ImportLog, PlanLine, ProductionBatch, Reservation, Shipment, User
from app.services import excel
from app.services.owner_detail import (
    MAX_ID_LIST,
    _parse_dt,
    count_purge_scope,
    list_record_ids,
    list_records,
    purge_scoped,
    target_title,
)
from app.services.owner_purge import CONFIRM_TOKEN, dataset_stats

router = APIRouter(prefix="/api/owner", tags=["owner"])

IMPORT_PURGE_MAP = {
    "workcenters": "workcenters_ds",
    "machines": "machines",
    "istasyonlar": "machines",
    "production_bom": "routing",
    "shifts": "shifts",
    "wc_weeks": "wc_weeks",
    "employees": "employees",
    "items": "items_ds",
    "bom": "bom",
    "routing": "routing",
    "op_rules": "op_rules",
    "orders": "orders_ds",
    "production": "production",
    "downtime": "downtime",
    "stock_receipts": "stock_receipts",
}

EXTRA_PURGE = [
    {"key": "plan_lines", "title": "Plan satirlari (tum planlama ciktisi)", "danger": "high"},
    {"key": "production_batches", "title": "Uretim partileri (birlestirilmis siparisler)", "danger": "high"},
    {"key": "reservations", "title": "Stok rezervasyonlari", "danger": "medium"},
    {"key": "shipments", "title": "Sevk kayitlari", "danger": "medium"},
    {"key": "import_logs", "title": "Import gecmisi (log)", "danger": "low"},
]

_EXTRA_MODELS = {
    "plan_lines": PlanLine,
    "production_batches": ProductionBatch,
    "reservations": Reservation,
    "shipments": Shipment,
    "import_logs": ImportLog,
}

ALLOWED_TARGETS = {"orders", "items", "workcenters", *IMPORT_PURGE_MAP.values(), *(e["key"] for e in EXTRA_PURGE)}


class PurgeRequest(BaseModel):
    target: str
    confirm: str = Field(min_length=1)
    ids: list[int] | None = None
    delete_all: bool = False
    select_filtered: bool = False
    from_dt: str | None = None
    to_dt: str | None = None
    username: str | None = None
    search: str | None = None


class PurgeResult(BaseModel):
    target: str
    deleted: int
    scope: str


def _extra_count(db: Session, key: str) -> int:
    m = _EXTRA_MODELS.get(key)
    if not m:
        return 0
    return db.query(func.count()).select_from(m).scalar() or 0


def _filter_kwargs(body: PurgeRequest) -> dict:
    return {
        "from_dt": _parse_dt(body.from_dt),
        "to_dt": _parse_dt(body.to_dt, end_of_day=True),
        "username": body.username or None,
        "search": body.search or None,
    }


def _scope_label(body: PurgeRequest, count: int) -> str:
    if body.delete_all:
        return "tumu"
    if body.select_filtered:
        return f"filtre:{count}"
    if body.ids:
        return f"secili:{len(body.ids)}"
    return "?"


@router.get("/stats")
def owner_stats(db: Session = Depends(get_db), _: User = Depends(require_owner)):
    stats = dataset_stats(db)
    count_by_kind = {d["key"]: d["count"] for d in stats["import_datasets"]}
    datasets = [
        {"key": k, "purge_key": IMPORT_PURGE_MAP[k], "title": t["title"], "count": count_by_kind.get(k, 0)}
        for k, t in excel.TEMPLATES.items()
    ]
    return {
        "orders": stats["orders"],
        "production_batches": stats["production_batches"],
        "items": stats["items"],
        "work_centers": stats["work_centers"],
        "plan_lines": stats["plan_lines"],
        "import_logs": stats["import_logs"],
        "import_datasets": datasets,
        "extra_purges": [{**e, "count": _extra_count(db, e["key"])} for e in EXTRA_PURGE],
    }


@router.get("/records")
def owner_records(
    target: str,
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    username: str | None = None,
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_owner),
):
    if target not in ALLOWED_TARGETS:
        raise HTTPException(400, "Gecersiz hedef")
    try:
        return list_records(
            db, target,
            from_dt=_parse_dt(from_dt),
            to_dt=_parse_dt(to_dt, end_of_day=True),
            username=username or None,
            search=search or None,
            offset=offset,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/records/ids")
def owner_record_ids(
    target: str,
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    username: str | None = None,
    search: str | None = None,
    limit: int = Query(MAX_ID_LIST, ge=1, le=MAX_ID_LIST),
    db: Session = Depends(get_db),
    _: User = Depends(require_owner),
):
    if target not in ALLOWED_TARGETS:
        raise HTTPException(400, "Gecersiz hedef")
    try:
        ids = list_record_ids(
            db, target,
            from_dt=_parse_dt(from_dt),
            to_dt=_parse_dt(to_dt, end_of_day=True),
            username=username or None,
            search=search or None,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"target": target, "ids": ids, "count": len(ids)}


@router.get("/detail")
def owner_detail_compat(
    target: str,
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    username: str | None = None,
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
):
    return owner_records(target, from_dt, to_dt, username, search, offset, limit, db, user)


@router.post("/purge/preview")
def owner_purge_preview(body: PurgeRequest, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    if body.target not in ALLOWED_TARGETS:
        raise HTTPException(400, "Gecersiz hedef")
    fk = _filter_kwargs(body)
    try:
        n = count_purge_scope(
            db, body.target,
            ids=body.ids,
            delete_all=body.delete_all,
            select_filtered=body.select_filtered,
            **fk,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"target": body.target, "count": n, "scope": _scope_label(body, n), "title": target_title(body.target)}


@router.post("/purge", response_model=PurgeResult)
def owner_purge(body: PurgeRequest, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    if body.confirm.strip().upper() != CONFIRM_TOKEN:
        raise HTTPException(400, f"Onay icin '{CONFIRM_TOKEN}' yazilmalidir")
    if body.target not in ALLOWED_TARGETS:
        raise HTTPException(400, "Gecersiz hedef")
    if not body.delete_all and not body.select_filtered and not body.ids:
        raise HTTPException(400, "Silinecek kayit secilmedi")
    fk = _filter_kwargs(body)
    try:
        preview_n = count_purge_scope(
            db, body.target,
            ids=body.ids,
            delete_all=body.delete_all,
            select_filtered=body.select_filtered,
            **fk,
        )
        deleted = purge_scoped(
            db, body.target,
            ids=body.ids,
            delete_all=body.delete_all,
            select_filtered=body.select_filtered,
            **fk,
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(400, f"Silme basarisiz: {e}")
    return PurgeResult(target=body.target, deleted=deleted, scope=_scope_label(body, preview_n))
