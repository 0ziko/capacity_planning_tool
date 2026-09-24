from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.models import Item, PlanLine, PlanOperationSegment
from app.schemas import AutoPlanRequest
from app.services.daily_scheduler import build_daily_schedule
from app.services.material_schedule import material_gate_for_batch
from tests.test_daily_schedule_faz11 import _pilot_wc_machine, _machine_op


@pytest.mark.parametrize("policy", ["conditional", "strict"])
@pytest.mark.parametrize("ready_date", [date(2026, 9, 9), None])
def test_expected_material_daily_date_and_missing_date(db, policy, ready_date):
    suffix = f"{policy}-{bool(ready_date)}"
    wc, machine = _pilot_wc_machine(db, f"D4-M-{suffix}")
    item = Item(code=f"D4-P-{suffix}", name="D4", product_group="G")
    db.add(item)
    db.commit()
    op, order = _machine_op(db, wc, machine, item)
    order.material_status = "expected"
    order.material_ready_date = ready_date
    db.add(PlanLine(order_id=order.id, operation_id=op.id, work_center_id=wc.id,
                    week_start=date(2026, 9, 7), planned_hours=1, planned_qty=2))
    db.commit()
    req = AutoPlanRequest(start_week=date(2026, 9, 7), weeks=1,
                          work_center_ids=[wc.id], planning_granularity="daily_detailed",
                          material_policy=policy)
    build_daily_schedule(db, req, username="test")
    db.flush()
    segments = db.query(PlanOperationSegment).filter_by(order_id=order.id).all()
    if ready_date:
        assert segments
        assert all(s.start_at.date() >= ready_date for s in segments)
    else:
        assert not segments


def test_batch_rounding_explanation_matches_latest_material_date():
    batch = SimpleNamespace(orders=[SimpleNamespace(order=SimpleNamespace(
        material_status="expected", material_ready_date=day))
        for day in [date(2026, 9, 8), date(2026, 9, 22)]])
    gate = material_gate_for_batch(batch)
    assert gate.earliest_week == date(2026, 9, 28)
    assert "2026-09-28" in gate.week_rounding_note


def test_ready_future_date_rejected_by_crud_and_excel(client, auth, db):
    from tests.test_capacity_flow import _xlsx
    from app.models import RoutingOperation, WorkCenter

    v_item = Item(code="D4-VALIDATION", name="Validation")
    v_wc = WorkCenter(code="D4-VAL-WC", name="D4 WC", is_planned=True)
    db.add_all([v_item, v_wc])
    db.flush()
    db.add(RoutingOperation(item_id=v_item.id, seq=10, operation_name="Op", work_center_id=v_wc.id, cycle_time_sec=60))  # giris kapisi: rota sart
    db.commit()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    body = dict(order_no="D4-VALIDATION", item_code="D4-VALIDATION", quantity=7,
                due_date=tomorrow, material_status="ready", material_ready_date=tomorrow)
    rejected = client.post("/api/orders", headers=auth, json=body)
    assert rejected.status_code == 400 and "Bekleniyor" in rejected.text
    created = client.post("/api/orders", headers=auth, json={**body, "material_ready_date": date.today().isoformat()})
    assert created.status_code == 201, created.text
    oid = created.json()["id"]
    rejected = client.put(f"/api/orders/{oid}", headers=auth, json={**body, "quantity": 999})
    assert rejected.status_code == 400
    headers = ["Sipariş No", "Termin", "Stok Kodu", "Miktar", "Malzeme Durumu", "Malzeme Hazır Tarihi"]
    rows = [["D4-VALIDATION", tomorrow, "D4-VALIDATION", 999, "ready", tomorrow],
            ["D4-INVALID-NEW", tomorrow, "D4-VALIDATION", 999, "ready", tomorrow]]
    imported = client.post("/api/imports/orders", headers=auth,
                           files={"file": ("orders.xlsx", _xlsx(headers, rows), "application/octet-stream")})
    assert imported.status_code == 200, imported.text
    assert len(imported.json()["errors"]) == 2
    orders = client.get("/api/orders", headers=auth).json()
    assert next(o for o in orders if o["id"] == oid)["quantity"] == 7
    assert not any(o["order_no"] == "D4-INVALID-NEW" for o in orders)
    valid = client.put(f"/api/orders/{oid}", headers=auth, json={**body, "material_status": "expected"})
    assert valid.status_code == 200


@pytest.mark.parametrize("policy", ["strict", "conditional"])
def test_revision_and_compare_preserve_material_policy(client, auth, policy):
    from tests.test_revenue_modes import WEEK, _setup
    wc_id = _setup(client, auth)
    order = client.post("/api/orders", headers=auth, json=dict(
        order_no=f"D4-REV-{policy}", due_date="2026-09-18", item_code="UCUZ", quantity=10)).json()
    scope = dict(start_week=WEEK.isoformat(), weeks=2, work_center_ids=[wc_id], material_policy=policy)
    comparison = client.post("/api/plan/compare", headers=auth, json=scope)
    assert comparison.status_code == 200, comparison.text
    for mode in ("due", "revenue"):
        assert (comparison.json()[mode]["created_lines"] == 0) == (policy == "strict")
    created = client.post("/api/plan/revisions", headers=auth,
                          json={**scope, "reason_codes": ["customer_postpone"]})
    assert created.status_code == 200, created.text
    rev = created.json()
    assert rev["material_policy"] == policy
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    assert (calc.json()["compare"]["proposed"]["line_count"] == 0) == (policy == "strict")
    applied = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert applied.status_code == 200, applied.text
    assert applied.json()["material_policy"] == policy
    lines = client.get("/api/plan/lines", headers=auth,
                       params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json()
    assert any(line["order_id"] == order["id"] for line in lines) == (policy == "conditional")
    if policy == "conditional":
        saved = [line for line in lines if line["order_id"] == order["id"]]
        assert all(line["material_unverified"] is True for line in saved)
        report = client.get("/api/plan/orders", headers=auth, params={"work_center_ids": [wc_id]})
        assert report.status_code == 200, report.text
        row = next(r for r in report.json() if r["order_id"] == order["id"])
        assert row["conditional_line_count"] == len(saved)
        assert row["unknown_material_line_count"] == 0
        assert "Koşullu plan" in row["material_note"]
        gantt = client.get("/api/plan/gantt", headers=auth, params={
            "work_center_id": wc_id, "start": WEEK.isoformat(), "end": "2026-09-20"})
        assert gantt.status_code == 200, gantt.text
        assert all(b["material_unverified"] is True for b in gantt.json()["bars"]
                   if b["order_no"] == order["order_no"])


@pytest.mark.parametrize("flag", [None, False, True])
def test_material_provenance_survives_job_move_scaling(flag):
    from app.services.job_moves import _to_draft, _scale_line
    from app.services.planning import draft_line_to_dict
    from app.services.material_schedule import recorded_material_note
    order = SimpleNamespace(order_no="PROVENANCE")
    row = PlanLine(order_id=1, operation_id=1, work_center_id=1,
                   week_start=date(2026, 9, 7), planned_hours=2, planned_qty=20,
                   mode="auto", material_unverified=flag)
    scaled = _scale_line(_to_draft(row, order), 0.5)
    assert scaled.material_unverified is flag
    assert draft_line_to_dict(scaled)["material_unverified"] is flag
    assert recorded_material_note(flag)
