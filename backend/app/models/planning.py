"""Siparis, plan, gerceklesen uretim, durus ve import loglari."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), index=True)
    customer: Mapped[str] = mapped_column(String(128), default="")
    due_date: Mapped[date] = mapped_column(Date, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open / closed / merged
    # Birlestirilmis siparis: bu siparis hangi birlesik siparise dahil edildi
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    item = relationship("Item")
    merged_into = relationship("Order", remote_side="Order.id", foreign_keys=[merged_into_id])
    plan_lines: Mapped[list["PlanLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class PlanLine(Base):
    """Bir siparis operasyonunun belirli bir haftaya yerlestirilmis is gucu saati."""

    __tablename__ = "plan_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("routing_operations.id", ondelete="CASCADE"))
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)  # Pazartesi
    planned_hours: Mapped[float] = mapped_column(Float)
    planned_qty: Mapped[float] = mapped_column(Float, default=0.0)
    mode: Mapped[str] = mapped_column(String(8), default="auto")  # auto / manual
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="plan_lines")
    operation = relationship("RoutingOperation")
    work_center = relationship("WorkCenter")


class ProductionActual(Base):
    """Gunluk import edilen uretim sonucu (bir onceki gun)."""

    __tablename__ = "production_actuals"

    id: Mapped[int] = mapped_column(primary_key=True)
    prod_date: Mapped[date] = mapped_column(Date, index=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    operation_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_no: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[float] = mapped_column(Float)
    # cevrim suresine gore hesaplanan is gucu saati
    earned_hours: Mapped[float] = mapped_column(Float, default=0.0)
    # sahadan gelen fiili calisma suresi (varsa) - cevrim suresi onerisi icin
    reported_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    work_center = relationship("WorkCenter")
    item = relationship("Item")


class Downtime(Base):
    __tablename__ = "downtimes"

    id: Mapped[int] = mapped_column(primary_key=True)
    dt_date: Mapped[date] = mapped_column(Date, index=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), index=True)
    reason_code: Mapped[str] = mapped_column(String(32), default="")
    reason_desc: Mapped[str] = mapped_column(String(256), default="")
    minutes: Mapped[float] = mapped_column(Float)

    work_center = relationship("WorkCenter")


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(256), default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
