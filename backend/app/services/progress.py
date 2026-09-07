"""Gunluk ilerleme: planlanan vs gerceklesen is gucu saati."""

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import PlanLine, ProductionActual, WorkCenter
from app.schemas import ProgressOut
from app.services import capacity as cap


def week_progress(db: Session, wc: WorkCenter, wk: date, as_of: date | None = None) -> ProgressOut:
    """as_of: bugunun tarihi. Uretim verisi 'bir onceki gun' import edildigi icin
    as_of'tan onceki calisma gunleri 'gecmis' sayilir."""
    wk = cap.week_start(wk)
    wk_end = wk + timedelta(days=6)
    as_of = as_of or date.today()

    planned = (
        db.query(func.sum(PlanLine.planned_hours))
        .filter(PlanLine.work_center_id == wc.id, PlanLine.week_start == wk)
        .scalar()
        or 0.0
    )
    wdays = cap.working_days(wc, wk, wk_end)
    n_days = len(wdays)
    elapsed = len([d for d in wdays if d < as_of])
    expected = planned * (elapsed / n_days) if n_days else 0.0

    actual = (
        db.query(func.sum(ProductionActual.earned_hours))
        .filter(
            ProductionActual.work_center_id == wc.id,
            ProductionActual.prod_date >= wk,
            ProductionActual.prod_date <= min(wk_end, as_of - timedelta(days=1)),
        )
        .scalar()
        or 0.0
    )
    remaining = max(planned - actual, 0.0)
    capacity = cap.week_capacity_hours(db, wc, wk)
    daily_cap = capacity / n_days if n_days else 0.0
    remaining_days = remaining / daily_cap if daily_cap > 0 else 0.0

    if planned <= 0:
        status = "no_plan"
    elif elapsed == 0:
        status = "ahead" if actual > 0 else "not_started"
    elif actual >= expected * 1.05:
        status = "ahead"
    elif actual >= expected * 0.9:
        status = "on_track"
    else:
        status = "behind"

    return ProgressOut(
        work_center_id=wc.id,
        work_center_code=wc.code,
        week_start=wk,
        planned_hours=round(planned, 2),
        expected_hours_to_date=round(expected, 2),
        actual_hours_to_date=round(actual, 2),
        remaining_hours=round(remaining, 2),
        remaining_days=round(remaining_days, 2),
        working_days=n_days,
        elapsed_days=elapsed,
        status=status,
    )


def daily_series(db: Session, wc: WorkCenter, wk: date) -> list[dict]:
    """Hafta icin gun gun kumulatif beklenen ve gerceklesen."""
    wk = cap.week_start(wk)
    wdays = cap.working_days(wc, wk, wk + timedelta(days=6))
    planned = (
        db.query(func.sum(PlanLine.planned_hours))
        .filter(PlanLine.work_center_id == wc.id, PlanLine.week_start == wk)
        .scalar()
        or 0.0
    )
    per_day = planned / len(wdays) if wdays else 0.0
    rows = (
        db.query(ProductionActual.prod_date, func.sum(ProductionActual.earned_hours))
        .filter(ProductionActual.work_center_id == wc.id, ProductionActual.prod_date >= wk, ProductionActual.prod_date <= wk + timedelta(days=6))
        .group_by(ProductionActual.prod_date)
        .all()
    )
    actual_by_day = {d: float(h or 0) for d, h in rows}
    out, cum_exp, cum_act = [], 0.0, 0.0
    for d in wdays:
        cum_exp += per_day
        cum_act += actual_by_day.get(d, 0.0)
        out.append(
            {
                "day": d.isoformat(),
                "expected_cum": round(cum_exp, 2),
                "actual_day": round(actual_by_day.get(d, 0.0), 2),
                "actual_cum": round(cum_act, 2),
            }
        )
    return out
