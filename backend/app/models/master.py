"""Master data: is merkezleri, vardiyalar, personel, stok kodlari, BOM, rota."""

from datetime import date, time

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class WorkCenter(Base):
    __tablename__ = "work_centers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(256), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Pilot: bu is merkezi planlamaya dahil mi?
    is_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    # "1 birim" = kac saat (orn. 10)
    capacity_unit_hours: Mapped[float] = mapped_column(Float, default=10.0)
    # Vardiya satirinda deger yoksa kullanilacak kisi basi verimli saat/gun
    default_efficient_hours: Mapped[float] = mapped_column(Float, default=4.0)
    # Alan (is merkezi grubu): orn. PRS / PRESHANELER
    area_code: Mapped[str] = mapped_column(String(32), default="")
    area_name: Mapped[str] = mapped_column(String(128), default="")
    # Kapasite kisi sayisi kaynagi:
    #   work_center -> vardiya kisi sayisi, yoksa is merkezine bagli aktif personel (varsayilan)
    #   machines    -> bu is merkezinin aktif makinelerine atanmis aktif personel (makine detayi aktif)
    capacity_source: Mapped[str] = mapped_column(String(16), default="work_center")

    shifts: Mapped[list["WorkCenterShift"]] = relationship(
        back_populates="work_center", cascade="all, delete-orphan", order_by="WorkCenterShift.id"
    )
    employees: Mapped[list["Employee"]] = relationship(back_populates="work_center", foreign_keys="Employee.work_center_id")
    machines: Mapped[list["Machine"]] = relationship(back_populates="work_center", cascade="all, delete-orphan", order_by="Machine.code")


