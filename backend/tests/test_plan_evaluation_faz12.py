"""FAZ 12: plan kalitesi olcumu ve kabul testleri."""

from datetime import date

from app.models import Order, ProductionActual, RoutingOperation
from app.models import Item
from app.schemas import AutoPlanRequest
from app.services import planning
from app.services.plan_evaluation import (
    NOT_FACTORY_PERFORMANCE,
    compare_modes,
    evaluate_plan,
    report_to_dict,
)
from app.services.plan_input_fingerprint import build_fingerprint_payload
from tests.brute_force_reference import best_and_worst_lateness, sequential_lateness_days
from tests.test_revenue_modes import WEEK, _setup


def _kpi_value(report_dict, name: str) -> float | None:
    for k in report_dict["delivery_kpis"]:
        if k["name"] == name:
            return k["value"]
    return None


def test_same_snapshot_same_fingerprint_and_kpis(client, auth, db):
    wc_id = _setup(client, auth)
    req = AutoPlanRequest(start_week=WEEK, weeks=2, work_center_ids=[wc_id], replace_existing=False)
    r1 = client.post(
        "/api/plan/evaluation",
        headers=auth,
        json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "benchmark_kind": "live"},
    )
    assert r1.status_code == 200
    body = {"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "benchmark_kind": "live"}
    r2 = client.post("/api/plan/evaluation", headers=auth, json=body)
    assert r2.status_code == 200
    d1, d2 = r1.json(), r2.json()
    assert d1["input_fingerprint"] == d2["input_fingerprint"]
    assert d1["mode_comparison"] == d2["mode_comparison"]
    assert d1["delivery_kpis"] == d2["delivery_kpis"]


def test_original_commitment_kpi_unchanged_when_db_revised(client, auth, db):
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "EV-1", "due_date": "2026-09-10", "item_code": "UCUZ", "quantity": 5, "unit_price": 1},
    )
    o = db.query(Order).filter(Order.order_no == "EV-1").one()
    snap = build_fingerprint_payload(db, AutoPlanRequest(start_week=WEEK, weeks=4, work_center_ids=[wc_id]))
    snap_orders = {x["id"]: x for x in snap["orders"]}
    snap_orders[o.id]["due_date"] = "2026-09-10"
    snap_orders[o.id]["revised_due_date"] = ""

    req = AutoPlanRequest(start_week=WEEK, weeks=4, work_center_ids=[wc_id], replace_existing=False)
    rep1 = report_to_dict(evaluate_plan(db, req, benchmark_kind="snapshot", snapshot_payload=snap))
    orig1 = _kpi_value(rep1, "on_time_full_delivery_original_due")

    o.revised_due_date = date(2026, 12, 31)
    db.commit()

    rep2 = report_to_dict(evaluate_plan(db, req, benchmark_kind="snapshot", snapshot_payload=snap))
    orig2 = _kpi_value(rep2, "on_time_full_delivery_original_due")
    assert orig1 == orig2


def test_production_after_as_of_excluded_from_plan_input(client, auth, db):
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "EV-2", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    op = db.query(RoutingOperation).join(Item).filter(Item.code == "UCUZ").first()
    as_of = date(2026, 9, 8)
    db.add(
        ProductionActual(
            prod_date=as_of,
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no="EV-2",
            quantity=6,
            earned_hours=6,
        )
    )
    db.add(
        ProductionActual(
            prod_date=date(2026, 9, 15),
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no="EV-2",
            quantity=4,
            earned_hours=4,
        )
    )
    db.commit()

    base = {"start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "replace_existing": False}
    sim_cut = planning.simulate(db, AutoPlanRequest(**base), production_as_of=as_of)
    sim_full = planning.simulate(db, AutoPlanRequest(**base), production_as_of=None)
    assert round(sim_cut.planned_hours, 1) == 4.0
    assert round(sim_full.planned_hours, 1) == 0.0


def test_synthetic_benchmark_not_factory_performance(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/plan/evaluation",
        headers=auth,
        json={
            "start_week": WEEK.isoformat(),
            "weeks": 2,
            "work_center_ids": [wc_id],
            "benchmark_kind": "synthetic_benchmark",
            "include_reserve_benchmark": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert NOT_FACTORY_PERFORMANCE in body["notes"]
    assert body["benchmark_kind"] == "synthetic_benchmark"
    if body["reserve_pct_scenarios"]:
        assert body["reserve_pct_scenarios"][0]["label"] == "synthetic_benchmark"


def test_heuristic_vs_brute_force_lateness_gap(db, client, auth):
    """4 is, tek op: sezgisel due_date modu ile tum siralarin en iyi gecikmesi karsilastirilir."""
    wc_id = _setup(client, auth)
    specs = [
        ("BF-1", "2026-09-08", 2),
        ("BF-2", "2026-09-09", 2),
        ("BF-3", "2026-09-10", 2),
        ("BF-4", "2026-09-11", 2),
    ]
    for no, due, qty in specs:
        client.post(
            "/api/orders",
            headers=auth,
            json={"order_no": no, "due_date": due, "item_code": "UCUZ", "quantity": qty, "unit_price": 1},
        )
    req = AutoPlanRequest(start_week=WEEK, weeks=1, work_center_ids=[wc_id], replace_existing=False, mode="due_date")
    sim = planning.simulate(db, req)
    from app.services import orders as orders_svc

    sched = orders_svc.order_schedule(db, [wc_id], lines=sim.lines, orders=sim.orders)
    heuristic_late = sum(max(0, (s.planned_end - s.due_date).days) for s in sched if s.planned_end and s.due_date)

    hours = [2.0] * 4
    dues = [date.fromisoformat(d) for _, d, _ in specs]
    best, worst, _best_perm = best_and_worst_lateness(hours, dues, week_start=WEEK)
    assert best <= heuristic_late + 0.01
    assert heuristic_late <= worst + 0.01
    assert compare_modes(db, req)["revenue_heuristic"]["note"]
