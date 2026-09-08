"""Owner paneli: toplu veri silme (geri alinamaz)."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    BomLine,
    Downtime,
    Employee,
    ImportLog,
    Item,
    Machine,
    OpTransitionRule,
    Order,
    PlanLine,
    ProductionActual,
    ProductionBatch,
    ProductionBatchOrder,
    Reservation,
    RoutingOperation,
    Shipment,
    StockReceipt,
    WorkCenter,
    WorkCenterShift,
    WorkCenterWeek,
)
from app.services import excel

CONFIRM_TOKEN = "SIL"


def _count(db: Session, model) -> int:
    return db.query(func.count()).select_from(model).scalar() or 0


def dataset_stats(db: Session) -> dict:
    """Owner paneli icin kayit sayilari."""
    import_datasets = [
        {"key": k, "title": t["title"], "count": _dataset_count(db, k)}
        for k, t in excel.TEMPLATES.items()
    ]
    return {
        "orders": _count(db, Order),
        "production_batches": _count(db, ProductionBatch),
        "items": _count(db, Item),
        "work_centers": _count(db, WorkCenter),
        "plan_lines": _count(db, PlanLine),
        "import_logs": _count(db, ImportLog),
        "import_datasets": import_datasets,
    }


def _dataset_count(db: Session, kind: str) -> int:
    m = {
        "workcenters": WorkCenter,
        "machines": Machine,
        "shifts": WorkCenterShift,
        "wc_weeks": WorkCenterWeek,
        "employees": Employee,
        "items": Item,
        "bom": BomLine,
        "routing": RoutingOperation,
        "op_rules": OpTransitionRule,
        "orders": Order,
        "production": ProductionActual,
        "downtime": Downtime,
        "stock_receipts": StockReceipt,
    }
    return _count(db, m[kind]) if kind in m else 0


def purge(db: Session, target: str) -> int:
    """Hedef veriyi siler; silinen kayit sayisini dondurur."""
    fn = {
        "orders": _purge_orders,
        "items": _purge_items,
        "workcenters": _purge_workcenters,
        "plan_lines": _purge_plan_lines,
        "production_batches": _purge_production_batches,
        "import_logs": _purge_import_logs,
        "workcenters_ds": _purge_workcenters,
        "machines": _purge_machines,
        "shifts": _purge_shifts,
        "wc_weeks": _purge_wc_weeks,
        "employees": _purge_employees,
        "items_ds": _purge_items_only,
        "bom": _purge_bom,
        "routing": _purge_routing,
        "op_rules": _purge_op_rules,
        "orders_ds": _purge_orders,
        "production": _purge_production,
        "downtime": _purge_downtime,
        "stock_receipts": _purge_stock_receipts,
        "reservations": _purge_reservations,
        "shipments": _purge_shipments,
    }
    if target not in fn:
        raise ValueError(f"Bilinmeyen hedef: {target}")
    return fn[target](db)


def _purge_plan_lines(db: Session) -> int:
    n = _count(db, PlanLine)
    db.query(PlanLine).delete(synchronize_session=False)
    return n


def _purge_production_batches(db: Session) -> int:
    n = _count(db, ProductionBatch)
    db.query(ProductionBatchOrder).delete(synchronize_session=False)
    db.query(PlanLine).filter(PlanLine.production_batch_id.isnot(None)).delete(synchronize_session=False)
    db.query(ProductionBatch).delete(synchronize_session=False)
    return n


def _purge_orders(db: Session) -> int:
    _purge_production_batches(db)
    ids = [i for (i,) in db.query(Order.id).all()]
    if not ids:
        return 0
    db.query(PlanLine).filter(PlanLine.order_id.in_(ids)).delete(synchronize_session=False)
    db.query(Reservation).filter(Reservation.order_id.in_(ids)).delete(synchronize_session=False)
    db.query(Shipment).filter(Shipment.order_id.in_(ids)).delete(synchronize_session=False)
    db.query(Order).filter(Order.merged_into_id.in_(ids)).update({Order.merged_into_id: None}, synchronize_session=False)
    return db.query(Order).filter(Order.id.in_(ids)).delete(synchronize_session=False)


def _purge_reservations(db: Session) -> int:
    n = _count(db, Reservation)
    db.query(Reservation).delete(synchronize_session=False)
    return n


def _purge_shipments(db: Session) -> int:
    n = _count(db, Shipment)
    db.query(Shipment).delete(synchronize_session=False)
    return n


def _purge_production(db: Session) -> int:
    n = _count(db, ProductionActual)
    db.query(ProductionActual).delete(synchronize_session=False)
    return n


def _purge_downtime(db: Session) -> int:
    n = _count(db, Downtime)
    db.query(Downtime).delete(synchronize_session=False)
    return n


def _purge_stock_receipts(db: Session) -> int:
    n = _count(db, StockReceipt)
    db.query(StockReceipt).delete(synchronize_session=False)
    return n


def _purge_bom(db: Session) -> int:
    n = _count(db, BomLine)
    db.query(BomLine).delete(synchronize_session=False)
    return n


def _purge_routing(db: Session) -> int:
    n = _count(db, RoutingOperation)
    db.query(PlanLine).delete(synchronize_session=False)
    db.query(RoutingOperation).delete(synchronize_session=False)
    return n


def _purge_op_rules(db: Session) -> int:
    n = _count(db, OpTransitionRule)
    db.query(OpTransitionRule).delete(synchronize_session=False)
    return n


def _purge_machines(db: Session) -> int:
    n = _count(db, Machine)
    db.query(Employee).filter(Employee.machine_id.isnot(None)).update({Employee.machine_id: None}, synchronize_session=False)
    db.query(Machine).delete(synchronize_session=False)
    return n


def _purge_shifts(db: Session) -> int:
    n = _count(db, WorkCenterShift)
    db.query(WorkCenterShift).delete(synchronize_session=False)
    return n


def _purge_wc_weeks(db: Session) -> int:
    n = _count(db, WorkCenterWeek)
    db.query(WorkCenterWeek).delete(synchronize_session=False)
    return n


def _purge_employees(db: Session) -> int:
    n = _count(db, Employee)
    db.query(Employee).delete(synchronize_session=False)
    return n


def _purge_items_only(db: Session) -> int:
    """Stok kodlari (BOM/rota/siparis baglantisi olmadan — once bagimliliklari temizler)."""
    _purge_orders(db)
    _purge_production(db)
    _purge_stock_receipts(db)
    _purge_reservations(db)
    _purge_shipments(db)
    _purge_bom(db)
    _purge_routing(db)
    db.query(OpTransitionRule).delete(synchronize_session=False)
    n = _count(db, Item)
    db.query(Item).delete(synchronize_session=False)
    return n


def _purge_items(db: Session) -> int:
    return _purge_items_only(db)


def _purge_workcenters(db: Session) -> int:
    db.query(PlanLine).delete(synchronize_session=False)
    db.query(ProductionActual).delete(synchronize_session=False)
    db.query(Downtime).delete(synchronize_session=False)
    db.query(RoutingOperation).delete(synchronize_session=False)
    db.query(Employee).update({Employee.work_center_id: None, Employee.machine_id: None}, synchronize_session=False)
    n = _count(db, WorkCenter)
    db.query(WorkCenter).delete(synchronize_session=False)
    return n


def _purge_import_logs(db: Session) -> int:
    n = _count(db, ImportLog)
    db.query(ImportLog).delete(synchronize_session=False)
    return n