class WorkCenterWeek(Base):
    """Haftalik is gucu istisnasi: belirli bir haftada kisi sayisi / kisi basi verimli saat /
    calisma gunu sayisi farkliysa buraya yazilir. Bos (None) alanlar varsayilan (vardiya, personel,
    makine atamasi) davranisi korur. Kapasite, plan, terminleme ve durus analizi bu degerleri kullanir."""

    __tablename__ = "work_center_weeks"
    __table_args__ = (UniqueConstraint("work_center_id", "week_start", name="uq_wc_week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)  # Pazartesi
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    efficient_hours_per_person: Mapped[float | None] = mapped_column(Float, nullable=True)
    working_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0..7; None => vardiya gunleri
    note: Mapped[str] = mapped_column(String(256), default="")

    work_center: Mapped[WorkCenter] = relationship()


class Machine(Base):
    """Is merkezi altindaki makine / tezgah. Personel makineye atanabilir; is merkezi
    capacity_source='machines' ise kapasite bu atamalardan hesaplanir."""

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(String(256), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    work_center: Mapped[WorkCenter] = relationship(back_populates="machines")
    employees: Mapped[list["Employee"]] = relationship(back_populates="machine")


class WorkCenterShift(Base):
    """Bir is merkezinin vardiya tanimi.

    weekdays: "0,1,2,3,4" (0=Pazartesi ... 6=Pazar)
    headcount: vardiyada calisan kisi sayisi (0 => personel listesinden sayilir)
    efficient_hours_per_person: kisi basi verimli calisma suresi (saat/gun)
    """

    __tablename__ = "work_center_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="Gunduz")
    weekdays: Mapped[str] = mapped_column(String(32), default="0,1,2,3,4")
    start_time: Mapped[time] = mapped_column(Time, default=time(8, 0))
    end_time: Mapped[time] = mapped_column(Time, default=time(18, 0))
    headcount: Mapped[int] = mapped_column(Integer, default=0)
    efficient_hours_per_person: Mapped[float | None] = mapped_column(Float, nullable=True)

    work_center: Mapped[WorkCenter] = relationship(back_populates="shifts")

    def weekday_set(self) -> set[int]:
        return {int(x) for x in self.weekdays.split(",") if x.strip() != ""}

    def nominal_hours(self) -> float:
        start = self.start_time.hour + self.start_time.minute / 60
        end = self.end_time.hour + self.end_time.minute / 60
        if end <= start:  # gece vardiyasi
            end += 24
        return end - start


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    work_center_id: Mapped[int | None] = mapped_column(ForeignKey("work_centers.id", ondelete="SET NULL"), nullable=True)
    # Makine atamasi (istege bagli); makine, personelin is merkezine ait olmali
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    work_center: Mapped[WorkCenter | None] = relationship(back_populates="employees", foreign_keys=[work_center_id])
    machine: Mapped["Machine | None"] = relationship(back_populates="employees")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    product_group: Mapped[str] = mapped_column(String(64), default="")
    unit: Mapped[str] = mapped_column(String(16), default="AD")

    bom_lines: Mapped[list["BomLine"]] = relationship(back_populates="item", cascade="all, delete-orphan")
    operations: Mapped[list["RoutingOperation"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="RoutingOperation.seq"
    )


class BomLine(Base):
    """Hammadde / yari mamul satiri."""

    __tablename__ = "bom_lines"
    __table_args__ = (UniqueConstraint("item_id", "component_code", name="uq_bom_item_component"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    component_code: Mapped[str] = mapped_column(String(64))
    component_name: Mapped[str] = mapped_column(String(256), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str] = mapped_column(String(16), default="AD")

    item: Mapped[Item] = relationship(back_populates="bom_lines")


class RoutingOperation(Base):
    """Asamali tezgah/operasyon: hangi is merkezinde, hangi sirayla, cevrim suresi (sn/adet)."""

    __tablename__ = "routing_operations"
    __table_args__ = (UniqueConstraint("item_id", "seq", name="uq_routing_item_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=10)
    operation_name: Mapped[str] = mapped_column(String(128), default="")
    work_center_id: Mapped[int] = mapped_column(ForeignKey("work_centers.id"), index=True)
    cycle_time_sec: Mapped[float] = mapped_column(Float, default=0.0)
    setup_time_min: Mapped[float] = mapped_column(Float, default=0.0)
    semi_finished_code: Mapped[str] = mapped_column(String(64), default="", index=True)  # operasyon sonu yarımamül

    item: Mapped[Item] = relationship(back_populates="operations")
    work_center: Mapped[WorkCenter] = relationship()

    def hours_for(self, quantity: float) -> float:
        return quantity * self.cycle_time_sec / 3600.0 + self.setup_time_min / 60.0


def norm_op(name: str) -> str:
    """Operasyon adini kural eslemesi icin normalize eder (buyuk/kucuk harf, bosluk, Turkce karakter)."""
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return " ".join(str(name or "").translate(tr).lower().split())


def norm_wip(code: str) -> str:
    """Yarimamul kodu eslemesi: bosluk kirp, buyuk harf."""
    return str(code or "").strip().upper()


class OpTransitionRule(Base):
    """Senaryo matrisi: iki ardisik operasyon arasinda sonraki operasyonun ne zaman baslayabilecegi.

    scope: 'group' (urun grubu geneli) veya 'item' (stok koduna ozel; grup kuralini ezer)
    rule:  'finish' -> onceki operasyon bitince (+ bekleme)
           'cycles' -> onceki operasyon lag_cycles cevrim (adet) tamamlayinca (+ bekleme); 0 = birlikte basla
    wait_minutes: tetikten sonra ek bekleme (kuruma, sogutma, tasima vb.)
    from_op / to_op: operasyon adi (normalize edilerek eslenir) — farkli stoklarin farkli seq'leri olabildigi icin ad kullanilir.
    """

    __tablename__ = "op_transition_rules"
    __table_args__ = (
        UniqueConstraint(
            "scope", "product_group", "item_id", "from_op_norm", "to_op_norm", "from_wip_norm", "to_wip_norm",
            name="uq_op_rule",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), default="group")  # group / item
    product_group: Mapped[str] = mapped_column(String(64), default="", index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=True, index=True)
    from_op: Mapped[str] = mapped_column(String(128))
    to_op: Mapped[str] = mapped_column(String(128))
    from_op_norm: Mapped[str] = mapped_column(String(128), index=True)
    to_op_norm: Mapped[str] = mapped_column(String(128), index=True)
    from_wip_code: Mapped[str] = mapped_column(String(64), default="")  # opsiyonel; operasyon + yarimamul birlikte
    to_wip_code: Mapped[str] = mapped_column(String(64), default="")
    from_wip_norm: Mapped[str] = mapped_column(String(64), default="", index=True)
    to_wip_norm: Mapped[str] = mapped_column(String(64), default="", index=True)
    rule: Mapped[str] = mapped_column(String(8), default="finish")  # finish / cycles
    lag_cycles: Mapped[float] = mapped_column(Float, default=0.0)
    wait_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(256), default="")

    item = relationship("Item")
