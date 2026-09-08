"""Gunluk veri guncellik API."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_user
from app.db.session import get_db
from app.models import User
from app.schemas import DataFreshnessOut
from app.services.data_freshness import daily_freshness

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/data-freshness", response_model=DataFreshnessOut)
def get_data_freshness(db: Session = Depends(get_db), _=Depends(require_user)):
    return daily_freshness(db)
