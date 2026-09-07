"""Ciro hesaplari (haftalik / aylik) ve iki planlama modunun karsilastirilmasi."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Order
from app.schemas import (
    AutoPlanRequest,
    CompareOrderRow,
    OrderScheduleOut,
    PeriodRevenue,
    PlanCompareOut,
    PlanCompareRequest,
    PlanScenario,
    RevenueOut,
)
from app.services import capacity as cap
from app.services import orders as orders_svc
from app.services import planning

COMPLETE_STATUSES = ("on_time", "late")


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def revenue_report(db: Session, wc_ids: list[int] | None, start: date, weeks: int, lines=None, orders: list[Order] | None = None, sched: list[OrderScheduleOut] | None = None) -> RevenueOut:
    """Plan sonucuna gore ciro.

    completed: siparisin tahmini bitis gununun dustugu donemde tum cirosu (teslim/fatura mantigi).
    earned   : her plan satiri, siparis cirosunun (planlanan saat / gereken saat) payini
               kendi haftasina yazar (oransal ilerleme mantigi).
    """
    start = cap.week_start(start)
    horizon_end = start + timedelta(weeks=weeks) - timedelta(days=1)
    if orders is None:
        orders = orders_svc._open_orders(db)  # noqa: SLF001
    if sched is None:
        sched = orders_svc.order_schedule(db, wc_ids, lines=lines, orders=orders)
    if lines is None:
        from app.models import PlanLine

        lines = db.query(PlanLine).filter(PlanLine.order_id.in_([o.id for o in orders])).all() if orders else []
        if wc_ids:
            lines = [l for l in lines if l.work_center_id in wc_ids]
    sched_by_id = {s.order_id: s for s in sched}

    completed_w: dict[date, float] = defaultdict(float)
    completed_n: dict[date, int] = defaultdict(int)
    earned_w: dict[date, float] = defaultdict(float)
    planned_rev = partial_rev = unplanned_rev = 0.0
    no_price = 0
    for s in sched:
        if s.revenue <= 0:
            no_price += 1
        if s.plan_status in COMPLETE_STATUSES and s.planned_end:
            planned_rev += s.revenue
            wk = cap.week_start(s.planned_end)
            completed_w[wk] += s.revenue
            completed_n[wk] += 1
        elif s.plan_status == "partial":
            partial_rev += s.revenue
        elif s.plan_status == "unplanned":
            unplanned_rev += s.revenue
    for l in lines:
        s = sched_by_id.get(l.order_id)
        if not s or s.required_hours <= 0 or s.revenue <= 0:
            continue
        # tamamen planlanan siparislerde pay, planlanan toplam saate gore alinir ki
        # yuvarlama farklari yuzunden oransal toplam cirodan sapmasin
        denom = s.planned_hours if s.plan_status in COMPLETE_STATUSES and s.planned_hours > 0 else s.required_hours
        earned_w[cap.week_start(l.week_start)] += s.revenue * (l.planned_hours / denom)

    last = max([horizon_end, *completed_w.keys(), *earned_w.keys()])
    wk_list = []
    wk = start
    while wk <= last:
        wk_list.append(wk)
        wk += timedelta(weeks=1)
    week_rows: list[PeriodRevenue] = []
    cum_c = cum_e = 0.0
    month_c: dict[str, float] = defaultdict(float)
    month_n: dict[str, int] = defaultdict(int)
    month_e: dict[str, float] = defaultdict(float)
    for wk in wk_list:
        c = completed_w.get(wk, 0.0)
        e = earned_w.get(wk, 0.0)
        cum_c += c
        cum_e += e
        week_rows.append(PeriodRevenue(period=wk.isoformat(), completed_revenue=round(c, 2), completed_orders=completed_n.get(wk, 0), earned_revenue=round(e, 2), cumulative_completed=round(cum_c, 2), cumulative_earned=round(cum_e, 2)))
        # ay: haftanin Pazartesi'sine gore (tamamlanan icin gercek bitis gunu kullanilir)
        month_e[_month_key(wk)] += e
    for s in sched:
        if s.plan_status in COMPLETE_STATUSES and s.planned_end:
            month_c[_month_key(s.planned_end)] += s.revenue
            month_n[_month_key(s.planned_end)] += 1
    months = sorted(set(month_c) | set(month_e) | {_month_key(wk) for wk in wk_list})
    month_rows: list[PeriodRevenue] = []
    cum_c = cum_e = 0.0
    for m in months:
        c = month_c.get(m, 0.0)
        e = month_e.get(m, 0.0)
        cum_c += c
        cum_e += e
        month_rows.append(PeriodRevenue(period=m, completed_revenue=round(c, 2), completed_orders=month_n.get(m, 0), earned_revenue=round(e, 2), cumulative_completed=round(cum_c, 2), cumulative_earned=round(cum_e, 2)))

    return RevenueOut(
        start=start,
        end=horizon_end,
        total_open_revenue=round(sum(o.quantity * (o.unit_price or 0.0) for o in orders), 2),
        planned_revenue=round(planned_rev, 2),
        partial_revenue=round(partial_rev, 2),
        unplanned_revenue=round(unplanned_rev, 2),
        no_price_orders=no_price,
        weeks=week_rows,
        months=month_rows,
    )


def _scenario(db: Session, sim: planning.Simulation, wc_ids: list[int] | None, weeks: int) -> PlanScenario:
    sched = orders_svc.order_schedule(db, wc_ids, lines=sim.lines, orders=sim.orders)
    rev = revenue_report(db, wc_ids, sim.start, weeks, lines=sim.lines, orders=sim.orders, sched=sched)
    return PlanScenario(
        mode=sim.mode,  # type: ignore[arg-type]
        label="Maksimum ciro" if sim.mode == "revenue" else "Termine göre",
        created_lines=len(sim.lines),
        planned_revenue=rev.planned_revenue,
        on_time=sum(1 for s in sched if s.plan_status == "on_time"),
        late=sum(1 for s in sched if s.plan_status == "late"),
        partial=sum(1 for s in sched if s.plan_status == "partial"),
        unplanned=sum(1 for s in sched if s.plan_status == "unplanned"),
        total_lateness_days=sum(max(s.lateness_days or 0, 0) for s in sched if s.plan_status == "late"),
        utilization_pct=round(sim.planned_hours / sim.capacity_hours * 100, 1) if sim.capacity_hours > 0 else 0.0,
        orders=sched,
        revenue=rev,
    )


def compare(db: Session, req: PlanCompareRequest) -> PlanCompareOut:
    base = dict(start_week=req.start_week, weeks=req.weeks, work_center_ids=req.work_center_ids, replace_existing=False)
    sim_due = planning.simulate(db, AutoPlanRequest(mode="due_date", **base))
    sim_rev = planning.simulate(db, AutoPlanRequest(mode="revenue", **base))
    due = _scenario(db, sim_due, req.work_center_ids, req.weeks)
    rev = _scenario(db, sim_rev, req.work_center_ids, req.weeks)
    rev_by_id = {s.order_id: s for s in rev.orders}

    rows: list[CompareOrderRow] = []
    rev_misses_due: list[str] = []
    rev_drops: list[str] = []
    due_drops: list[str] = []
    for d in due.orders:
        r = rev_by_id.get(d.order_id)
        if not r:
            continue
        d_done = d.plan_status in COMPLETE_STATUSES
        r_done = r.plan_status in COMPLETE_STATUSES
        if d.plan_status == "on_time" and r.plan_status == "late":
            diff = "rev_misses_due"
            rev_misses_due.append(d.order_no)
        elif d_done and not r_done:
            diff = "rev_drops"
            rev_drops.append(d.order_no)
        elif r_done and not d_done:
            diff = "due_drops"
            due_drops.append(d.order_no)
        elif d.planned_end and r.planned_end and r.planned_end < d.planned_end:
            diff = "rev_earlier"
        elif d.planned_end and r.planned_end and r.planned_end > d.planned_end:
            diff = "rev_later"
        elif d.plan_status == r.plan_status:
            diff = "same"
        else:
            diff = "other"
        rows.append(
            CompareOrderRow(
                order_id=d.order_id,
                order_no=d.order_no,
                customer=d.customer,
                item_code=d.item_code,
                quantity=d.quantity,
                revenue=d.revenue,
                due_date=d.due_date,
                due_status=d.plan_status,
                due_end=d.planned_end,
                due_lateness=d.lateness_days,
                rev_status=r.plan_status,
                rev_end=r.planned_end,
                rev_lateness=r.lateness_days,
                diff=diff,
            )
        )
    return PlanCompareOut(due=due, revenue=rev, rows=rows, rev_misses_due=rev_misses_due, rev_drops=rev_drops, due_drops=due_drops)
