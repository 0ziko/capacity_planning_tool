"""Siparis, plan, gerceklesen uretim, durus ve import loglari."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), index=True)
    position_no: Mapped[str] = mapped_column(String(32), default="", index=True)  # siparis pozisyonu (aynı no'da coklu satir)
    customer: Mapped[str] = mapped_column(String(128), default="")
    due_date: Mapped[date] = mapped_column(Date, index=True)
    revised_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # dolu ise planlama bu tarihi kullanir
    market: Mapped[str] = mapped_column(String(16), default="domestic")  # domestic / export
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    # birim satis fiyati (ciro = miktar x birim fiyat); para birimi uygulama genelinde tek kabul edilir
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open / closed / merged (legacy)
    # Legacy: birlestirilmis siparis (migrate edildi); yeni akista kullanilmaz
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    item = relationship("Item")
    merged_into = relationship("Order", remote_side="Order.id", foreign_keys=[merged_into_id])
    plan_lines: Mapped[list["PlanLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    batch_links: Mapped[list["ProductionBatchOrder"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class ProductionBatch(Base):
    """Ayni stok kodundan birden fazla siparis icin birlestirilmis uretim partisi. Siparisler acik kalir."""

    __tablename__ = "production_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open / closed
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    item = relationship("Item")
    orders: Mapped[list["ProductionBatchOrder"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    plan_lines: Mapped[list["PlanLine"]] = relationship(back_populates="production_batch", cascade="all, delete-orphan")


class ProductionBatchOrder(Base):
    """Parti icindeki siparis satiri (miktar siparis talebinden)."""

    __tablename__ = "production_batch_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("production_batches.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True, unique=True)
    quantity: Mapped[float] = mapped_column(Float)

    batch = relationship("ProductionBatch", back_populates="orders")
    order = relationship("Order", back_populates="batch_links")


class PlanLine(Base):
    """Bir siparis veya uretim partisi operasyonunun belirli bir haftaya yerlestirilmis is gucu saati."""

    __tablename__ = "plan_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    production_batch_id: Mapped[int | None] = mapped_column(ForeignKey("production_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("routing_operations.id", ondelete="CASCADE"))
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)  # Pazartesi
    planned_hours: Mapped[float] = mapped_column(Float)
    planned_qty: Mapped[float] = mapped_column(Float, default=0.0)
    mode: Mapped[str] = mapped_column(String(8), default="auto")  # auto / manual
    # otomatik planin stratejisi: due_date (termine gore) / revenue (maksimum ciro); manuelde bos
    strategy: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="plan_lines")
    production_batch: Mapped["ProductionBatch | None"] = relationship(back_populates="plan_lines")
    operation = relationship("RoutingOperation")
    work_center = relationship("WorkCenter")


class ProductionActual(Base):
    """Gunluk import edilen uretim sonucu (bir onceki gun).

    order_no bos olabilir: uretim operasyonel beyanidir; siparis baglantisi zorunlu degildir.
    """

    __tablename__ = "production_actuals"

    id: Mapped[int] = mapped_column(primary_key=True)
    prod_date: Mapped[date] = mapped_column(Date, index=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    operation_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_no: Mapped[str] = mapped_column(String(64), default="")
    semi_finished_code: Mapped[str] = mapped_column(String(64), default="", index=True)
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


class StockReceipt(Base):
    """Bitmis urun depo girisi (uretimden depoya alinan miktar). Siparisten bagimsizdir."""

    __tablename__ = "stock_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    receipt_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[float] = mapped_column(Float)
    lot: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(256), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual / import / progress
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    item = relationship("Item")


class Reservation(Base):
    """Depodaki bitmis urunun bir siparise rezervasyonu (sevk edilene kadar acik)."""

    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(8), default="auto")  # auto / manual
    note: Mapped[str] = mapped_column(String(256), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    item = relationship("Item")
    order = relationship("Order")


class Shipment(Base):
    """Siparise sevk edilen miktar; stoktan duser."""

    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    ship_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    item = relationship("Item")
    order = relationship("Order")


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
