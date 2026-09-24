"""Planlama girdisi surumu: onay oncesi veri degisimi tespiti (SHA-256)."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BomLine,
    Item,
    Shipment,
    ProductionBatch,
    OpTransitionRule,
    Order,
    PlanLine,
    ProductionActual,
    Reservation,
    RoutingOperation,
    WorkCenterWeek,
)
from app.models.mes import MesDetail
from app.schemas import AutoPlanRequest
from app.services import capacity as cap
from app.services.planning import plan_horizon_scope, _selected_work_centers


def _iso(d: date | None) -> str:
    return d.isoformat() if d else ""


def build_fingerprint_payload(db: Session, req: AutoPlanRequest) -> dict:
    """Planı etkileyen canli girdiler (created_at vb. haric)."""
    scope = plan_horizon_scope(req.start_week, req.weeks)
    start = scope.start
    end = scope.end_exclusive
    selected_wcs = _selected_work_centers(db, req.work_center_ids)
    wc_ids = sorted(w.id for w in selected_wcs)
    wc_set = set(wc_ids)

    orders = (
        db.query(Order)
        .filter(Order.status == "open")
        .order_by(Order.id)
        .all()
    )
    order_part = [
        {
            "id": o.id,
            "item_id": o.item_id,
            "quantity": round(float(o.quantity or 0), 6),
            "due_date": _iso(o.due_date),
            "revised_due_date": _iso(o.revised_due_date),
            "status": o.status,
            "material_status": getattr(o, "material_status", None) or "unknown",
            "material_ready_date": _iso(getattr(o, "material_ready_date", None)),
        }
        for o in orders
    ]

    pl_q = db.query(PlanLine).filter(PlanLine.week_start >= start, PlanLine.week_start < end)
    if wc_set:
        pl_q = pl_q.filter(PlanLine.work_center_id.in_(wc_set))
    plan_part = sorted(
        [
            {
                "id": p.id,
                "order_id": p.order_id,
                "production_batch_id": p.production_batch_id,
                "operation_id": p.operation_id,
                "work_center_id": p.work_center_id,
                "machine_id": p.machine_id,
                "week_start": _iso(p.week_start),
                "planned_hours": round(float(p.planned_hours or 0), 6),
                "planned_qty": round(float(p.planned_qty or 0), 6),
                "material_unverified": p.material_unverified,
                "mode": p.mode,
            }
            for p in pl_q.all()
        ],
        key=lambda x: (x["id"],),
    )

    item_ids = {o.item_id for o in orders}
    from app.services.bom_tree import is_wip_asm_link
    bom_rows = db.query(BomLine).filter(BomLine.item_id.in_(item_ids)).order_by(BomLine.id).all()
    wip_codes = {b.component_code for b in bom_rows if is_wip_asm_link(b.component_code, b.source_wip, b.recipe_seq)}
    if wip_codes:
        item_ids.update(i for (i,) in db.query(Item.id).filter(func.upper(Item.code).in_({code.upper() for code in wip_codes})).all())
        bom_rows = db.query(BomLine).filter(BomLine.item_id.in_(item_ids)).order_by(BomLine.id).all()
    routing_part: list[dict] = []
    if item_ids:
        ops = (
            db.query(RoutingOperation)
            .filter(RoutingOperation.item_id.in_(item_ids))
            .order_by(RoutingOperation.item_id, RoutingOperation.seq)
            .all()
        )
        routing_part = [
            {
                "id": op.id,
                "item_id": op.item_id,
                "seq": op.seq,
                "work_center_id": op.work_center_id,
                "cycle_time_sec": round(float(op.cycle_time_sec or 0), 6),
                "line_interval_sec": op.line_interval_sec,
                "station_ids": [op.primary_machine_id] + sorted(r.machine_id for r in op.alt_stations),
                "setup_time_min": round(float(op.setup_time_min or 0), 6),
                "time_basis": getattr(op, "time_basis", None) or "legacy_unspecified",
                "crew_size": op.crew_size if getattr(op, "crew_size", None) is not None else None,
                "machine_cycle_time_sec": round(float(op.machine_cycle_time_sec), 6)
                if getattr(op, "machine_cycle_time_sec", None) is not None
                else None,
                "setup_labor_minutes": round(float(op.setup_labor_minutes), 6)
                if getattr(op, "setup_labor_minutes", None) is not None
                else None,
                "setup_machine_minutes": round(float(op.setup_machine_minutes), 6)
                if getattr(op, "setup_machine_minutes", None) is not None
                else None,
                "units_per_cycle": int(getattr(op, "units_per_cycle", None) or 1),
            }
            for op in ops
        ]

    wc_week_part: list[dict] = []
    if wc_set:
        rows = (
            db.query(WorkCenterWeek)
            .filter(
                WorkCenterWeek.work_center_id.in_(wc_set),
                WorkCenterWeek.week_start >= start,
                WorkCenterWeek.week_start < end,
            )
            .order_by(WorkCenterWeek.work_center_id, WorkCenterWeek.week_start)
            .all()
        )
        wc_week_part = [
            {
                "work_center_id": r.work_center_id,
                "week_start": _iso(r.week_start),
                "headcount": r.headcount,
                "efficient_hours_per_person": r.efficient_hours_per_person,
                "working_days": r.working_days,
                "line_hours_per_day": r.line_hours_per_day,
            }
            for r in rows
        ]

    prod_part: list[dict] = []
    if wc_set and item_ids:
        rows = (
            db.query(
                ProductionActual.order_no,
                ProductionActual.item_id,
                ProductionActual.operation_seq,
                ProductionActual.work_center_id,
                ProductionActual.quantity,
                ProductionActual.earned_hours,
            )
            .filter(
                ProductionActual.work_center_id.in_(wc_set),
                ProductionActual.item_id.in_(item_ids),
            )
            .all()
        )
        prod_part = sorted(
            [
                {
                    "order_no": (r.order_no or "").upper(),
                    "item_id": r.item_id,
                    "operation_seq": r.operation_seq,
                    "work_center_id": r.work_center_id,
                    "quantity": round(float(r.quantity or 0), 6),
                    "earned_hours": round(float(r.earned_hours or 0), 6),
                }
                for r in rows
            ],
            key=lambda x: (x["order_no"], x["item_id"], x["operation_seq"], x["work_center_id"]),
        )

    res_part = sorted(
        [
            {
                "order_id": r.order_id,
                "item_id": r.item_id,
                "quantity": round(float(r.quantity or 0), 6),
                "source": r.source,
                "stock_provenance": getattr(r, "stock_provenance", None) or "legacy_unspecified",
            }
            for r in db.query(Reservation).order_by(Reservation.id).all()
        ],
        key=lambda x: (x["order_id"], x["item_id"]),
    )

    rules = db.query(OpTransitionRule).order_by(OpTransitionRule.id).all()
    rules_part = [
        {
            "scope": r.scope,
            "product_group": r.product_group or "",
            "item_id": r.item_id,
            "from_op_norm": r.from_op_norm,
            "to_op_norm": r.to_op_norm,
            "rule": r.rule,
            "lag_cycles": r.lag_cycles,
            "wait_minutes": r.wait_minutes,
        }
        for r in rules
    ]

    # Include effective labor calendars, not just explicit weekly rows: shifts,
    # staffing defaults, reserve and holidays also change a reviewed plan.
    labor_calendars = []
    for wc in sorted(selected_wcs, key=lambda w: w.id):
        days = cap.capacity_for_range(db, wc, start, scope.end_inclusive).days
        labor_calendars.append({
            "work_center_id": wc.id,
            "planning_reserve_pct": float(wc.planning_reserve_pct or 0),
            "planning_mode": wc.planning_mode,
            "stations": [{"id": m.id, "active": m.is_active, "crew": m.required_crew_size,
                          "weeks": [{"week": _iso(r.week_start), "hours": r.working_hours} for r in sorted(m.weeks, key=lambda r: r.week_start) if start <= r.week_start <= scope.end_inclusive]}
                         for m in sorted(wc.machines, key=lambda m: m.id)],
            "days": [{"day": _iso(d.day), "hours": d.hours} for d in days],
        })

    from app.core.config import get_settings
    return {
        "production_source": get_settings().production_source,
        "scope": {"start": _iso(start), "weeks": req.weeks, "work_center_ids": wc_ids},
        "orders": order_part,
        "plan_lines": plan_part,
        "routing": routing_part,
        "wc_weeks": wc_week_part,
        "production": prod_part,
        "reservations": res_part,
        # MES mapping/quantity and shipment changes invalidate reviewed demand.
        # The shared MES pool crosses item/center boundaries, so retain its full ledger.
        "mes": [{"detail_id": r.detail_id, "prod_date": _iso(r.prod_date),
                 "material_code": r.material_code, "machine_code": r.machine_code,
                 "quantity": r.quantity, "mapping": r.mapping, "receipt_id": r.receipt_id}
                for r in db.query(MesDetail).order_by(MesDetail.detail_id).all()],
        "shipments": [{"id": r.id, "order_id": r.order_id, "item_id": r.item_id,
                       "quantity": r.quantity, "ship_date": _iso(r.ship_date)}
                      for r in db.query(Shipment).order_by(Shipment.id).all()],
        "bom": [{"item_id": r.item_id, "component_code": r.component_code,
                 "source_wip": r.source_wip, "quantity": r.quantity, "recipe_seq": r.recipe_seq}
                for r in bom_rows],
        "batches": [{"id": b.id, "item_id": b.item_id, "quantity": b.quantity,
                     "status": b.status, "due_date": _iso(b.due_date),
                     "members": [{"order_id": link.order_id, "quantity": link.quantity}
                                 for link in sorted(b.orders, key=lambda link: link.order_id)]}
                    for b in db.query(ProductionBatch).options(selectinload(ProductionBatch.orders)).order_by(ProductionBatch.id).all()],
        "op_rules": rules_part,
        "labor_calendars": labor_calendars,
    }


def compute_plan_input_fingerprint(db: Session, req: AutoPlanRequest) -> str:
    payload = build_fingerprint_payload(db, req)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
