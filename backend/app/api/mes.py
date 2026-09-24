from datetime import date
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.deps import require_user, require_poweruser
from app.db.session import get_db
from app.services import mes, mes_progress

router = APIRouter(prefix="/api/mes", tags=["MES"])


@router.get("/delivery-risk")
def delivery_risk(as_of: date, horizon: int = Query(8, ge=1, le=12), work_center_ids: list[int] | None = Query(None),
                  db: Session = Depends(get_db), _=Depends(require_user)):
    from app.services.delivery_risk import analyze
    return analyze(db, as_of, horizon, work_center_ids)


def contents(file):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "MES raporunu .xlsx olarak yükleyin")
    data = file.file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Dosya en fazla 20 MB olabilir")
    return data


@router.post("/preview")
def preview(file: UploadFile = File(...), db: Session = Depends(get_db), _=Depends(require_poweruser)):
    data = contents(file)
    try:
        return mes.preview(db, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/preview/jobs")
def start_preview_job(file: UploadFile = File(...), user=Depends(require_poweruser)):
    from app.services import mes_preview_jobs
    data = contents(file)
    try:
        return mes_preview_jobs.start(user.id, data)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/preview/jobs/{job_id}")
def get_preview_job(job_id: str, user=Depends(require_poweruser)):
    from app.services import mes_preview_jobs
    result = mes_preview_jobs.get(user.id, job_id)
    if result is None:
        raise HTTPException(404, "MES önizleme takibi bulunamadı. Aynı dosyayı tekrar seçerek önizlemeyi yenileyin; üretim aktarılmadı.")
    return result


@router.post("/import")
def apply(token: str = Query(..., min_length=64, max_length=64), file: UploadFile = File(...),
                db: Session = Depends(get_db), user=Depends(require_poweruser)):
    data = contents(file)
    try:
        result = mes.apply_import(db, data, token, user.username, file.filename or "mes.xlsx")
        db.commit()
        return result
    except (ValueError, IntegrityError) as e:
        db.rollback()
        raise HTTPException(409, str(e) if isinstance(e, ValueError) else "Veri değişti. Önizlemeyi yenileyin.") from e


@router.post("/import/jobs")
def start_import_job(request_id: str = Query(..., pattern="^[a-f0-9]{32}$"),
                     token: str = Query(..., min_length=64, max_length=64),
                     file: UploadFile = File(...), user=Depends(require_poweruser)):
    from app.services import mes_import_jobs
    data = contents(file)
    try:
        return mes_import_jobs.start(user.id, user.username, request_id, data, token, file.filename or "mes.xlsx")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/import/jobs/{job_id}")
def get_import_job(job_id: str, user=Depends(require_poweruser)):
    from app.services import mes_import_jobs
    result = mes_import_jobs.get(user.id, job_id)
    if result is None:
        raise HTTPException(404, "Bu kullanıcı için kalıcı MES aktarım kaydı bulunamadı. İstek sunucuya ulaşmamış veya eski sürümde başlatılmış olabilir.")
    return result


@router.get("/progress")
def progress(as_of: date, work_center_ids: list[int] | None = Query(None), horizon: int = Query(4, ge=1, le=12),
             db: Session = Depends(get_db), _=Depends(require_user)):
    return mes_progress.progress(db, as_of, work_center_ids, horizon)


@router.get("/progress.xlsx")
def export(as_of: date, work_center_ids: list[int] | None = Query(None), horizon: int = Query(4, ge=1, le=12),
           db: Session = Depends(get_db), _=Depends(require_user)):
    from app.services.mes_export import export_report
    return Response(export_report(mes_progress.progress(db, as_of, work_center_ids, horizon)),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="MES_ilerleme_{as_of}.xlsx"'})


@router.get("/source-status")
def source_status(db: Session = Depends(get_db), _=Depends(require_user)):
    from app.core.config import get_settings
    from app.models.mes import MesDetail
    return {"production_source": get_settings().production_source,
            "has_mes_records": db.query(MesDetail.detail_id).first() is not None,
            "automatic_cutover": False}


@router.get("/free-stock")
def free_stock(as_of: date | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    return mes.free_stock_rows(db, as_of)


@router.get("/inventory")
def inventory(as_of: date | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    from app.services.mes_inventory import report
    return report(db, as_of or date.today())


@router.get("/inventory.xlsx")
def inventory_export(as_of: date | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    from app.services.mes_inventory import report
    from app.services.excel import build_report
    r = report(db, as_of or date.today())
    content = build_report({
        "Bilgi": (["Açıklama"], [[n] for n in r["notes"]]),
        "Bakiyeler": (["Malzeme", "Açılış", "Üretim", "Bilinen tüketim", "Kalan", "Belirsiz tüketim", "Kullanılabilir"],
            [[x[k] for k in ("material_code", "opening_qty", "produced_qty", "consumed_qty", "balance", "pending_consumption_qty", "available_qty")] for x in r["rows"]]),
        "Hareketler": (["Tarih", "MES ID", "Malzeme", "Hareket", "Miktar", "Bakiye", "Üretilen kod", "Makine"],
            [[x[k] for k in ("day", "detail_id", "material_code", "kind", "quantity", "balance_after", "output_code", "machine_code")] for x in r["movements"]]),
        "Belirsiz tüketimler": (["Tarih", "MES ID", "Üretilen kod", "Miktar", "Açıklama"],
            [[x[k] for k in ("day", "detail_id", "output_code", "quantity", "reason")] for x in r["pending"]])})
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="MES_stok_hareketleri.xlsx"'})
