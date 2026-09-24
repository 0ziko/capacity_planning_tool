from app.services.operation_constraints import unplanned_entry
from app.models import Item, RoutingOperation
from tests.test_revenue_modes import WEEK, _setup


def test_small_unplanned_load_preserved():
    row = unplanned_entry(order_no="T", item_code="X", operation_seq=10,
                          work_center_code="WC", hours=0.1/3600, reason="oncul_eksik")
    assert row["hours"] > 0
    assert abs(row["hours"]*3600-0.1) < 0.0001


def test_zero_cycle_reported_as_data_gap(client, auth, db):
    wc = _setup(client, auth)
    item = db.query(Item).filter_by(code="UCUZ").one()
    for op in item.operations: op.cycle_time_sec = 0
    db.commit()
    order = client.post("/api/orders",headers=auth,json=dict(order_no="ZERO-DIAG",item_code="UCUZ",quantity=10,due_date="2026-09-30")).json()
    from app.schemas import AutoPlanRequest
    from app.services.planning import simulate
    sim = simulate(db,AutoPlanRequest(start_week=WEEK,weeks=2,work_center_ids=[wc],replace_existing=True))
    rows = [r for r in sim.unplanned if r["order_no"]=="ZERO-DIAG"]
    assert rows and all(r["reason"]=="operasyon_suresi_eksik" for r in rows)
    assert not any(l.order_id==order["id"] for l in sim.lines)


def test_missing_assembly_supply_not_capacity():
    from types import SimpleNamespace
    from app.models import Order, WorkCenter
    from app.services.planning import _place_quantity
    item = Item(id=998, code="DIAG-FG")
    op = RoutingOperation(id=998, item_id=998, seq=10, work_center_id=998,
                          cycle_time_sec=3600, setup_time_min=0)
    item.operations = [op]
    order = Order(id=998, order_no="DIAG", item=item, quantity=10)
    wc = WorkCenter(id=998, code="DIAG")
    lines, unplanned, _ = _place_quantity(order,10,"DIAG",None,{998:wc},[WEEK],
        {(998,WEEK):100},assembly_outputs={"WIP":0},assembly_wip_req=[("WIP",10)])
    assert not lines
    assert unplanned[0]["reason"] == "yarimamul_eksik"
