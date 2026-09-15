"""MES detail ledger. Independent of customers and legacy daily aggregates."""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class MesDetail(Base):
    __tablename__ = "mes_details"
    detail_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    prod_date: Mapped[date] = mapped_column(Date, index=True)
    material_code: Mapped[str] = mapped_column(String(64), index=True)
    machine_code: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[float] = mapped_column(Float)
    mapping: Mapped[dict] = mapped_column(JSON)
    # Deliberately no foreign key: history survives routing/plan replacement.
    receipt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class MesPlanBaseline(Base):
    __tablename__ = "mes_plan_baselines"
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    lines: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
