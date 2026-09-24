"""B1/B5: kalan is hesabi ve mukerrer planlama onleme."""

from datetime import date, timedelta

from sqlalchemy.orm import joinedload

from app.models import BomLine, Item, Order, PlanLine, ProductionActual, RoutingOperation, WorkCenter, WorkCenterShift
from app.models.planning import ProductionBatch, ProductionBatchOrder
from datetime import time

from tests.test_capacity_flow import _upload
from tests.test_revenue_modes import _setup

START = date(2026, 9, 14)
NOV = date(2026, 11, 2)
BEFORE = date(2026, 9, 7)


def _wc_shift(db, code: str) -> int:
    wc = WorkCenter(code=code, name=code, is_planned=True, is_active=True, default_efficient_hours=4)
    db.add(wc)
    db.flush()
    db.add(
        WorkCenterShift(
            work_center_id=wc.id,
            name="G",
            weekdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(18, 0),
            headcount=10,
            efficient_hours_per_person=4,
        )
    )
    _staff_db(db, wc.id, 10, 4)
    db.commit()
    return wc.id


def _plan_hours(client, auth, **params) -> float:
    return sum(l["planned_hours"] for l in client.get("/api/plan/lines", headers=auth, params=params).json())


def test_completed_production_schedules_remaining_only(client, auth, db):
    """10 adet x1 saat, 6 adet tamamlanmis -> kalan plan 4 saat."""
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RW-1", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    op = db.query(RoutingOperation).join(Item).filter(Item.code == "UCUZ").first()
    db.add(
        ProductionActual(
            prod_date=START,
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no="RW-1",
            quantity=6,
            earned_hours=6,
        )
    )
    db.commit()

    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "replace_existing": True},
    )
    hours = _plan_hours(client, auth, start=START.isoformat(), work_center_ids=[wc_id], mode="auto")
    assert round(hours, 1) == 4.0


def test_append_mode_no_duplicate_plan(client, auth):
    """10 saatlik is icin ekleme iki kez -> toplam 10 saat; ikinci cagri 0 yeni satir."""
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RW-2", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    body = {"start_week": START.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "replace_existing": False}
    r1 = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=body).json()
    assert r1["created"] >= 1
    h1 = _plan_hours(client, auth, start=START.isoformat(), work_center_ids=[wc_id])
    r2 = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=body).json()
    assert r2["created"] == 0
    h2 = _plan_hours(client, auth, start=START.isoformat(), work_center_ids=[wc_id])
    assert round(h1, 1) == 10.0
    assert round(h2, 1) == 10.0


def test_manual_plan_reduces_auto_load(client, auth, db):
    """10 saatlik iste korunmus 4 saat manuel -> yeni auto yuk 6 saat."""
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RW-3", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    order_id = r.json()["id"]
    op = db.query(RoutingOperation).join(Item).filter(Item.code == "UCUZ").first()
    client.post(
        "/api/plan/manual",
        headers=auth,
        json={"order_id": order_id, "operation_id": op.id, "week_start": START.isoformat(), "planned_hours": 4, "planned_qty": 4},
    )
    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "replace_existing": True},
    )
    auto_h = _plan_hours(client, auth, start=START.isoformat(), work_center_ids=[wc_id], mode="auto")
    manual_h = _plan_hours(client, auth, start=START.isoformat(), work_center_ids=[wc_id], mode="manual")
    assert round(manual_h, 1) == 4.0
    assert round(auto_h, 1) == 6.0


