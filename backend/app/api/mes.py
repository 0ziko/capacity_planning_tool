from datetime import date
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.deps import require_user, require_poweruser
from app.db.session import get_db
from app.services import mes, mes_progress

router = APIRouter(prefix="/api/mes", tags=["MES"])


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
