"""Close the four verified empty batches; retain their metadata and snapshot."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.models import ProductionBatch, PlanLine, Order
from app.services.production_batches import close_empty_batches
from app.services.plan_preflight import _items_without_routing
expected={152:'6005577',153:'6005582',189:'6005576',209:'6005580'}
with SessionLocal() as db:
    rows=[]
    for bid,code in expected.items():
        b=db.get(ProductionBatch,bid)
        assert b and b.item.code==code and not b.orders
        assert not db.query(Order).filter(Order.item_id==b.item_id).count()
        assert not db.query(PlanLine).filter(PlanLine.production_batch_id==bid).count()
        rows.append(dict(id=bid,code=code,batch_no=b.batch_no,status=b.status,quantity=b.quantity))
    path=Path(__file__).resolve().parents[2]/'outputs'/'deleted-order-batches-before.json'
    if not path.exists():path.write_text(json.dumps(rows,indent=2),encoding='utf-8')
    close_empty_batches(db,set(expected))
    assert not {r.item_code for r in _items_without_routing(db)}.intersection(expected.values())
    db.commit()
    print(json.dumps({'closed_batches':list(expected),'remaining_routing_gaps':[r.item_code for r in _items_without_routing(db)]}))
