"""Siparis, plan, gerceklesen uretim, durus ve import loglari."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), index=True)
    position_no: Mapped[str] = mapped_column(String(32), default="", index=True)  # siparis pozisyonu (aynı no'da coklu satir)
    customer: Mapped[str] = mapped_column(String(128), default="")
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # siparis tarihi (import / manuel)
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
    semi_finished_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    mode: Mapped[str] = mapped_column(String(8), default="auto")  # auto / manual
    # otomatik planin stratejisi: due_date (termine gore) / revenue (maksimum ciro); manuelde bos
    strategy: Mapped[str] = mapped_column(String(16), default="")
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("plan_revisions.id", ondelete="SET NULL"), nullable=True, index=True)
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


class DailyDataAck(Base):
    """Gunluk veri basligi icin 'bugun degisiklik yok' onayi (baslik bazinda)."""

    __tablename__ = "daily_data_acks"
    __table_args__ = (UniqueConstraint("checkpoint_key", "ack_date", name="uq_daily_data_ack"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_key: Mapped[str] = mapped_column(String(32), index=True)
    ack_date: Mapped[date] = mapped_column(Date, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlanRevision(Base):
    """Onayli plan revizyonu: taslak girdiler + yeniden hesap + onay kaydi."""

    __tablename__ = "plan_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    reason_codes: Mapped[str] = mapped_column(String(256), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    start_week: Mapped[date] = mapped_column(Date, index=True)
    weeks: Mapped[int] = mapped_column(Integer, default=8)
    mode: Mapped[str] = mapped_column(String(16), default="due_date")
    wc_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    horizon_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    replace_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_by: Mapped[str] = mapped_column(String(64), default="")
    reject_note: Mapped[str] = mapped_column(String(512), default="")

    changes: Mapped[list["PlanRevisionChange"]] = relationship(back_populates="revision", cascade="all, delete-orphan")
    snapshots: Mapped[list["PlanRevisionSnapshot"]] = relationship(back_populates="revision", cascade="all, delete-orphan")
    events: Mapped[list["PlanRevisionEvent"]] = relationship(back_populates="revision", cascade="all, delete-orphan")


class PlanRevisionChange(Base):
    __tablename__ = "plan_revision_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("plan_revisions.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # order | wc_week
    entity_id: Mapped[int] = mapped_column(Integer, default=0)
    extra_key: Mapped[str] = mapped_column(String(64), default="")  # wc_id|YYYY-MM-DD | item_code
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")

    revision: Mapped[PlanRevision] = relationship(back_populates="changes")


class PlanRevisionSnapshot(Base):
    __tablename__ = "plan_revision_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("plan_revisions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # baseline | proposed
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    revision: Mapped[PlanRevision] = relationship(back_populates="snapshots")


class PlanRevisionEvent(Base):
    __tablename__ = "plan_revision_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("plan_revisions.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(24))
    username: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    revision: Mapped[PlanRevision] = relationship(back_populates="events")
