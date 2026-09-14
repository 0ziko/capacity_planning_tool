"""Gunluk veri guncellik API."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_user
from app.db.session import get_db
from app.models import User
from app.schemas import DataFreshnessOut
from app.services.data_freshness import confirm_no_change, daily_freshness
from app.services.data_integrity import audit_data, critical_table_counts

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/data-freshness", response_model=DataFreshnessOut)
def get_data_freshness(db: Session = Depends(get_db), _=Depends(require_user)):
    return daily_freshness(db)


@router.get("/data-integrity")
def get_data_integrity(db: Session = Depends(get_db), _=Depends(require_user)):
    return {"counts": critical_table_counts(db), **audit_data(db)}


@router.post("/data-freshness/checkpoints/{checkpoint_key}/confirm-no-change", response_model=DataFreshnessOut)
def post_confirm_no_change(
    checkpoint_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return confirm_no_change(db, checkpoint_key, user.username)
