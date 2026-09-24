from datetime import date
from app.models import Order, Reservation, PlanLine, RoutingOperation, Item
from app.services.orders import order_schedule
from app.services.order_finished_netting import PROVENANCE_EXTERNAL
from tests.test_orders_flow import _setup


def test_schedule_uses_net_need_and_checks_each_operation(client, auth, db):
    _setup(client, auth)
    oid = client.post("/api/orders", headers=auth, json={"order_no":"NET-SCHED", "item_code":"MAM-1", "quantity":100, "due_date":"2026-09-30"}).json()["id"]
    order = db.get(Order, oid)
    ops = sorted(order.item.operations, key=lambda o:o.seq)
    db.add(Reservation(item_id=order.item_id, order_id=oid, quantity=40, source="manual", stock_provenance=PROVENANCE_EXTERNAL))
    for op in ops:
        db.add(PlanLine(order_id=oid, operation_id=op.id, work_center_id=op.work_center_id,
                       week_start=date(2026,9,7), planned_qty=60, planned_hours=op.hours_for(60), mode="auto"))
    db.commit()
    row = next(r for r in order_schedule(db,None) if r.order_id==oid)
    assert row.required_hours == 1.8
    assert row.coverage_pct == 100 and row.plan_status == "on_time"
    # Excess first-operation load must not hide a missing final operation.
    lines = db.query(PlanLine).filter_by(order_id=oid).order_by(PlanLine.operation_id).all()
    for line in lines:
        if line.operation_id == ops[0].id:
            line.planned_qty=1000; line.planned_hours=10
        else:
            line.planned_qty=30; line.planned_hours=0.6
    db.commit()
    row = next(r for r in order_schedule(db,None) if r.order_id==oid)
    assert row.plan_status == "partial" and row.coverage_pct < 100
    db.query(Reservation).filter_by(order_id=oid).update({Reservation.quantity:100}); db.commit()
    row = next(r for r in order_schedule(db,None) if r.order_id==oid)
    assert row.plan_status == "covered" and row.required_hours == 0
    assert row.coverage_pct == 100 and row.planned_end is None


def test_batch_schedule_assigns_remaining_need_per_member(client, auth, db):
    from app.models import ProductionBatch, ProductionBatchOrder, ProductionActual
    _setup(client, auth)
    ids = [client.post("/api/orders", headers=auth, json={"order_no":f"B-NET-{i}", "item_code":"MAM-1", "quantity":100, "due_date":"2026-09-30"}).json()["id"] for i in range(2)]
    members = [db.get(Order, oid) for oid in ids]
    ops = sorted(members[0].item.operations, key=lambda o:o.seq)
    batch = ProductionBatch(batch_no="B-NET", item_id=members[0].item_id, quantity=200, due_date=date(2026,9,30))
    db.add(batch); db.flush()
    for member in members:
        db.add(ProductionBatchOrder(batch_id=batch.id, order_id=member.id, quantity=100))
    db.add(Reservation(item_id=members[0].item_id, order_id=ids[0], quantity=40, source="manual", stock_provenance=PROVENANCE_EXTERNAL))
    for op in ops:
        db.add(ProductionActual(prod_date=date(2026,9,1), work_center_id=op.work_center_id,
              item_id=op.item_id, operation_seq=op.seq, order_no=members[1].order_no, quantity=20, earned_hours=op.hours_for(20)))
        db.add(PlanLine(order_id=ids[0], production_batch_id=batch.id, operation_id=op.id,
              work_center_id=op.work_center_id, week_start=date(2026,9,7), planned_qty=140,
              planned_hours=op.hours_for(140), mode="auto"))
    db.commit()
    rows = {r.order_id:r for r in order_schedule(db,None)}
    assert rows[ids[0]].required_hours == 1.8
    assert rows[ids[1]].required_hours == 2.4
    assert all(rows[i].plan_status == "on_time" and rows[i].coverage_pct == 100 for i in ids)
    assert rows[ids[0]].planned_hours == 1.8 and rows[ids[1]].planned_hours == 2.4

    from app.services.planning import plan_lines
    members[0].customer = "Musteri A"
    members[1].customer = "Musteri B"
    db.commit()
    output = [line for line in plan_lines(db, None, None, None) if line.production_batch_id == batch.id]
    assert len(output) == 2
    assert all(line.customer == "Musteri A / Musteri B" for line in output)
    assert [m.order_id for m in output[0].batch_members] == ids
    assert [m.quantity for m in output[0].batch_members] == [100,100]
    assert sum(line.planned_qty for line in output if line.operation_id == ops[0].id) == 140
