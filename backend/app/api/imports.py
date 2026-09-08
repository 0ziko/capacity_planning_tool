"""Excel import / sablon / yedek."""

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import require_poweruser, require_user
from app.db.session import get_db
from app.models import ImportLog, User
from app.schemas import ImportResult, OrderImportPreview
from app.services import excel

router = APIRouter(prefix="/api", tags=["imports"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/imports/kinds", response_model=list[dict])
def import_kinds(_=Depends(require_user)):
    return [
        {"kind": k, "title": t["title"], "columns": [c[1] for c in t["columns"]], "required": [c[1] for c in t["columns"] if c[0] in t["required"]]}
        for k, t in excel.TEMPLATES.items()
    ]


@router.get("/imports/template/{kind}")
def template(kind: str, _=Depends(require_user)):
    if kind not in excel.TEMPLATES:
        raise HTTPException(404, "Sablon bulunamadi")
    return Response(excel.build_template(kind), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="sablon_{kind}.xlsx"'})


@router.post("/imports/orders/preview", response_model=OrderImportPreview)
async def preview_orders(file: UploadFile = File(...), db: Session = Depends(get_db), _=Depends(require_poweruser)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    content = await file.read()
    rows, errs = excel.read_rows(content, "orders")
    if errs:
        return OrderImportPreview(
            parse_errors=errs,
            error_rows=[],
            only_in_system=[],
            only_in_file=[],
            updated=[],
            unchanged_count=0,
            file_row_count=0,
            system_open_count=0,
            missing_item_codes=[],
        )
    return excel.preview_orders_import(db, rows)


@router.post("/imports/orders/preview.xlsx")
async def preview_orders_xlsx(file: UploadFile = File(...), db: Session = Depends(get_db), _=Depends(require_poweruser)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    content = await file.read()
    rows, errs = excel.read_rows(content, "orders")
    if errs:
        preview = OrderImportPreview(
            parse_errors=errs,
            error_rows=[],
            only_in_system=[],
            only_in_file=[],
            updated=[],
            unchanged_count=0,
            file_row_count=0,
            system_open_count=0,
            missing_item_codes=[],
        )
        data = excel.build_orders_import_preview_xlsx(db, [], preview)
    else:
        preview = excel.preview_orders_import(db, rows)
        data = excel.build_orders_import_preview_xlsx(db, rows, preview)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base = (file.filename or "siparis").rsplit(".", 1)[0]
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{base}_onizleme_{stamp}.xlsx"'})


@router.post("/imports/{kind}", response_model=ImportResult)
async def upload(
    kind: str,
    file: UploadFile = File(...),
    remove_missing: bool = Query(False, description="Yalnizca siparis importu: listede olmayan acik siparisleri sil"),
    db: Session = Depends(get_db),
    user: User = Depends(require_poweruser),
):
    if kind not in excel.TEMPLATES:
        raise HTTPException(404, "Bilinmeyen import turu")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    content = await file.read()
    try:
        return excel.run_import(db, kind, content, file.filename or "", user.username, remove_missing=remove_missing if kind == "orders" else False)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(400, f"Import basarisiz: {e}")


@router.get("/imports/log", response_model=list[dict])
def import_log(limit: int = 50, db: Session = Depends(get_db), _=Depends(require_user)):
    rows = db.query(ImportLog).order_by(ImportLog.created_at.desc()).limit(limit).all()
    return [
        {"id": r.id, "kind": r.kind, "filename": r.filename, "username": r.username, "inserted": r.inserted, "updated": r.updated, "errors": r.errors.split("\n") if r.errors else [], "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@router.get("/backup.xlsx")
def backup(db: Session = Depends(get_db), _=Depends(require_user)):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return Response(excel.build_backup(db), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="kapasite_yedek_{stamp}.xlsx"'})
