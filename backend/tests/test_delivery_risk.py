from datetime import timedelta
import pytest
from sqlalchemy import event
from app.models import Reservation, Shipment, StockReceipt, ProductionBatch, ProductionBatchOrder
from app.services.delivery_risk import analyze, _place
from test_mes import case, DAY, import_rows, plan


def test_shared_calendar_and_backwards_do_not_overlap():
    cal = {10: 8., 11: 8.}
    assert _place(4, 10, cal) == (10., 10.5, 0.)
    assert _place(8, 10, cal) == (10.5, 11.5, 0.)
    assert _place(8, 10, cal)[2] == 4
    back = {10: 8., 11: 8.}
    assert _place(4, 12, back, True) == (11.5, 12., 0.)
    assert _place(8, 12, back, True) == (10.5, 11.5, 0.)


def test_delivery_chain_shared_load_filters_and_no_writes(db, case):
    for i, o in enumerate(case['orders']):
        o.quantity = 40
        o.due_date = DAY + timedelta(days=2)
        o.position_no = str(i + 1)
        o.customer = 'Customer ' + str(i)
    db.commit()
    writes = []
    def inspect(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in ('INSERT', 'UPDATE', 'DELETE'):
            writes.append(statement)
    event.listen(db.bind, 'before_cursor_execute', inspect)
    try:
        r = analyze(db, DAY)
        filtered = analyze(db, DAY, work_center_ids=[case['wc'].id])
        assert filtered['orders'] == r['orders']
        a, b = r['orders']
        assert a['forecast_finish'] == DAY + timedelta(days=2)
        assert b['forecast_finish'] == DAY + timedelta(days=3)
        assert b['status'] == 'late' and b['at_risk_qty'] == 40
        assert b['customer'] == 'Customer 1' and b['position_no'] == '2'
        assert b['steps'][0]['risk']
        assert a['steps'][0]['latest_finish'] <= a['steps'][-1]['latest_finish']
        assert not db.new and not db.dirty and not db.deleted
        assert not writes
    finally:
        event.remove(db.bind, 'before_cursor_execute', inspect)


def test_stock_reserved_and_shipped_are_counted_once(db, case):
    a, b = case['orders']
    b.item_id = a.item_id
    a.due_date = DAY
    b.due_date = DAY + timedelta(days=1)
    db.add(StockReceipt(item_id=a.item_id, receipt_date=DAY, quantity=150))
    db.add(Shipment(item_id=a.item_id, order_id=a.id, ship_date=DAY, quantity=20))
    db.add(Reservation(item_id=a.item_id, order_id=b.id, quantity=90))
    db.commit()
    rows = {o['order_id']: o for o in analyze(db, DAY)['orders']}
    assert rows[a.id]['open_qty'] == 80
    assert rows[a.id]['scenario_stock_qty'] == 40
    assert rows[b.id]['reserved_qty'] == 90
    assert sum(o['production_qty'] for o in rows.values()) == 50
    assert sum(o['reserved_qty'] + o['scenario_stock_qty'] for o in rows.values()) == 130


def test_covered_order_does_not_need_route(db, case):
    a = case['orders'][0]
    db.add(StockReceipt(item_id=a.item_id, receipt_date=DAY, quantity=100))
    db.commit()
    row = next(o for o in analyze(db, DAY)['orders'] if o['order_id'] == a.id)
    assert row['status'] == 'covered' and row['hours'] == 0
    assert row['steps'] == []


def test_missing_duration_is_not_a_zero_hour_promise(db, case):
    case['ops'][1][0].cycle_time_sec = 0
    db.commit()
    rows = analyze(db, DAY)['orders']
    assert rows[0]['status'] == 'unknown'
    assert rows[0]['forecast_finish'] is None
    assert rows[1]['status'] == 'unknown'


def test_common_wip_allocates_once_by_finish_plan(db, case):
    plan(db, case, 0, DAY, 100, final=True)
    plan(db, case, 1, DAY + timedelta(days=7), 100, final=True)
    import_rows(db, case, [('shared-risk', DAY, case['shared'], 100)])
    r = analyze(db, DAY)
    rows = {o['order_id']: o for o in r['orders']}
    assert rows[case['orders'][0].id]['steps'][0]['remaining_qty'] == 0
    assert rows[case['orders'][1].id]['steps'][0]['remaining_qty'] == 100
    assert sum(a['quantity'] for a in r['allocations']) == 100


def test_batch_wip_reaches_non_anchor_without_persistent_link(db, case):
    a, b = case['orders']
    b.item = a.item
    batch = ProductionBatch(batch_no='Risk batch', item_id=a.item_id, due_date=DAY, quantity=200)
    db.add(batch); db.flush()
    db.add_all([ProductionBatchOrder(batch_id=batch.id, order_id=o.id, quantity=100) for o in (a, b)])
    db.commit()
    line = plan(db, case, 0, DAY, 200, final=True)
    line.production_batch_id = batch.id
    db.commit()
    import_rows(db, case, [('batch-risk', DAY, case['shared'], 150)])
    rows = {o['order_id']: o for o in analyze(db, DAY)['orders']}
    assert rows[b.id]['steps'][0]['remaining_qty'] == 0
    assert rows[a.id]['steps'][0]['remaining_qty'] == 50
    assert line.order_id == a.id and line.planned_qty == 200
    assert db.query(ProductionBatchOrder).count() == 2


def test_material_ready_date_limits_prediction(db, case):
    o = case['orders'][1]
    o.quantity = 1
    o.material_ready_date = DAY + timedelta(days=3)
    db.commit()
    row = next(o2 for o2 in analyze(db, DAY)['orders'] if o2['order_id'] == o.id)
    assert row['forecast_finish'] == o.material_ready_date
