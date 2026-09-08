"""Gunluk operasyonel veri guncellik kontrolu."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ImportLog, Order, ProductionActual, StockReceipt

# checkpoint key -> excel import kind (WIP uretim importundan turetilir)
CHECKPOINTS: tuple[dict, ...] = (
    {"key": "finished_stock", "label": "Bitmiş Ürün Stoğu", "import_kind": "stock_receipts"},
    {"key": "wip_stock", "label": "Yarımamül Stoğu", "import_kind": "production", "from_production": True},
    {"key": "production_output", "label": "Üretim Çıktıları", "import_kind": "production"},
    {"key": "open_orders", "label": "Açık Siparişler", "import_kind": "orders"},
)


def _last_import(db: Session, kind: str) -> ImportLog | None:
    rows = (
        db.query(ImportLog)
        .filter(ImportLog.kind == kind)
        .order_by(ImportLog.created_at.desc())
        .limit(5)
        .all()
    )
    for row in rows:
        if not (row.errors or "").strip():
            return row
    return None


def _max_prod_date(db: Session, *, wip_only: bool = False) -> date | None:
    q = db.query(func.max(ProductionActual.prod_date))
    if wip_only:
        q = q.filter(ProductionActual.semi_finished_code != "")
    return q.scalar()


def _max_receipt_date(db: Session) -> date | None:
    return db.query(func.max(StockReceipt.receipt_date)).scalar()


def _checkpoint_status(last_import_at: datetime | None, today: date) -> str:
    if last_import_at is None:
        return "missing"
    if last_import_at.date() >= today:
        return "ok"
    return "stale"


def _detail_tr(label: str, status: str, last_import_at: datetime | None, last_data_date: date | None, today: date) -> str:
    if status == "missing":
        return f"{label} henüz import edilmemiş — bugün ({today.strftime('%d.%m.%Y')}) güncellenmeli."
    if status == "ok":
        parts = ["Bugün güncellendi"]
        if last_data_date:
            parts.append(f"veri tarihi {last_data_date.strftime('%d.%m.%Y')}")
        return " · ".join(parts) + "."
    assert last_import_at is not None
    days = (today - last_import_at.date()).days
    when = last_import_at.strftime("%d.%m.%Y %H:%M")
    msg = f"Son import {when} — bugün güncellenmeli."
    if days > 0:
        msg = f"Son import {when} ({days} gün önce) — bugün güncellenmeli."
    if last_data_date:
        msg += f" Son veri tarihi: {last_data_date.strftime('%d.%m.%Y')}."
    return msg


def daily_freshness(db: Session, *, today: date | None = None) -> dict:
    today = today or date.today()
    checkpoints: list[dict] = []
    needs_attention = False

    prod_log = _last_import(db, "production")
    prod_import_at = prod_log.created_at if prod_log else None
    max_prod = _max_prod_date(db, wip_only=False)
    max_wip = _max_prod_date(db, wip_only=True)

    for cp in CHECKPOINTS:
        kind = cp["import_kind"]
        if cp.get("from_production"):
            log = prod_log
            last_import_at = prod_import_at
            last_data_date = max_wip
        else:
            log = _last_import(db, kind)
            last_import_at = log.created_at if log else None
            if cp["key"] == "finished_stock":
                last_data_date = _max_receipt_date(db)
            elif cp["key"] == "production_output":
                last_data_date = max_prod
            elif cp["key"] == "open_orders":
                last_data_date = last_import_at.date() if last_import_at else None
            else:
                last_data_date = None

        status = _checkpoint_status(last_import_at, today)
        if status != "ok":
            needs_attention = True

        checkpoints.append(
            {
                "key": cp["key"],
                "label": cp["label"],
                "import_kind": kind,
                "status": status,
                "last_import_at": last_import_at.isoformat() if last_import_at else None,
                "last_import_by": (log.username if log else "") or "",
                "last_data_date": last_data_date.isoformat() if last_data_date else None,
                "detail": _detail_tr(cp["label"], status, last_import_at, last_data_date, today),
            }
        )

    open_count = db.query(func.count()).select_from(Order).filter(Order.status == "open").scalar() or 0

    return {
        "today": today.isoformat(),
        "needs_attention": needs_attention,
        "open_order_count": open_count,
        "checkpoints": checkpoints,
    }
