from datetime import date
from app.models import Item, Order, RoutingOperation, PlanLine, WorkCenter
from app.services.planning import plan_lines


def test_plan_lines_resolve_exact_wip_name_without_root_fallback(db):
    wc = WorkCenter(code="DETAIL-WC", name="Detail", is_planned=True)
    item = Item(code="6-DETAIL", name="Finished")
    wip = Item(code="5-DETAIL-02", name="Annealed part")
    db.add_all([wc, item, wip]); db.flush()
    order = Order(order_no="DETAIL-ORDER", item_id=item.id, quantity=20, due_date=date(2026, 10, 1))
    op = RoutingOperation(item_id=item.id, seq=10, operation_name="TAVLAMA", work_center_id=wc.id,
                          semi_finished_code=wip.code, cycle_time_sec=60)
    db.add_all([order, op]); db.flush()
    db.add(PlanLine(order_id=order.id, operation_id=op.id, work_center_id=wc.id,
                   week_start=date(2026, 9, 21), planned_qty=20, planned_hours=1, mode="auto"))
    db.flush()
    row = next(r for r in plan_lines(db, [wc.id], None, None) if r.order_id == order.id)
    assert row.operation_name == "TAVLAMA"
    assert row.semi_finished_code == wip.code
    assert row.semi_finished_name == "Annealed part"
    assert row.planned_qty == 20
    op.semi_finished_code = "5-DETAIL-UNKNOWN"; db.flush()
    row = next(r for r in plan_lines(db, [wc.id], None, None) if r.order_id == order.id)
    assert row.semi_finished_name == ""
