"""Olcum birimleri: plan uyumu, standart saat ciktisi, durus adam-dakika (M5)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Downtime, Item, Order, PlanLine, ProductionActual
from app.services import capacity as cap
from app.services.gantt import _production_map


def effective_good_qty(actual: ProductionActual) -> float:
    st = getattr(actual, "quality_status", None) or "legacy_unspecified"
    if st == "legacy_unspecified":
        return float(actual.quantity or 0)
    g = getattr(actual, "good_qty", None)
    if g is not None and g > 0:
        return float(g)
    return float(actual.quantity or 0)


def production_hours_by_plan_line(
    db: Session,
    wc_id: int,
    week_start: date,
    as_of: date | None = None,
) -> dict[int, float]:
    """Plan satiri id -> eslesen uretim saati (aynı siparis/operasyon; plansiz is plani tuketmez)."""
    wk = cap.week_start(week_start)
    lines = (
        db.query(PlanLine)
        .options(joinedload(PlanLine.order).joinedload(Order.item).joinedload(Item.operations))
        .filter(PlanLine.work_center_id == wc_id, PlanLine.week_start == wk)
        .all()
    )
    if not lines:
        return {}
    order_ids = {pl.order_id for pl in lines}
    orders_by_id = {o.id: o for o in db.query(Order).options(joinedload(Order.item)).filter(Order.id.in_(order_ids)).all()}
    prod_map = _production_map(db, wc_id, orders_by_id, as_of or date.today())
    out: dict[int, float] = {}
    for pl in lines:
        pr = prod_map.get((pl.order_id, pl.operation_id), {"hours": 0.0})
        out[pl.id] = float(pr.get("hours") or 0.0)
    return out


def week_plan_and_output_kpis(
    db: Session,
    wc_id: int,
    week_start: date,
    *,
    as_of: date | None = None,
) -> dict[str, float]:
    wk = cap.week_start(week_start)
    wk_end = wk + timedelta(days=6)
    as_of = as_of or date.today()
    planned = (
        db.query(func.sum(PlanLine.planned_hours))
        .filter(PlanLine.work_center_id == wc_id, PlanLine.week_start == wk)
        .scalar()
        or 0.0
    )
    std_output = (
        db.query(func.sum(ProductionActual.earned_hours))
        .filter(
            ProductionActual.work_center_id == wc_id,
            ProductionActual.prod_date >= wk,
            ProductionActual.prod_date <= min(wk_end, as_of),
        )
        .scalar()
        or 0.0
    )
    matched_by_line = production_hours_by_plan_line(db, wc_id, wk, as_of=as_of)
    matched_total = 0.0
    plan_remaining = 0.0
    lines = db.query(PlanLine).filter(PlanLine.work_center_id == wc_id, PlanLine.week_start == wk).all()
    for pl in lines:
        m = min(float(pl.planned_hours or 0), matched_by_line.get(pl.id, 0.0))
        matched_total += m
        plan_remaining += max(float(pl.planned_hours or 0) - matched_by_line.get(pl.id, 0.0), 0.0)
    return {
        "planned_hours": round(float(planned), 4),
        "standard_hour_equivalent_output": round(float(std_output), 4),
        "plan_matched_output_hours": round(matched_total, 4),
        "plan_adherence_remaining_hours": round(plan_remaining, 4),
    }


def downtime_labor_minutes(row: Downtime) -> tuple[float | None, str]:
    """Olculmus adam-dakika; legacy icin None."""
    basis = getattr(row, "time_basis", None) or "legacy_unspecified"
    dur = getattr(row, "duration_minutes", None)
    if dur is None:
        dur = float(row.minutes or 0)
    if basis == "legacy_unspecified":
        return None, "legacy_unspecified"
    if basis == "labor_minutes":
        return round(float(dur), 3), "labor_minutes"
    if basis == "elapsed_minutes":
        hc = getattr(row, "affected_headcount", None)
        if hc is None or hc <= 0:
            return None, "elapsed_minutes_missing_headcount"
        return round(float(dur) * float(hc), 3), "elapsed_minutes"
    return None, basis


def downtime_machine_minutes(row: Downtime) -> float | None:
    if getattr(row, "machine_id", None) is None:
        return None
    basis = getattr(row, "time_basis", None) or "legacy_unspecified"
    if basis == "legacy_unspecified":
        return None
    dur = getattr(row, "duration_minutes", None)
    if dur is None:
        dur = float(row.minutes or 0)
    return round(float(dur), 3)
