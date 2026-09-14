"""Veri butunlugu denetimleri — sessiz silme yok; raporlanan uyarilar."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from app.models import (
    BomLine,
    Item,
    Order,
    PlanLine,
    PlanRevision,
    PlanRevisionEvent,
    PlanRevisionSnapshot,
    ProductionActual,
    ProductionBatch,
    ProductionBatchOrder,
    Reservation,
    RoutingOperation,
    RoutingOperationStation,
)
from app.models.master import norm_wip


def critical_table_counts(db: Session) -> dict[str, int]:
    """Restore dogrulama icin kritik tablo satir sayilari."""
    return {
        "orders": db.query(func.count(Order.id)).scalar() or 0,
        "routing_operations": db.query(func.count(RoutingOperation.id)).scalar() or 0,
        "routing_operation_stations": db.query(func.count(RoutingOperationStation.id)).scalar() or 0,
        "bom_lines": db.query(func.count(BomLine.id)).scalar() or 0,
        "production_actuals": db.query(func.count(ProductionActual.id)).scalar() or 0,
        "reservations": db.query(func.count(Reservation.id)).scalar() or 0,
        "production_batches": db.query(func.count(ProductionBatch.id)).scalar() or 0,
        "production_batch_orders": db.query(func.count(ProductionBatchOrder.id)).scalar() or 0,
        "plan_lines": db.query(func.count(PlanLine.id)).scalar() or 0,
        "plan_revisions": db.query(func.count(PlanRevision.id)).scalar() or 0,
        "plan_revision_snapshots": db.query(func.count(PlanRevisionSnapshot.id)).scalar() or 0,
        "plan_revision_events": db.query(func.count(PlanRevisionEvent.id)).scalar() or 0,
    }


def audit_data(db: Session) -> dict:
    """Is kurallari ve FK tutarliligi — hata/uyari listesi."""
    issues: list[dict] = []

    for op in db.query(RoutingOperation).all():
        if op.cycle_time_sec is not None and op.cycle_time_sec < 0:
            issues.append({"level": "error", "code": "negative_cycle_time", "message": f"Negatif cevrim: item_id={op.item_id} seq={op.seq}"})
        if op.cycle_time_sec == 0 and op.setup_time_min == 0:
            issues.append({"level": "warn", "code": "zero_routing_time", "message": f"Sifir sureli operasyon: item_id={op.item_id} seq={op.seq}"})

    for bl in db.query(BomLine).all():
        if bl.quantity is not None and bl.quantity < 0:
            issues.append({"level": "error", "code": "negative_bom_qty", "message": f"Negatif BOM: item_id={bl.item_id} {bl.component_code}"})
        u = (bl.unit or "").strip().upper()
        if u and u not in ("AD", "KG", "M", "M2", "M3", "LT", "PK", "SET", "PCS", "EA"):
            issues.append({"level": "warn", "code": "unknown_unit", "message": f"Bilinmeyen birim '{bl.unit}': BOM item_id={bl.item_id} {bl.component_code}"})

    for pa in db.query(ProductionActual).filter(ProductionActual.earned_hours < 0).limit(50).all():
        issues.append({"level": "error", "code": "negative_earned_hours", "message": f"Negatif earned_hours: production_actual id={pa.id}"})

    for pa in db.query(ProductionActual).filter(
        ProductionActual.quality_status != "legacy_unspecified",
        ProductionActual.good_qty.is_(None),
        ProductionActual.quantity > 0,
    ).limit(50):
        issues.append(
            {
                "level": "warn",
                "code": "quality_without_good_qty",
                "message": f"Kalite dogrulandi ama good_qty yok: production_actual id={pa.id}",
            }
        )

    # Basit BOM dongusu: ayni mamul icinde bilesen -> mamul kodu zinciri (1 adim)
    items_by_id = {i.id: i for i in db.query(Item).all()}
    for bl in db.query(BomLine).all():
        parent = items_by_id.get(bl.item_id)
        if not parent:
            issues.append({"level": "error", "code": "missing_item", "message": f"BOM yetim item_id={bl.item_id}"})
            continue
        if bl.component_code.upper() == parent.code.upper():
            issues.append({"level": "warn", "code": "bom_self_reference", "message": f"BOM kendine referans: {parent.code}"})

    # Dongulu BOM (2 adim): A bileseni B mamulunde, B bileseni A mamulunde
    comp_index: dict[str, set[int]] = defaultdict(set)
    for bl in db.query(BomLine).all():
        comp_index[bl.component_code.upper()].add(bl.item_id)
    for bl in db.query(BomLine).all():
        parent = items_by_id.get(bl.item_id)
        if not parent:
            continue
        for other_item in comp_index.get(parent.code.upper(), ()):
            if other_item == bl.item_id:
                continue
            if any(b.component_code.upper() == parent.code.upper() for b in db.query(BomLine).filter(BomLine.item_id == other_item)):
                issues.append(
                    {
                        "level": "warn",
                        "code": "bom_cycle_pair",
                        "message": f"Olası BOM dongusu: {parent.code} <-> {(items_by_id.get(other_item).code if items_by_id.get(other_item) else other_item)}",
                    }
                )

    # Belirsiz gerceklesme: wip kodu var ama rota eslesmesi yok
    for pa in db.query(ProductionActual).filter(ProductionActual.semi_finished_code != "").limit(200).all():
        wip = norm_wip(pa.semi_finished_code)
        item = db.get(Item, pa.item_id)
        if not item:
            issues.append({"level": "error", "code": "production_missing_item", "message": f"Uretim yetim stok id={pa.item_id} pa={pa.id}"})
            continue
        if not any(norm_wip(o.semi_finished_code or "") == wip for o in item.operations):
            issues.append(
                {
                    "level": "warn",
                    "code": "production_wip_unmatched",
                    "message": f"Yarimamul rota ile eslesmedi: {pa.semi_finished_code} item={item.code} pa={pa.id}",
                }
            )

    insp = inspect(db.get_bind())
    for table, col, ref in (
        ("plan_lines", "order_id", "orders"),
        ("plan_lines", "operation_id", "routing_operations"),
        ("production_batch_orders", "order_id", "orders"),
        ("reservations", "order_id", "orders"),
    ):
        if not insp.has_table(table):
            continue
        q = text(
            f"SELECT COUNT(*) FROM {table} t WHERE t.{col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {ref} r WHERE r.id = t.{col})"
        )
        n = db.execute(q).scalar() or 0
        if n:
            issues.append({"level": "error", "code": "orphan_fk", "message": f"{table}.{col} -> {ref}: {n} yetim satir"})

    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    return {"error_count": len(errors), "warn_count": len(warns), "issues": issues[:500]}
