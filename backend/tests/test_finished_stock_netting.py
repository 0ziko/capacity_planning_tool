"""FAZ 07 / M2: bitmis stok netlemesi ve malzeme hazir tarihi."""

from datetime import date

from app.models import Item, Order, Reservation, RoutingOperation, Shipment
from app.models.planning import ProductionActual
from app.services.order_finished_netting import (
    PROVENANCE_EXTERNAL,
    PROVENANCE_PRODUCTION,
    compute_order_demand_netting,
)
from app.services.material_schedule import material_earliest_week
from app.services.remaining_work import build_work_map_for_order, produced_qty_map, SchedulingContext
from tests.test_revenue_modes import WEEK, _setup

START = date(2026, 9, 14)


def _last_op(db, item_code="UCUZ"):
    return (
        db.query(RoutingOperation)
        .join(Item)
        .filter(Item.code == item_code)
        .order_by(RoutingOperation.seq.desc())
        .first()
    )


def test_external_reservation_reduces_net_70(client, auth, db):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FN-1", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 100, "unit_price": 1},
    )
    oid = r.json()["id"]
    order = db.get(Order, oid)
    db.add(
        Reservation(
            item_id=order.item_id,
            order_id=oid,
            quantity=30,
            source="manual",
            stock_provenance=PROVENANCE_EXTERNAL,
        )
    )
    db.commit()
    net = compute_order_demand_netting(db, order)
    assert net.net_production_qty == 70


def test_production_and_reservation_not_double_80(client, auth, db):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FN-2", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 100, "unit_price": 1},
    )
    oid = r.json()["id"]
    order = db.get(Order, oid)
    op = _last_op(db)
    db.add(
        ProductionActual(
            prod_date=START,
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no="FN-2",
            quantity=20,
            earned_hours=20,
        )
    )
    db.add(
        Reservation(
            item_id=order.item_id,
            order_id=oid,
            quantity=20,
            source="manual",
            stock_provenance=PROVENANCE_PRODUCTION,
        )
    )
    db.commit()
    produced, _ = produced_qty_map(db)
    net = compute_order_demand_netting(db, order, produced_map=produced)
    assert net.net_production_qty == 100
    ctx = SchedulingContext(horizon_start=START, horizon_end_exclusive=date(2026, 10, 1))
    wm = build_work_map_for_order(db, order, ctx, produced=produced)
    assert round(sum(w.qty_to_schedule for w in wm.values()), 1) == 80.0


def test_shipped_and_produced_not_double(client, auth, db):
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FN-3", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 100, "unit_price": 1},
    )
    oid = r.json()["id"]
    order = db.get(Order, oid)
    op = _last_op(db)
    db.add(Shipment(item_id=order.item_id, order_id=oid, ship_date=START, quantity=20))
    db.add(
        ProductionActual(
            prod_date=START,
            work_center_id=op.work_center_id,
            item_id=op.item_id,
            operation_seq=op.seq,
            order_no="FN-3",
            quantity=20,
            earned_hours=20,
        )
    )
    db.commit()
    produced, _ = produced_qty_map(db)
    net = compute_order_demand_netting(db, order, produced_map=produced)
    assert net.demand_balance == 80
    assert net.net_production_qty == 80


def test_material_ready_date_week_gate():
    ew, _ = material_earliest_week(date(2026, 9, 21))
    assert ew == date(2026, 9, 21)
    ew2, _ = material_earliest_week(date(2026, 9, 18))
    assert ew2 == date(2026, 9, 21)


def test_material_unknown_conditional_vs_strict(client, auth):
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FN-U", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    body = {"start_week": START.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "replace_existing": True}
    cond = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**body, "material_policy": "conditional"}).json()
    assert cond["created"] >= 1
    assert cond.get("material_unverified") is True
    _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**body, "replace_existing": True})
    strict = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**body, "material_policy": "strict"}).json()
    assert strict["created"] == 0
    assert any(u.get("reason") == "malzeme_unknown_strict" for u in strict.get("unplanned") or [])


def test_material_expected_no_plan_before_ready_week(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={
            "order_no": "FN-M",
            "due_date": "2026-09-20",
            "item_code": "UCUZ",
            "quantity": 10,
            "unit_price": 1,
            "material_status": "expected",
            "material_ready_date": "2026-09-21",
        },
    )
    oid = r.json()["id"]
    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 1, "work_center_ids": [wc_id], "replace_existing": True},
    )
    lines = client.get("/api/plan/lines", headers=auth, params={"start": START.isoformat(), "work_center_ids": [wc_id]}).json()
    assert not any(l["order_id"] == oid and str(l["week_start"]).startswith("2026-09-14") for l in lines)


def test_orders_import_material_roundtrip(client, auth):
    from tests.test_capacity_flow import _upload

    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Termin", "Stok Kodu", "Miktar", "Malzeme Durumu", "Malzeme Hazır Tarihi"],
        [["FN-X", "2026-09-20", "UCUZ", 5, "expected", "2026-09-21"]],
    )
    o = next(x for x in client.get("/api/orders", headers=auth).json() if x["order_no"] == "FN-X")
    assert o["material_status"] == "expected"
    assert o["material_ready_date"] == "2026-09-21"
    r2 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FN-LEG", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 1, "unit_price": 1},
    )
    assert r2.status_code == 201
    leg = next(x for x in client.get("/api/orders", headers=auth).json() if x["order_no"] == "FN-LEG")
    assert leg.get("material_status") == "unknown"


def test_plan_hours_reflect_net_70(client, auth, db):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FN-P", "due_date": "2026-09-20", "item_code": "UCUZ", "quantity": 100, "unit_price": 1},
    )
    oid = r.json()["id"]
    order = db.get(Order, oid)
    db.add(
        Reservation(
            item_id=order.item_id,
            order_id=oid,
            quantity=30,
            source="manual",
            stock_provenance=PROVENANCE_EXTERNAL,
        )
    )
    db.commit()
    _plan_with_ack(client,
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "replace_existing": True},
    )
    hours = sum(
        l["planned_hours"]
        for l in client.get("/api/plan/lines", headers=auth, params={"start": START.isoformat(), "work_center_ids": [wc_id]}).json()
    )
    assert round(hours, 1) == 70.0

from tests.test_capacity_flow import _plan_with_ack
