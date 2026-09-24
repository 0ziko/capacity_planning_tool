"""Customer fulfillment and MES plan matching are distinct measurements."""
from collections import defaultdict
from sqlalchemy.orm import selectinload
from app.models import Item, Order, ProductionBatch, ProductionBatchOrder
from app.schemas import OrderProgressOut, OrderProgressOp
from app.services.stock import order_rows
from app.services.mes_actuals import measure
from app.services.bom_tree import explode_order, has_wip_structure
from app.services.remaining_work import produced_qty_map, required_qty_by_operation
from app.services.routing_resource import planning_load_hours


def report(db, wc_ids, as_of):
    orders=db.query(Order).options(selectinload(Order.item).selectinload(Item.operations)).filter(Order.status=="open").order_by(Order.due_date,Order.id).all()
    stock={r.order_id:r for r in order_rows(db)}
    batches=db.query(ProductionBatch).options(selectinload(ProductionBatch.orders).joinedload(ProductionBatchOrder.order)).all()
    matched_qty,matched_hours,planned_hours,dates=(defaultdict(float),defaultdict(float),defaultdict(float),defaultdict(list))
    intended={o.id:float(o.quantity) for o in orders}
    members={}
    active_batch={}
    for batch in batches:
        total=sum(link.quantity for link in batch.orders)
        members[batch.id]=[(link.order_id,link.quantity/total) for link in batch.orders if total>0]
        for link in batch.orders:
            if batch.status == "open":
                intended[link.order_id]=float(link.quantity)
                active_batch[link.order_id]=batch
    measured=measure(db,as_of)
    for line in measured["lines"]:
        shares=members.get(line.production_batch_id,[]) if line.production_batch_id else [(line.order_id,1.)]
        match=measured["matches"][line.id]
        for oid,share in shares:
            key=(oid,line.operation_id)
            planned_hours[key]+=line.planned_hours*share
            matched_qty[key]+=match["qty"]*share
            matched_hours[key]+=match["hours"]*share
            if match["last_date"]:
                dates[oid].extend([match["first_date"],match["last_date"]])
    credits,_=produced_qty_map(db,as_of=as_of,include_mes=True)
    netting={}
    required={o.id:required_qty_by_operation(db,o,produced=credits,netting_cache=netting) for o in orders}
    from app.services.planning_candidates import batch_anchor_order
    allocated=dict(credits)
    for batch in batches:
        if batch.status != "open":
            continue
        anchor=batch_anchor_order(batch)
        if not anchor:
            continue
        op_ids={op for link in batch.orders for op in required.get(link.order_id,{})}
        for op_id in op_ids:
            total=sum(required.get(link.order_id,{}).get(op_id,0) for link in batch.orders)
            credit=credits.get((anchor.id,op_id),0)
            for link in batch.orders:
                rq=required.get(link.order_id,{}).get(op_id,0)
                allocated[(link.order_id,op_id)]=credit*rq/total if total else 0
    out=[]
    for order in orders:
        jobs=explode_order(db,order) if has_wip_structure(order) else None
        routes=[(job.item,job.quantity) for job in jobs.wip_jobs] if jobs else []
        routes.append((order.item,order.quantity))
        ops=[]
        remainder=0.
        for item,quantity in routes:
            for op in sorted(item.operations,key=lambda op:op.seq):
                if wc_ids and op.work_center_id not in wc_ids:
                    continue
                key=(order.id,op.id)
                left=max(required[order.id].get(op.id,0)-allocated.get(key,0),0)
                batch=active_batch.get(order.id)
                gross_share=1.
                setup_share=1.
                setup_required=allocated.get(key,0)<=1e-6
                if batch:
                    total=sum(link.quantity for link in batch.orders)
                    gross_share=intended[order.id]/total if total else 0
                    total_left=sum(max(required.get(link.order_id,{}).get(op.id,0)-allocated.get((link.order_id,op.id),0),0) for link in batch.orders)
                    setup_share=left/total_left if total_left else 0
                    setup_required=not any(allocated.get((link.order_id,op.id),0)>1e-6 for link in batch.orders)
                setup=planning_load_hours(op,1,setup_required=True)-planning_load_hours(op,1,setup_required=False)
                remainder+=planning_load_hours(op,left,setup_required=False)+(setup*setup_share if left and setup_required else 0)
                ops.append(OrderProgressOp(operation_id=op.id,item_code=item.code,required_qty=quantity,
                    remaining_qty=left,operation_seq=op.seq,operation_name=op.operation_name,
                    work_center_code=op.work_center.code,semi_finished_code=op.semi_finished_code or "",
                    required_hours=planning_load_hours(op,quantity,setup_required=False)+setup*gross_share,planned_hours=planned_hours[key],
                    produced_qty=matched_qty[key],earned_hours=matched_hours[key],
                    pct=min(matched_qty[key]/quantity*100,100) if quantity else 0))
        coverage=stock[order.id]
        remaining=max(coverage.remaining,0)
        fulfilled=min(coverage.reserved+coverage.shipped,order.quantity)
        final=max(order.item.operations,key=lambda op:op.seq) if order.item.operations else None
        ds=dates[order.id]
        out.append(OrderProgressOut(order_id=order.id,order_no=order.order_no,position_no=order.position_no or "",
            customer=order.customer,item_code=order.item.code,quantity=order.quantity,due_date=order.revised_due_date or order.due_date,
            required_hours=sum(op.required_hours for op in ops),earned_hours=sum(op.earned_hours for op in ops),
            produced_qty=matched_qty[(order.id,final.id)] if final else 0,
            pct=fulfilled/order.quantity*100 if order.quantity else 0,
            status="completed" if remaining<=1e-6 else "in_progress" if fulfilled>0 else "not_started",
            first_prod_date=min(ds) if ds else None,last_prod_date=max(ds) if ds else None,ops=ops,
            production_source="mes",reserved_qty=coverage.reserved,shipped_qty=coverage.shipped,
            unfulfilled_qty=remaining,planned_share_qty=intended[order.id],remaining_hours=remainder))
    return out
