from datetime import date
from uuid import uuid4
import pytest
from app.models import Item, Order, ProductionBatch, ProductionBatchOrder
from app.services.plan_preflight import _items_without_routing
from app.services.production_batches import open_batches_with_ops


@pytest.mark.parametrize('bulk', [False, True])
def test_last_order_deletion_closes_batch_and_unblocks_preflight(db, client, auth, bulk):
    item = Item(code='EMPTY-'+uuid4().hex, name='Missing route')
    db.add(item); db.flush()
    batch = ProductionBatch(batch_no=uuid4().hex, item_id=item.id, quantity=20, due_date=date.today())
    orders = [Order(order_no=uuid4().hex, item_id=item.id, quantity=10, due_date=date.today(), status='open') for _ in range(2)]
    db.add_all([batch,*orders]); db.flush()
    ids=[o.id for o in orders]; bid=batch.id; code=item.code
    db.add_all([ProductionBatchOrder(batch_id=bid, order_id=oid, quantity=10) for oid in ids]);db.commit()
    assert any(r.item_code==code for r in _items_without_routing(db))
    assert client.delete(f'/api/orders/{ids[0]}',headers=auth).status_code==204
    db.expire_all()
    assert db.get(ProductionBatch,bid).status=='open'
    assert any(r.item_code==code for r in _items_without_routing(db))
    response=client.delete('/api/orders',params={'status':'open'},headers=auth) if bulk else client.delete(f'/api/orders/{ids[1]}',headers=auth)
    assert response.status_code==204
    db.expire_all()
    assert db.get(ProductionBatch,bid).status=='closed'
    assert not db.query(ProductionBatchOrder).filter_by(batch_id=bid).count()
    assert not any(r.item_code==code for r in _items_without_routing(db))


def test_old_empty_batch_is_not_a_planning_demand(db):
    item=Item(code='ORPHAN-'+uuid4().hex,name='No demand')
    db.add(item);db.flush()
    batch=ProductionBatch(batch_no=uuid4().hex,item_id=item.id,quantity=75,due_date=date.today(),status='open')
    db.add(batch);db.commit()
    assert batch.id not in {b.id for b in open_batches_with_ops(db)}
    assert not any(r.item_code==item.code for r in _items_without_routing(db))


def test_import_bulk_deletion_closes_any_empty_batch(db):
    from app.services.orders import bulk_delete_orders
    item=Item(code='IMPORT-DELETE-'+uuid4().hex,name='Unrelated stock code')
    db.add(item);db.flush()
    order=Order(order_no=uuid4().hex,item_id=item.id,quantity=10,due_date=date.today(),status='open')
    batch=ProductionBatch(batch_no=uuid4().hex,item_id=item.id,quantity=10,due_date=date.today(),status='open')
    db.add_all([order,batch]);db.flush()
    bid=batch.id;oid=order.id
    db.add(ProductionBatchOrder(batch_id=bid,order_id=oid,quantity=10));db.commit()
    assert bulk_delete_orders(db,[oid])==1
    db.commit();db.expire_all()
    assert db.get(ProductionBatch,bid).status=='closed'
    assert not any(r.item_code==item.code for r in _items_without_routing(db))


def test_closed_demand_does_not_block_but_reopened_demand_is_checked(db):
    item=Item(code='CLOSED-DEMAND-'+uuid4().hex,name='Missing route')
    db.add(item);db.flush()
    order=Order(order_no=uuid4().hex,item_id=item.id,quantity=10,due_date=date.today(),status='closed')
    batch=ProductionBatch(batch_no=uuid4().hex,item_id=item.id,quantity=10,due_date=date.today(),status='open')
    db.add_all([order,batch]);db.flush()
    db.add(ProductionBatchOrder(batch_id=batch.id,order_id=order.id,quantity=10));db.commit()
    assert not any(r.item_code==item.code for r in _items_without_routing(db))
    order.status='open';db.commit()
    assert any(r.item_code==item.code for r in _items_without_routing(db))
