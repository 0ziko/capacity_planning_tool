"""Planlama girdisi surumu: onay oncesi veri degisimi tespiti (SHA-256)."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    OpTransitionRule,
    Order,
    PlanLine,
    ProductionActual,
    Reservation,
    RoutingOperation,
    WorkCenterWeek,
)
from app.schemas import AutoPlanRequest
from app.services import capacity as cap
from app.services.planning import plan_horizon_scope


def _iso(d: date | None) -> str:
    return d.isoformat() if d else ""


def build_fingerprint_payload(db: Session, req: AutoPlanRequest) -> dict:
    """Planı etkileyen canli girdiler (created_at vb. haric)."""
    scope = plan_horizon_scope(req.start_week, req.weeks)
    start = scope.start
    end = scope.end_exclusive
    wc_ids = sorted(req.work_center_ids or [])
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
                "week_start": _iso(p.week_start),
                "planned_hours": round(float(p.planned_hours or 0), 6),
                "planned_qty": round(float(p.planned_qty or 0), 6),
                "mode": p.mode,
            }
            for p in pl_q.all()
        ],
        key=lambda x: (x["id"],),
    )

    item_ids = {o.item_id for o in orders}
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
            if not wc_set or op.work_center_id in wc_set
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

    return {
        "scope": {"start": _iso(start), "weeks": req.weeks, "work_center_ids": wc_ids},
        "orders": order_part,
        "plan_lines": plan_part,
        "routing": routing_part,
        "wc_weeks": wc_week_part,
        "production": prod_part,
        "reservations": res_part,
        "op_rules": rules_part,
    }


def compute_plan_input_fingerprint(db: Session, req: AutoPlanRequest) -> str:
    payload = build_fingerprint_payload(db, req)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
