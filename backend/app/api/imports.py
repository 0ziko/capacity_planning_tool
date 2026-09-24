"""Excel import / sablon / yedek."""

from datetime import date, datetime

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


@router.get("/exports/wc-weeks.xlsx")
def export_wc_weeks(start: date, weeks: int = Query(12, ge=1, le=60),
                    work_center_ids: list[int] | None = Query(None),
                    db: Session = Depends(get_db), _=Depends(require_user)):
    return Response(excel.build_wc_weeks_xlsx(db, start, weeks, work_center_ids), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="haftalik_is_gucu_{start.isoformat()}.xlsx"'})


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


@router.post("/imports/erp-excel/preview")
async def erp_excel_preview(
    file: UploadFile = File(...),
    warehouses: list[str] | None = Query(None, description="Bitmiş ürün sayılacak depo kodları (boş = dosyadaki tüm depolar)"),
    close_missing: bool = Query(True, description="Listede olmayan açık siparişleri kapat"),
    zero_missing: bool = Query(True, description="Seçili depolarda görünmeyen ürünün stoğunu 0'a çek"),
    db: Session = Depends(get_db), _=Depends(require_poweruser),
):
    """ERP Excel ("Sipariş Ana Veri" + "Depo - Ana Veri") ön izleme: ne eklenir, ne güncellenir, ne kapanır, stok farkları."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    from app.services import erp_excel_sync
    try:
        return erp_excel_sync.preview(db, await file.read(), warehouses=warehouses or None, close_missing=close_missing, zero_missing=zero_missing)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Dosya okunamadı: {e}")


@router.post("/imports/erp-excel/apply")
async def erp_excel_apply(
    file: UploadFile = File(...),
    do_orders: bool = Query(True), do_stock: bool = Query(True),
    warehouses: list[str] | None = Query(None), close_missing: bool = Query(True), zero_missing: bool = Query(True),
    db: Session = Depends(get_db), user: User = Depends(require_poweruser),
):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    from app.services import erp_excel_sync
    try:
        return erp_excel_sync.apply(db, await file.read(), username=user.username, filename=file.filename or "", do_orders=do_orders, do_stock=do_stock,
                                    warehouses=warehouses or None, close_missing=close_missing, zero_missing=zero_missing)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(400, f"Eşitleme başarısız: {e}")


@router.post("/imports/line-dizilim/preview")
async def line_dizilim_preview(file: UploadFile = File(...), db: Session = Depends(get_db), _=Depends(require_poweruser)):
    """Saha tablosu (Tavlama_Yikama_Onceliklendirme.xlsx): yıkama makinesi uygunluğu + dizilim, tavlama dizilimi; ön izleme."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    from app.services import line_dizilim_sync
    try:
        return line_dizilim_sync.preview(db, await file.read())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Dosya okunamadı: {e}")


@router.post("/imports/line-dizilim/apply")
async def line_dizilim_apply(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Yalnizca .xlsx dosyalari kabul edilir")
    from app.services import line_dizilim_sync
    try:
        return line_dizilim_sync.apply(db, await file.read(), username=user.username, filename=file.filename or "")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(400, f"Eşitleme başarısız: {e}")


@router.post("/imports/{kind}", response_model=ImportResult)
async def upload(
    kind: str,
    file: UploadFile = File(...),
    remove_missing: bool = Query(False, description="Yalnizca siparis importu: listede olmayan acik siparisleri sil"),
    db: Session = Depends(get_db),
    user: User = Depends(require_poweruser),
):
    if kind not in excel.TEMPLATES and kind not in excel.RAW_IMPORTERS:
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
        {"id": r.id, "kind": r.kind, "filename": r.filename, "username": r.username, "inserted": r.inserted, "updated": r.updated, "errors": r.errors.split("\n") if r.errors else [], "warnings": (r.warnings or "").split("\n") if r.warnings else [], "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@router.get("/backup.xlsx")
def backup(db: Session = Depends(get_db), _=Depends(require_user)):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return Response(
        excel.build_backup(db),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="kapasite_veri_aktarim_{stamp}.xlsx"'},
    )
