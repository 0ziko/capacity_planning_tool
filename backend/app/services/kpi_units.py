"""Olcum birimleri: plan uyumu, standart saat ciktisi, durus adam-dakika (M5)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import Downtime, Item, Order, PlanLine, ProductionActual
from app.core.config import get_settings
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
    *, lines: list[PlanLine] | None = None,
) -> dict[int, float]:
    """Plan satiri id -> eslesen uretim saati (aynı siparis/operasyon; plansiz is plani tuketmez)."""
    wk = cap.week_start(week_start)
    if get_settings().production_source == "mes":
        from app.services.mes_actuals import measure
        measured = measure(db, as_of or date.today())
        return {line.id: measured["matches"][line.id]["hours"] for line in measured["lines"]
                if line.work_center_id == wc_id and line.week_start == wk}
    if lines is None:
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
    if get_settings().production_source == "mes":
        from app.services.mes_actuals import weekly_kpis
        return weekly_kpis(db, [wc_id], wk, wk, as_of).get((wc_id, wk), {
            "planned_hours": 0., "standard_hour_equivalent_output": 0.,
            "plan_matched_output_hours": 0., "plan_adherence_remaining_hours": 0.})
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


def plan_and_output_kpis_for_range(
    db: Session, wc_ids: list[int], start: date, end: date, *, as_of: date | None = None,
) -> dict[tuple[int, date], dict[str, float]]:
    """Bulk weekly report, retaining the per-week production matching scope.

    Empty work-center/week cells require no queries. Matching remains scoped to
    the orders planned in each cell; pooling weeks would change FIFO allocation.
    """
    as_of = as_of or date.today()
    if get_settings().production_source == "mes":
        from app.services.mes_actuals import weekly_kpis
        return weekly_kpis(db, wc_ids, start, end, as_of)
    grouped: dict[tuple[int, date], list[PlanLine]] = defaultdict(list)
    producing_wcs = {row[0] for row in db.query(ProductionActual.work_center_id).filter(ProductionActual.work_center_id.in_(wc_ids), ProductionActual.prod_date <= as_of).distinct().all()}
    query = db.query(PlanLine)
    if producing_wcs:
        query = query.options(selectinload(PlanLine.order).selectinload(Order.item).selectinload(Item.operations))
    lines = query.filter(
        PlanLine.work_center_id.in_(wc_ids), PlanLine.week_start >= start, PlanLine.week_start <= end,
    ).all()
    for line in lines:
        grouped[(line.work_center_id, line.week_start)].append(line)
    output: dict[tuple[int, date], float] = defaultdict(float)
    actuals = db.query(ProductionActual.work_center_id, ProductionActual.prod_date, func.sum(ProductionActual.earned_hours)).filter(
        ProductionActual.work_center_id.in_(wc_ids), ProductionActual.prod_date >= start,
        ProductionActual.prod_date <= min(end + timedelta(days=6), as_of),
    ).group_by(ProductionActual.work_center_id, ProductionActual.prod_date).all()
    for wc_id, day, hours in actuals:
        output[(wc_id, cap.week_start(day))] += float(hours or 0)
    result = {}
    for key in grouped.keys() | output.keys():
        cell_lines = grouped.get(key, [])
        matched = production_hours_by_plan_line(db, key[0], key[1], as_of, lines=cell_lines) if cell_lines and key[0] in producing_wcs else {}
        result[key] = {
            "planned_hours": round(sum(float(p.planned_hours or 0) for p in cell_lines), 4),
            "standard_hour_equivalent_output": round(output.get(key, 0.0), 4),
            "plan_matched_output_hours": round(sum(min(float(p.planned_hours or 0), matched.get(p.id, 0.0)) for p in cell_lines), 4),
            "plan_adherence_remaining_hours": round(sum(max(float(p.planned_hours or 0) - matched.get(p.id, 0.0), 0.0) for p in cell_lines), 4),
        }
    return result


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
