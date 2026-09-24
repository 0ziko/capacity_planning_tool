"""Bitmis urun stok/rezervasyon/uretim ile siparis uretim ihtiyaci netlestirme (M2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Order, Reservation, RoutingOperation, Shipment

PROVENANCE_EXTERNAL = "external_finished_stock"
PROVENANCE_PRODUCTION = "completed_production"
PROVENANCE_LEGACY = "legacy_unspecified"


@dataclass
class OrderDemandNetting:
    order_id: int
    order_quantity: float
    shipped_qty: float
    demand_balance: float
    external_finished_stock: float
    completed_production_qty: float
    production_linked_reservation: float
    net_production_qty: float
    provenance: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _last_operation_id(order: Order) -> int | None:
    ops = sorted(order.item.operations or [], key=lambda x: x.seq) if order.item else []
    return ops[-1].id if ops else None


def compute_order_demand_netting(
    db: Session,
    order: Order,
    *,
    produced_map: dict[tuple[int, int], float] | None = None,
    shipped_qty: float | None = None,
    reservations: list | None = None,
) -> OrderDemandNetting:
    """Talep bakiyesi = siparis miktari - sevk. Stok/uretim kredisi cift sayilmaz."""
    from app.services.remaining_work import produced_qty_map

    snapshot = db.info.get("_merge_read_snapshot")
    if snapshot is not None:
        if shipped_qty is None:
            shipped_qty = snapshot["shipped"].get(order.id, 0.)
        if reservations is None:
            reservations = snapshot["reservations"].get(order.id, [])
    shipped = float(shipped_qty) if shipped_qty is not None else float(
        db.query(func.sum(Shipment.quantity)).filter(Shipment.order_id == order.id).scalar() or 0.0
    )
    demand_balance = max(float(order.quantity or 0) - shipped, 0.0)

    if produced_map is None:
        produced_map, _ = produced_qty_map(db)

    fg_op = _last_operation_id(order)
    fg_produced = float(produced_map.get((order.id, fg_op), 0.0)) if fg_op else 0.0

    external = 0.0
    prod_link = 0.0
    legacy = 0.0
    warnings: list[str] = []

    rows = reservations if reservations is not None else db.query(Reservation).filter(Reservation.order_id == order.id).all()
    for r in rows:
        prov = (getattr(r, "stock_provenance", None) or PROVENANCE_LEGACY).strip()
        q = float(r.quantity or 0)
        if prov == PROVENANCE_EXTERNAL:
            external += q
        elif prov == PROVENANCE_PRODUCTION:
            prod_link += q
        elif prov == PROVENANCE_LEGACY:
            legacy += q
            warnings.append(
                f"netlestirme_uyarisi: siparis {order.order_no} rezervasyon #{r.id} legacy provenance"
            )
        else:
            external += q

    external_effective = external + legacy
    # Uretim gerceklesmesi (produced_qty_map) ayri dusulur; burada yalnizca stok/rezervasyon kredisi.
    stock_only = external_effective + max(0.0, prod_link - min(fg_produced, prod_link))
    from app.core.config import get_settings
    if get_settings().production_source == "mes":
        # MES pool already excludes reserved/shipped FG; reservation is deducted here once.
        stock_only = external_effective + prod_link
    stock_credit = min(demand_balance, stock_only)
    net = max(0.0, demand_balance - stock_credit)

    return OrderDemandNetting(
        order_id=order.id,
        order_quantity=float(order.quantity or 0),
        shipped_qty=shipped,
        demand_balance=round(demand_balance, 4),
        external_finished_stock=round(external_effective, 4),
        completed_production_qty=round(fg_produced, 4),
        production_linked_reservation=round(prod_link, 4),
        net_production_qty=round(net, 4),
        provenance={
            "shipment": round(shipped, 4),
            "external_finished_stock": round(external, 4),
            "legacy_unspecified": round(legacy, 4),
            "completed_production": round(fg_produced, 4),
            "reservation_completed_production": round(prod_link, 4),
            "stock_credit": round(stock_credit, 4),
        },
        warnings=warnings,
    )


def net_production_scale(order: Order, netting: OrderDemandNetting | None) -> float:
    """Operasyon gerekli miktarlarini olceklendirmek icin (0..1)."""
    if not order.quantity or order.quantity <= 1e-9:
        return 0.0
    if netting is None:
        return 1.0
    return max(0.0, min(1.0, netting.net_production_qty / float(order.quantity)))