def test_outside_horizon_plan_not_repeated(client, auth, db):
    """Ufuk disinda korunmus 10 adet kesin plan -> ufuk icine tekrar eklenmez."""
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RW-4", "due_date": "2026-11-30", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    op = db.query(RoutingOperation).join(Item).filter(Item.code == "UCUZ").first()
    order = db.query(Order).filter(Order.order_no == "RW-4").first()
    db.add(
        PlanLine(
            order_id=order.id,
            operation_id=op.id,
            work_center_id=wc_id,
            week_start=NOV,
            planned_hours=10,
            planned_qty=10,
            mode="auto",
            created_by="test",
        )
    )
    db.commit()

    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 1, "work_center_ids": [wc_id], "replace_existing": True},
    )
    inside = _plan_hours(client, auth, start=START.isoformat(), end=(START + timedelta(days=6)).isoformat(), work_center_ids=[wc_id])
    total = _plan_hours(client, auth, start=BEFORE.isoformat(), work_center_ids=[wc_id])
    assert inside == 0.0
    assert round(total, 1) == 10.0


def test_completed_op_zero_and_past_unplanned_rescheduled(client, auth, db):
    """Tamamlanan op 0 yuk; gecmis plansiz uretim yeniden planlanir."""
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RW-5", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    op = db.query(RoutingOperation).join(Item).filter(Item.code == "UCUZ").first()
    db.add(
        ProductionActual(
            prod_date=START,
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no="RW-5",
            quantity=10,
            earned_hours=10,
        )
    )
    db.commit()
    r_done = _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "replace_existing": True},
    ).json()
    assert r_done["created"] == 0

    db.query(ProductionActual).delete()
    order = db.query(Order).filter(Order.order_no == "RW-5").first()
    db.add(
        PlanLine(
            order_id=order.id,
            operation_id=op.id,
            work_center_id=wc_id,
            week_start=BEFORE,
            planned_hours=10,
            planned_qty=10,
            mode="auto",
            created_by="test",
        )
    )
    db.commit()

    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "replace_existing": True},
    )
    inside = _plan_hours(client, auth, start=START.isoformat(), work_center_ids=[wc_id], mode="auto")
    assert round(inside, 1) == 10.0


def test_batch_bom_coefficient_preserved(client, auth, db):
    """Uretim partisi + BOM katsayisi 2: toplam miktar korunur."""
    wc_id = _wc_shift(db, "BOM-WC")
    fg = Item(code="6999901", name="FG BOM", product_group="T")
    wip = Item(code="5999901", name="WIP BOM", product_group="T")
    db.add_all([fg, wip])
    db.flush()
    db.add(RoutingOperation(item_id=wip.id, seq=10, operation_name="WIP-OP", work_center_id=wc_id, cycle_time_sec=3600, setup_time_min=0))
    db.add(RoutingOperation(item_id=fg.id, seq=10, operation_name="FG-OP", work_center_id=wc_id, cycle_time_sec=3600, setup_time_min=0))
    db.add(BomLine(item_id=fg.id, component_code="5999901", quantity=2, source_wip="5999901"))
    db.commit()

    o1 = client.post("/api/orders", headers=auth, json={"order_no": "RW-B1", "due_date": "2026-10-01", "item_code": "6999901", "quantity": 5, "unit_price": 1}).json()
    o2 = client.post("/api/orders", headers=auth, json={"order_no": "RW-B2", "due_date": "2026-10-05", "item_code": "6999901", "quantity": 5, "unit_price": 1}).json()
    batch = client.post(
        "/api/plan/merge",
        headers=auth,
        json={"order_ids": [o1["id"], o2["id"]], "note": "test"},
    ).json()

    wk = START.isoformat()
    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": wk, "weeks": 8, "work_center_ids": [wc_id], "replace_existing": True},
    )
    lines = client.get("/api/plan/lines", headers=auth, params={"start": wk, "work_center_ids": [wc_id]}).json()
    batch_lines = [l for l in lines if l.get("production_batch_id") == batch["id"]]
    wip_h = sum(l["planned_hours"] for l in batch_lines if l.get("semi_finished_code"))
    fg_h = sum(l["planned_hours"] for l in batch_lines if not l.get("semi_finished_code"))
    assert round(wip_h, 1) == 20.0  # 10 FG * BOM 2
    assert round(fg_h, 1) == 10.0

from tests.test_capacity_flow import _plan_with_ack

from tests.test_capacity_flow import _staff_db
