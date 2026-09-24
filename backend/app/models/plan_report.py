"""Otomatik plan / revizyon onayi sonrasi uretilen plan raporu (kalici kayit)."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PlanReport(Base):
    __tablename__ = "plan_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # auto | revision
    revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    start_week: Mapped[str] = mapped_column(String(10), default="")
    weeks: Mapped[int] = mapped_column(Integer, default=8)
    summary: Mapped[str] = mapped_column(Text, default="")  # tek satir ozet (liste icin)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
