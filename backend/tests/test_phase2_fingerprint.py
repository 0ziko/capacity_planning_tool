"""Reviewed plans must not survive changes to fulfillment or shared MES inputs."""
from datetime import date
from app.models import Item, WorkCenter, RoutingOperation, BomLine, Order, Shipment, ProductionBatch, ProductionBatchOrder
from app.models.mes import MesDetail
from app.schemas import AutoPlanRequest
from app.services.plan_input_fingerprint import compute_plan_input_fingerprint


def test_fingerprint_tracks_mes_shipping_bom_and_batch_members(db, monkeypatch):
    wc=WorkCenter(code="D2-FP",name="D2",is_planned=True)
    fg=Item(code="6920011",name="FG")
    wip=Item(code="5920011",name="WIP")
    db.add_all([wc,fg,wip]); db.flush()
    order=Order(order_no="D2-FP",item_id=fg.id,quantity=10,due_date=date(2026,10,1))
    bom=BomLine(item_id=fg.id,component_code=wip.code,source_wip=wip.code,quantity=2,recipe_seq=0)
    op=RoutingOperation(item_id=wip.id,work_center_id=wc.id,seq=10,operation_name="WIP",cycle_time_sec=60)
    db.add_all([order,bom,op]); db.flush()
    req=AutoPlanRequest(start_week=date(2026,9,14),weeks=2,work_center_ids=[wc.id])
    def fingerprint():
        db.flush()
        return compute_plan_input_fingerprint(db,req)
    previous=fingerprint()
    def changed():
        nonlocal previous
        current=fingerprint()
        assert current != previous
        previous=current
    mes=MesDetail(detail_id="D2-FP-1",prod_date=date(2026,9,14),material_code=wip.code,
                  machine_code="D2",quantity=3,mapping={"status":"unmapped"})
    db.add(mes); changed()
    mes.quantity=4; changed()
    mes.mapping={"status":"mapped","kind":"wip","work_center_id":wc.id}; changed()
    mes.updated_by="audit_only"
    assert fingerprint()==previous
    shipment=Shipment(item_id=fg.id,order_id=order.id,quantity=2,ship_date=date(2026,9,15))
    db.add(shipment); changed()
    shipment.quantity=3; changed()
    bom.quantity=3; changed()
    op.cycle_time_sec=120; changed()
    batch=ProductionBatch(batch_no="D2-FP",item_id=fg.id,quantity=10,due_date=date(2026,10,1))
    db.add(batch); db.flush()
    member=ProductionBatchOrder(batch_id=batch.id,order_id=order.id,quantity=10)
    db.add(member); changed()
    member.quantity=8; changed()
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "production_source", "mes")
    changed()
    # No mutation: repeated calculation is stable.
    assert fingerprint()==previous
