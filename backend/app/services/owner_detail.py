"""Owner paneli: kayit listesi, filtreleme ve secili silme."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Callable

from sqlalchemy import func, or_
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
from app.services.owner_purge import purge

PURGE_TO_IMPORT_KIND: dict[str, str] = {
    "workcenters_ds": "workcenters",
    "workcenters": "workcenters",
    "machines": "machines",
    "shifts": "shifts",
    "wc_weeks": "wc_weeks",
    "employees": "employees",
    "items_ds": "items",
    "items": "items",
    "bom": "bom",
    "routing": "routing",
    "op_rules": "op_rules",
    "orders_ds": "orders",
    "orders": "orders",
    "production": "production",
    "downtime": "downtime",
    "stock_receipts": "stock_receipts",
}

MAX_PURGE_IDS = 5000
MAX_ID_LIST = 10000


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59, 999999))


def _parse_dt(value: str | None, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if len(raw) == 10:
        d = date.fromisoformat(raw)
        return _day_end(d) if end_of_day else datetime.combine(d, time.min)
    return datetime.fromisoformat(raw)


class TargetSpec:
    __slots__ = ("model", "date_attr", "user_attr", "date_only", "label_fn", "subtitle_fn", "search_attrs", "order_attr", "import_kind")

    def __init__(
        self,
        model,
        *,
        date_attr: str | None = None,
        user_attr: str | None = None,
        date_only: bool = False,
        label_fn: Callable[[Any], str] | None = None,
        subtitle_fn: Callable[[Any], str] | None = None,
        search_attrs: tuple[str, ...] = (),
        order_attr: str = "id",
        import_kind: str | None = None,
    ):
        self.model = model
        self.date_attr = date_attr
        self.user_attr = user_attr
        self.date_only = date_only
        self.label_fn = label_fn or (lambda r: str(getattr(r, "id", "")))
        self.subtitle_fn = subtitle_fn or (lambda _r: "")
        self.search_attrs = search_attrs
        self.order_attr = order_attr
        self.import_kind = import_kind

    @property
    def has_datetime(self) -> bool:
        return bool(self.date_attr)


def _order_label(o: Order) -> str:
    return f"{o.order_no}{('/' + o.position_no) if o.position_no else ''}"


TARGET_SPECS: dict[str, TargetSpec] = {
    "orders": TargetSpec(Order, date_attr="created_at", label_fn=_order_label, search_attrs=("order_no", "customer"), import_kind="orders"),
    "orders_ds": TargetSpec(Order, date_attr="created_at", label_fn=_order_label, search_attrs=("order_no", "customer"), import_kind="orders"),
    "production": TargetSpec(
        ProductionActual, date_attr="prod_date", date_only=True, import_kind="production",
        label_fn=lambda r: r.order_no or r.semi_finished_code or f"item#{r.item_id}",
        subtitle_fn=lambda r: f"{r.prod_date} · {r.quantity} ad",
        search_attrs=("order_no", "semi_finished_code"),
        order_attr="prod_date",
    ),
    "downtime": TargetSpec(Downtime, date_attr="dt_date", date_only=True, import_kind="downtime", label_fn=lambda r: r.reason_desc or r.reason_code or f"WC#{r.work_center_id}", order_attr="dt_date"),
    "plan_lines": TargetSpec(PlanLine, date_attr="created_at", user_attr="created_by", label_fn=lambda r: f"Hafta {r.week_start}", subtitle_fn=lambda r: f"{r.planned_hours:.1f} sa · siparis#{r.order_id}"),
    "production_batches": TargetSpec(ProductionBatch, date_attr="created_at", user_attr="created_by", label_fn=lambda r: r.batch_no or f"parti-{r.id}", search_attrs=("batch_no",)),
    "reservations": TargetSpec(Reservation, date_attr="created_at", user_attr="created_by", label_fn=lambda r: f"Siparis #{r.order_id}", subtitle_fn=lambda r: f"{r.quantity} ad"),
    "shipments": TargetSpec(Shipment, date_attr="created_at", user_attr="created_by", label_fn=lambda r: f"Siparis #{r.order_id}", subtitle_fn=lambda r: f"{r.ship_date} · {r.quantity} ad"),
    "stock_receipts": TargetSpec(StockReceipt, date_attr="created_at", user_attr="created_by", import_kind="stock_receipts", label_fn=lambda r: f"item#{r.item_id}", subtitle_fn=lambda r: f"{r.receipt_date} · {r.quantity} ad"),
    "import_logs": TargetSpec(ImportLog, date_attr="created_at", user_attr="username", label_fn=lambda r: r.filename or r.kind, subtitle_fn=lambda r: f"+{r.inserted} / ~{r.updated}", search_attrs=("filename", "kind")),
    "items": TargetSpec(Item, label_fn=lambda r: r.code, subtitle_fn=lambda r: r.name, search_attrs=("code", "name"), import_kind="items"),
    "items_ds": TargetSpec(Item, label_fn=lambda r: r.code, subtitle_fn=lambda r: r.name, search_attrs=("code", "name"), import_kind="items"),
    "workcenters": TargetSpec(WorkCenter, label_fn=lambda r: r.code, subtitle_fn=lambda r: r.name, search_attrs=("code", "name"), import_kind="workcenters"),
    "workcenters_ds": TargetSpec(WorkCenter, label_fn=lambda r: r.code, subtitle_fn=lambda r: r.name, search_attrs=("code", "name"), import_kind="workcenters"),
    "machines": TargetSpec(Machine, label_fn=lambda r: r.code, subtitle_fn=lambda r: r.name, search_attrs=("code", "name"), import_kind="machines"),
    "shifts": TargetSpec(WorkCenterShift, label_fn=lambda r: r.name, subtitle_fn=lambda r: f"WC#{r.work_center_id}", import_kind="shifts"),
    "wc_weeks": TargetSpec(WorkCenterWeek, label_fn=lambda r: str(r.week_start), subtitle_fn=lambda r: f"WC#{r.work_center_id}", import_kind="wc_weeks"),
    "employees": TargetSpec(Employee, label_fn=lambda r: r.code, subtitle_fn=lambda r: r.name, search_attrs=("code", "name"), import_kind="employees"),
    "bom": TargetSpec(BomLine, label_fn=lambda r: r.component_code, subtitle_fn=lambda r: r.component_name, search_attrs=("component_code", "component_name"), import_kind="bom"),
    "routing": TargetSpec(RoutingOperation, label_fn=lambda r: r.operation_name or f"op.{r.seq}", subtitle_fn=lambda r: f"item#{r.item_id} · seq {r.seq}", import_kind="routing"),
    "op_rules": TargetSpec(OpTransitionRule, label_fn=lambda r: f"{r.from_op} → {r.to_op}", subtitle_fn=lambda r: r.note, search_attrs=("from_op", "to_op"), import_kind="op_rules"),
}


def target_title(target: str) -> str:
    kind = PURGE_TO_IMPORT_KIND.get(target, target)
    if kind in excel.TEMPLATES:
        return excel.TEMPLATES[kind]["title"]
    titles = {
        "plan_lines": "Plan satirlari",
        "production_batches": "Uretim partileri",
        "reservations": "Stok rezervasyonlari",
        "shipments": "Sevk kayitlari",
        "import_logs": "Import gecmisi",
        "orders": "Siparisler",
        "items": "Stok kodlari",
        "workcenters": "Is merkezleri",
    }
    return titles.get(target, target)


def _spec(target: str) -> TargetSpec:
    spec = TARGET_SPECS.get(target)
    if not spec:
        raise ValueError(f"Bilinmeyen hedef: {target}")
    return spec


def _apply_filters(q, spec: TargetSpec, from_dt: datetime | None, to_dt: datetime | None, username: str | None, search: str | None):
    col = getattr(spec.model, spec.date_attr, None) if spec.date_attr else None
    if col is not None:
        if spec.date_only:
            if from_dt:
                q = q.filter(col >= from_dt.date())
            if to_dt:
                q = q.filter(col <= to_dt.date())
        else:
            if from_dt:
                q = q.filter(col >= from_dt)
            if to_dt:
                q = q.filter(col <= to_dt)
    if username and spec.user_attr:
        q = q.filter(getattr(spec.model, spec.user_attr) == username)
    if search and spec.search_attrs:
        term = f"%{search.strip()}%"
        q = q.filter(or_(*[getattr(spec.model, a).ilike(term) for a in spec.search_attrs]))
    return q


def _row_dict(spec: TargetSpec, r) -> dict:
    raw_dt = getattr(r, spec.date_attr, None) if spec.date_attr else None
    if raw_dt is None:
        dt_iso = None
    elif spec.date_only:
        dt_iso = raw_dt.isoformat() if isinstance(raw_dt, date) else raw_dt.date().isoformat()
    else:
        dt_iso = raw_dt.isoformat()
    user = getattr(r, spec.user_attr, None) if spec.user_attr else None
    return {
        "id": r.id,
        "label": spec.label_fn(r),
        "subtitle": spec.subtitle_fn(r) or "",
        "username": user or "—",
        "datetime": dt_iso,
    }


def _import_batches(db: Session, kind: str | None, from_dt, to_dt, username, limit: int) -> list[dict]:
    if not kind:
        return []
    q = db.query(ImportLog).filter(ImportLog.kind == kind)
    if from_dt:
        q = q.filter(ImportLog.created_at >= from_dt)
    if to_dt:
        q = q.filter(ImportLog.created_at <= to_dt)
    if username:
        q = q.filter(ImportLog.username == username)
    rows = q.order_by(ImportLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "username": r.username,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "inserted": r.inserted,
            "updated": r.updated,
            "has_errors": bool((r.errors or "").strip()),
        }
        for r in rows
    ]


def list_records(
    db: Session,
    target: str,
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    username: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    spec = _spec(target)
    total = db.query(func.count()).select_from(spec.model).scalar() or 0
    fq = _apply_filters(db.query(spec.model), spec, from_dt, to_dt, username, search)
    filtered_count = fq.count()
    order_col = getattr(spec.model, spec.order_attr, spec.model.id)
    rows = fq.order_by(order_col.desc(), spec.model.id.desc()).offset(offset).limit(limit).all()
    users: list[str] = []
    if spec.user_attr:
        users = sorted({u for (u,) in db.query(getattr(spec.model, spec.user_attr)).distinct() if u})
    import_kind = spec.import_kind or PURGE_TO_IMPORT_KIND.get(target)
    return {
        "target": target,
        "title": target_title(target),
        "total_count": total,
        "filtered_count": filtered_count,
        "has_datetime": spec.has_datetime,
        "records": [_row_dict(spec, r) for r in rows],
        "offset": offset,
        "limit": limit,
        "users": users,
        "import_batches": _import_batches(db, import_kind, from_dt, to_dt, username, 50),
    }


def list_record_ids(
    db: Session,
    target: str,
    *,
    from_dt=None,
    to_dt=None,
    username=None,
    search=None,
    limit: int = MAX_ID_LIST,
) -> list[int]:
    spec = _spec(target)
    q = _apply_filters(db.query(spec.model.id), spec, from_dt, to_dt, username, search)
    return [i for (i,) in q.limit(limit).all()]


def count_purge_scope(
    db: Session,
    target: str,
    *,
    ids: list[int] | None,
    delete_all: bool,
    select_filtered: bool,
    from_dt,
    to_dt,
    username,
    search,
) -> int:
    if delete_all:
        return db.query(func.count()).select_from(_spec(target).model).scalar() or 0
    if select_filtered:
        return _apply_filters(db.query(_spec(target).model), _spec(target), from_dt, to_dt, username, search).count()
    if ids:
        return db.query(func.count()).select_from(_spec(target).model).filter(_spec(target).model.id.in_(ids)).scalar() or 0
    raise ValueError("Silme kapsami belirtilmedi (ids, delete_all veya select_filtered)")


def purge_scoped(
    db: Session,
    target: str,
    *,
    ids: list[int] | None = None,
    delete_all: bool = False,
    select_filtered: bool = False,
    from_dt=None,
    to_dt=None,
    username=None,
    search=None,
) -> int:
    if delete_all:
        return purge(db, target)
    if select_filtered:
        ids = list_record_ids(db, target, from_dt=from_dt, to_dt=to_dt, username=username, search=search)
    if not ids:
        return 0
    if len(ids) > MAX_PURGE_IDS:
        raise ValueError(f"Tek seferde en fazla {MAX_PURGE_IDS} kayit silinebilir")
    return _purge_by_ids(db, target, ids)


def _purge_by_ids(db: Session, target: str, ids: list[int]) -> int:
    fn = _PURGE_BY_IDS.get(target)
    if not fn:
        raise ValueError("Bu hedef icin secili silme tanimli degil")
    return fn(db, ids)


def _delete_orders_by_ids(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    batch_order_ids = [i for (i,) in db.query(ProductionBatchOrder.order_id).filter(ProductionBatchOrder.order_id.in_(ids)).all()]
    if batch_order_ids:
        batch_ids = [i for (i,) in db.query(ProductionBatchOrder.batch_id).filter(ProductionBatchOrder.order_id.in_(batch_order_ids)).distinct().all()]
        db.query(ProductionBatchOrder).filter(ProductionBatchOrder.order_id.in_(batch_order_ids)).delete(synchronize_session=False)
        for bid in batch_ids:
            if not db.query(ProductionBatchOrder).filter(ProductionBatchOrder.batch_id == bid).first():
                db.query(PlanLine).filter(PlanLine.production_batch_id == bid).delete(synchronize_session=False)
                db.query(ProductionBatch).filter(ProductionBatch.id == bid).delete(synchronize_session=False)
    db.query(PlanLine).filter(PlanLine.order_id.in_(ids)).delete(synchronize_session=False)
    db.query(Reservation).filter(Reservation.order_id.in_(ids)).delete(synchronize_session=False)
    db.query(Shipment).filter(Shipment.order_id.in_(ids)).delete(synchronize_session=False)
    db.query(Order).filter(Order.merged_into_id.in_(ids)).update({Order.merged_into_id: None}, synchronize_session=False)
    return db.query(Order).filter(Order.id.in_(ids)).delete(synchronize_session=False)


def _delete_items_by_ids(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    order_ids = [i for (i,) in db.query(Order.id).filter(Order.item_id.in_(ids)).all()]
    if order_ids:
        _delete_orders_by_ids(db, order_ids)
    batch_ids = [i for (i,) in db.query(ProductionBatch.id).filter(ProductionBatch.item_id.in_(ids)).all()]
    if batch_ids:
        db.query(ProductionBatchOrder).filter(ProductionBatchOrder.batch_id.in_(batch_ids)).delete(synchronize_session=False)
        db.query(PlanLine).filter(PlanLine.production_batch_id.in_(batch_ids)).delete(synchronize_session=False)
        db.query(ProductionBatch).filter(ProductionBatch.id.in_(batch_ids)).delete(synchronize_session=False)
    op_ids = [i for (i,) in db.query(RoutingOperation.id).filter(RoutingOperation.item_id.in_(ids)).all()]
    if op_ids:
        db.query(PlanLine).filter(PlanLine.operation_id.in_(op_ids)).delete(synchronize_session=False)
    db.query(ProductionActual).filter(ProductionActual.item_id.in_(ids)).delete(synchronize_session=False)
    db.query(StockReceipt).filter(StockReceipt.item_id.in_(ids)).delete(synchronize_session=False)
    db.query(Reservation).filter(Reservation.item_id.in_(ids)).delete(synchronize_session=False)
    db.query(Shipment).filter(Shipment.item_id.in_(ids)).delete(synchronize_session=False)
    db.query(BomLine).filter(BomLine.item_id.in_(ids)).delete(synchronize_session=False)
    db.query(RoutingOperation).filter(RoutingOperation.item_id.in_(ids)).delete(synchronize_session=False)
    db.query(OpTransitionRule).filter(OpTransitionRule.item_id.in_(ids)).delete(synchronize_session=False)
    return db.query(Item).filter(Item.id.in_(ids)).delete(synchronize_session=False)


def _delete_workcenters_by_ids(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    op_ids = [i for (i,) in db.query(RoutingOperation.id).filter(RoutingOperation.work_center_id.in_(ids)).all()]
    if op_ids:
        db.query(PlanLine).filter(PlanLine.operation_id.in_(op_ids)).delete(synchronize_session=False)
        db.query(RoutingOperation).filter(RoutingOperation.id.in_(op_ids)).delete(synchronize_session=False)
    db.query(PlanLine).filter(PlanLine.work_center_id.in_(ids)).delete(synchronize_session=False)
    db.query(ProductionActual).filter(ProductionActual.work_center_id.in_(ids)).delete(synchronize_session=False)
    db.query(Downtime).filter(Downtime.work_center_id.in_(ids)).delete(synchronize_session=False)
    db.query(Employee).filter(Employee.work_center_id.in_(ids)).update({Employee.work_center_id: None}, synchronize_session=False)
    machine_ids = [i for (i,) in db.query(Machine.id).filter(Machine.work_center_id.in_(ids)).all()]
    if machine_ids:
        db.query(Employee).filter(Employee.machine_id.in_(machine_ids)).update({Employee.machine_id: None}, synchronize_session=False)
    return db.query(WorkCenter).filter(WorkCenter.id.in_(ids)).delete(synchronize_session=False)


def _delete_routing_by_ids(db: Session, ids: list[int]) -> int:
    db.query(PlanLine).filter(PlanLine.operation_id.in_(ids)).delete(synchronize_session=False)
    return db.query(RoutingOperation).filter(RoutingOperation.id.in_(ids)).delete(synchronize_session=False)


def _delete_machines_by_ids(db: Session, ids: list[int]) -> int:
    db.query(Employee).filter(Employee.machine_id.in_(ids)).update({Employee.machine_id: None}, synchronize_session=False)
    return db.query(Machine).filter(Machine.id.in_(ids)).delete(synchronize_session=False)


def _delete_batches_by_ids(db: Session, ids: list[int]) -> int:
    db.query(ProductionBatchOrder).filter(ProductionBatchOrder.batch_id.in_(ids)).delete(synchronize_session=False)
    db.query(PlanLine).filter(PlanLine.production_batch_id.in_(ids)).delete(synchronize_session=False)
    return db.query(ProductionBatch).filter(ProductionBatch.id.in_(ids)).delete(synchronize_session=False)


def _delete_simple(db: Session, model, ids: list[int]) -> int:
    return db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)


_PURGE_BY_IDS: dict[str, Callable] = {
    "orders": _delete_orders_by_ids,
    "orders_ds": _delete_orders_by_ids,
    "items": _delete_items_by_ids,
    "items_ds": _delete_items_by_ids,
    "workcenters": _delete_workcenters_by_ids,
    "workcenters_ds": _delete_workcenters_by_ids,
    "machines": _delete_machines_by_ids,
    "shifts": lambda db, ids: _delete_simple(db, WorkCenterShift, ids),
    "wc_weeks": lambda db, ids: _delete_simple(db, WorkCenterWeek, ids),
    "employees": lambda db, ids: _delete_simple(db, Employee, ids),
    "bom": lambda db, ids: _delete_simple(db, BomLine, ids),
    "routing": _delete_routing_by_ids,
    "op_rules": lambda db, ids: _delete_simple(db, OpTransitionRule, ids),
    "production": lambda db, ids: _delete_simple(db, ProductionActual, ids),
    "downtime": lambda db, ids: _delete_simple(db, Downtime, ids),
    "plan_lines": lambda db, ids: _delete_simple(db, PlanLine, ids),
    "production_batches": _delete_batches_by_ids,
    "reservations": lambda db, ids: _delete_simple(db, Reservation, ids),
    "shipments": lambda db, ids: _delete_simple(db, Shipment, ids),
    "stock_receipts": lambda db, ids: _delete_simple(db, StockReceipt, ids),
    "import_logs": lambda db, ids: _delete_simple(db, ImportLog, ids),
}

detail = list_records
