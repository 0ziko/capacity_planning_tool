"""Plan kalitesi olcumu (FAZ 12 — M6/M7/T3). Optimizasyon eklemez; raporlar."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import Order, PlanRevision, WorkCenter
from app.schemas import AutoPlanRequest, RevenueOut
from app.services import orders as orders_svc
from app.services import planning
from app.services import revenue as revenue_svc
from app.services.kpi_units import week_plan_and_output_kpis
from app.services.plan_input_fingerprint import compute_plan_input_fingerprint
from app.services.planning_candidates import REVENUE_MODE_LABEL

SYNTHETIC_BENCHMARK_LABEL = "synthetic_benchmark"
NOT_FACTORY_PERFORMANCE = "Bu rapor gercek fabrika performansi degildir."

BACKTEST_DATA_REQUIREMENTS = [
    "Tarih bazli siparis/termin snapshot (due_date, revised_due_date aninda)",
    "Uretim actual prod_date <= bilgi kesim tarihi",
    "Sevk shipment_date <= bilgi kesim tarihi",
    "Rota/time_basis aninda",
    "Kapasite/vardiya aninda",
    "Plan revizyon snapshot (onay anindaki input_fingerprint)",
]

RESERVE_PCT_SCENARIOS = (0.0, 5.0, 10.0, 15.0, 20.0)


@dataclass
class KpiRatio:
    name: str
    numerator: float
    denominator: float
    unit: str
    period_start: date
    period_end: date
    sample_count: int
    notes: str = ""

    @property
    def value(self) -> float | None:
        if self.denominator <= 0:
            return None
        return round(self.numerator / self.denominator, 4)


@dataclass
class PlanEvaluationReport:
    benchmark_kind: Literal["live", "synthetic_benchmark", "snapshot"]
    input_fingerprint: str
    revenue_heuristic_not_optimal: bool = True
    notes: list[str] = field(default_factory=list)
    mode_comparison: dict[str, Any] = field(default_factory=dict)
    delivery_kpis: list[KpiRatio] = field(default_factory=list)
    revenue_kpis: dict[str, Any] = field(default_factory=dict)
    reserve_pct_scenarios: list[dict[str, Any]] = field(default_factory=list)
    revision_kpis: dict[str, Any] = field(default_factory=dict)
    wip_summary: dict[str, Any] = field(default_factory=dict)
    bottleneck_hours: float = 0.0
    data_gaps: list[str] = field(default_factory=list)


def _ship_totals(rev: RevenueOut) -> tuple[float, float]:
    ps = sum(w.planned_shipment_revenue for w in rev.weeks)
    ash = sum(w.actual_shipment_revenue for w in rev.weeks)
    return ps, ash


def _commitment_schedule(
    db: Session,
    sim: planning.Simulation,
    wc_ids: list[int] | None,
    *,
    use_original_due: bool,
    order_overrides: dict[int, dict] | None = None,
) -> list:
    orders = list(sim.orders)
    if use_original_due or order_overrides:
        patched: list[Order] = []
        for o in orders:
            po = copy.copy(o)
            ov = (order_overrides or {}).get(o.id, {})
            if use_original_due:
                po.revised_due_date = None
            if "due_date" in ov and ov["due_date"]:
                po.due_date = date.fromisoformat(str(ov["due_date"]))
            if "revised_due_date" in ov:
                rd = ov.get("revised_due_date")
                po.revised_due_date = date.fromisoformat(str(rd)) if rd else None
            patched.append(po)
        return orders_svc.order_schedule(db, wc_ids, lines=sim.lines, orders=patched)
    return orders_svc.order_schedule(db, wc_ids, lines=sim.lines, orders=orders)


def delivery_kpis_from_schedule(
    sched: list,
    *,
    period_start: date,
    period_end: date,
    commitment: Literal["original", "revised"] = "original",
) -> list[KpiRatio]:
    rows = [s for s in sched if s.planned_end and period_start <= s.planned_end <= period_end]
    if commitment == "original":
        on_time = sum(1 for s in rows if s.planned_end and s.due_date and s.planned_end <= s.due_date)
    else:
        on_time = sum(1 for s in rows if s.plan_status == "on_time")

    pred_errors = [float(s.lateness_days or 0) for s in rows if s.planned_end]

    return [
        KpiRatio(
            name="on_time_full_delivery_original_due" if commitment == "original" else "on_time_full_delivery_revised_due",
            numerator=float(on_time),
            denominator=float(len(rows)),
            unit="siparis",
            period_start=period_start,
            period_end=period_end,
            sample_count=len(rows),
        ),
        KpiRatio(
            name="predicted_end_error_days_avg",
            numerator=sum(abs(e) for e in pred_errors),
            denominator=float(len(pred_errors)) if pred_errors else 0.0,
            unit="gun",
            period_start=period_start,
            period_end=period_end,
            sample_count=len(pred_errors),
            notes="Ortalama mutlak sapma (lateness_days)",
        ),
    ]


def compare_modes(db: Session, req: AutoPlanRequest, *, production_as_of: date | None = None) -> dict[str, Any]:
    base = {**req.model_dump(), "replace_existing": False, "mode": "due_date"}
    sim_due = planning.simulate(db, AutoPlanRequest(**base), production_as_of=production_as_of)
    sim_rev = planning.simulate(db, AutoPlanRequest(**{**base, "mode": "revenue"}), production_as_of=production_as_of)
    wc_ids = req.work_center_ids
    sched_due = orders_svc.order_schedule(db, wc_ids, lines=sim_due.lines, orders=sim_due.orders)
    sched_rev = orders_svc.order_schedule(db, wc_ids, lines=sim_rev.lines, orders=sim_rev.orders)
    rev_due = revenue_svc.revenue_report(db, wc_ids, sim_due.start, req.weeks, lines=sim_due.lines, orders=sim_due.orders, sched=sched_due)
    rev_rev = revenue_svc.revenue_report(db, wc_ids, sim_rev.start, req.weeks, lines=sim_rev.lines, orders=sim_rev.orders, sched=sched_rev)
    ps_d, ash_d = _ship_totals(rev_due)
    ps_r, ash_r = _ship_totals(rev_rev)
    return {
        "due_date": {
            "planned_hours": round(sim_due.planned_hours, 2),
            "lines": len(sim_due.lines),
            "planned_ship_value": round(ps_d, 2),
            "actual_ship_value": round(ash_d, 2),
            "on_time": sum(1 for s in sched_due if s.plan_status == "on_time"),
            "late": sum(1 for s in sched_due if s.plan_status == "late"),
        },
        "revenue_heuristic": {
            "planned_hours": round(sim_rev.planned_hours, 2),
            "lines": len(sim_rev.lines),
            "planned_ship_value": round(ps_r, 2),
            "actual_ship_value": round(ash_r, 2),
            "on_time": sum(1 for s in sched_rev if s.plan_status == "on_time"),
            "late": sum(1 for s in sched_rev if s.plan_status == "late"),
            "note": REVENUE_MODE_LABEL,
        },
    }


def reserve_pct_benchmark(db: Session, req: AutoPlanRequest, wc_ids: list[int]) -> list[dict[str, Any]]:
    wcs = db.query(WorkCenter).filter(WorkCenter.id.in_(wc_ids)).all()
    saved = {w.id: w.planning_reserve_pct for w in wcs}
    out: list[dict[str, Any]] = []
    try:
        for pct in RESERVE_PCT_SCENARIOS:
            for w in wcs:
                w.planning_reserve_pct = pct
            db.flush()
            sim = planning.simulate(db, AutoPlanRequest(**{**req.model_dump(), "replace_existing": False}))
            out.append(
                {
                    "planning_reserve_pct": pct,
                    "planned_hours": round(sim.planned_hours, 2),
                    "capacity_hours": round(sim.capacity_hours, 2),
                    "utilization_pct": round(sim.planned_hours / sim.capacity_hours * 100, 1) if sim.capacity_hours else 0,
                    "unplanned_count": len(sim.unplanned),
                    "label": SYNTHETIC_BENCHMARK_LABEL,
                }
            )
    finally:
        for w in wcs:
            w.planning_reserve_pct = saved[w.id]
        db.flush()
    return out


def revision_shift_kpis(db: Session) -> dict[str, Any]:
    revs = db.query(PlanRevision).filter(PlanRevision.status == "applied").all()
    return {
        "applied_revisions": len(revs),
        "sample_count": len(revs),
        "notes": "Detayli is/gun kaymasi revizyon snapshot diff ile genisletilebilir",
    }


def wip_open_qty(db: Session) -> dict[str, Any]:
    from app.models import ProductionActual

    rows = db.query(ProductionActual).order_by(ProductionActual.prod_date.desc()).limit(500).all()
    if not rows:
        return {"open_wip_qty": 0.0, "avg_age_days": None, "sample_count": 0}
    today = date.today()
    ages = [(today - r.prod_date).days for r in rows if r.prod_date]
    qty = sum(float(r.quantity or 0) for r in rows)
    return {
        "open_wip_qty": round(qty, 2),
        "avg_age_days": round(sum(ages) / len(ages), 1) if ages else None,
        "sample_count": len(rows),
        "notes": "Son 500 uretim kaydi ozeti; tam WIP muhasebesi degil",
    }


def bottleneck_unplanned_hours(sim: planning.Simulation) -> float:
    return round(sum(float(u.get("hours") or 0) for u in sim.unplanned), 2)


def orders_from_snapshot_payload(payload: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for o in payload.get("orders") or []:
        out[int(o["id"])] = {
            "due_date": o.get("due_date"),
            "revised_due_date": o.get("revised_due_date"),
        }
    return out


def evaluate_plan(
    db: Session,
    req: AutoPlanRequest,
    *,
    benchmark_kind: Literal["live", "synthetic_benchmark", "snapshot"] = "live",
    snapshot_payload: dict | None = None,
    production_as_of: date | None = None,
    include_reserve_benchmark: bool = False,
) -> PlanEvaluationReport:
    fp = compute_plan_input_fingerprint(db, req)
    if snapshot_payload:
        fp = hashlib.sha256(json.dumps(snapshot_payload, sort_keys=True).encode()).hexdigest()

    notes = [NOT_FACTORY_PERFORMANCE] if benchmark_kind == "synthetic_benchmark" else []
    if benchmark_kind != "snapshot" and snapshot_payload is None:
        notes.append("Tarihsel snapshot yok; canli/sentetik girdi kullanildi.")
        notes.extend(BACKTEST_DATA_REQUIREMENTS[:3])

    mode_cmp = compare_modes(db, req, production_as_of=production_as_of)
    scope = planning.plan_horizon_scope(req.start_week, req.weeks)
    sim = planning.simulate(
        db,
        AutoPlanRequest(**{**req.model_dump(), "replace_existing": False}),
        production_as_of=production_as_of,
    )
    order_ov = orders_from_snapshot_payload(snapshot_payload) if snapshot_payload else None
    sched_orig = _commitment_schedule(db, sim, req.work_center_ids, use_original_due=True, order_overrides=order_ov)
    sched_rev = _commitment_schedule(db, sim, req.work_center_ids, use_original_due=False, order_overrides=order_ov)

    period_end = scope.end_exclusive - timedelta(days=1)
    delivery = delivery_kpis_from_schedule(sched_orig, period_start=scope.start, period_end=period_end, commitment="original")
    delivery += delivery_kpis_from_schedule(sched_rev, period_start=scope.start, period_end=period_end, commitment="revised")

    wc_id = (req.work_center_ids or [None])[0]
    if wc_id:
        matched = week_plan_and_output_kpis(db, wc_id, scope.start, as_of=production_as_of or date.today())
        delivery.append(
            KpiRatio(
                name="matched_plan_compliance_remaining_hours",
                numerator=matched.get("plan_remaining_hours", 0),
                denominator=matched.get("planned_hours", 0) or 0,
                unit="saat",
                period_start=scope.start,
                period_end=period_end,
                sample_count=1,
            )
        )

    rev = revenue_svc.revenue_report(db, req.work_center_ids, scope.start, req.weeks, lines=sim.lines, orders=sim.orders)
    ps, ash = _ship_totals(rev)
    data_gaps: list[str] = []
    if rev.no_price_orders:
        data_gaps.append(f"{rev.no_price_orders} siparis birim fiyatsiz (ciro KPI kismi)")

    from app.services.routing_resource import resource_definition_stats

    rstats = resource_definition_stats(db)
    if rstats["missing_detailed_schedule_definition"]:
        data_gaps.append(
            f"{rstats['missing_detailed_schedule_definition']} operasyon daily_detailed icin tanim eksik — pilot hazir damgasi YOK"
        )

    reserve_rows: list[dict[str, Any]] = []
    if include_reserve_benchmark and req.work_center_ids:
        reserve_rows = reserve_pct_benchmark(db, req, req.work_center_ids)

    return PlanEvaluationReport(
        benchmark_kind=benchmark_kind,
        input_fingerprint=fp,
        notes=notes,
        mode_comparison=mode_cmp,
        delivery_kpis=delivery,
        revenue_kpis={
            "planned_ship_value": round(ps, 2),
            "actual_ship_value": round(ash, 2),
            "partial_revenue": rev.partial_revenue,
            "no_price_orders": rev.no_price_orders,
        },
        reserve_pct_scenarios=reserve_rows,
        revision_kpis=revision_shift_kpis(db),
        wip_summary=wip_open_qty(db),
        bottleneck_hours=bottleneck_unplanned_hours(sim),
        data_gaps=data_gaps,
    )


def report_to_dict(report: PlanEvaluationReport) -> dict[str, Any]:
    return {
        "benchmark_kind": report.benchmark_kind,
        "input_fingerprint": report.input_fingerprint,
        "revenue_heuristic_not_optimal": report.revenue_heuristic_not_optimal,
        "notes": report.notes,
        "mode_comparison": report.mode_comparison,
        "delivery_kpis": [
            {
                "name": k.name,
                "numerator": k.numerator,
                "denominator": k.denominator,
                "value": k.value,
                "unit": k.unit,
                "period_start": k.period_start.isoformat(),
                "period_end": k.period_end.isoformat(),
                "sample_count": k.sample_count,
                "notes": k.notes,
            }
            for k in report.delivery_kpis
        ],
        "revenue_kpis": report.revenue_kpis,
        "reserve_pct_scenarios": report.reserve_pct_scenarios,
        "revision_kpis": report.revision_kpis,
        "wip_summary": report.wip_summary,
        "bottleneck_unplanned_hours": report.bottleneck_hours,
        "data_gaps": report.data_gaps,
        "backtest_data_requirements": BACKTEST_DATA_REQUIREMENTS,
    }
