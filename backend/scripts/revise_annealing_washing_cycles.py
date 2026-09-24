"""One-time user-authorized cycle correction; audit guards against repeat division."""
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.master import RoutingOperation, norm_op

audit_path = Path(__file__).resolve().parents[2] / 'outputs/tavlama-yikama-cevrim-revizyonu-20260921.json'
assert not audit_path.exists(), 'Existing audit: do not divide a second time.'
def selected(op):
    return bool(re.search(r'\b(tavlama|yikama)\b', norm_op(op.operation_name)))

with SessionLocal() as db:
    all_ops = db.scalars(select(RoutingOperation).with_for_update()).all()
    targets = [o for o in all_ops if selected(o)]
    assert targets
    before = {o.id: o.cycle_time_sec for o in all_ops}
    changes = []
    for o in targets:
        assert o.cycle_time_sec is not None and math.isfinite(o.cycle_time_sec) and o.cycle_time_sec > 0
        changes.append(dict(id=o.id,item_id=o.item_id,seq=o.seq,name=o.operation_name,
                            semi_finished_code=o.semi_finished_code,old=o.cycle_time_sec,
                            new=o.cycle_time_sec/1.6,setup=o.setup_time_min,
                            machine_cycle=o.machine_cycle_time_sec))
    audit = dict(created=datetime.now().isoformat(),divisor=1.6,counts=dict(Counter(o.operation_name for o in targets)),changes=changes,status='prepared')
    audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    for o,c in zip(targets,changes): o.cycle_time_sec=c['new']
    db.commit()
    db.expire_all()
    after = {o.id:o for o in db.scalars(select(RoutingOperation)).all()}
    changed_ids={c['id'] for c in changes}
    for c in changes:
        o=after[c['id']]
        assert o.cycle_time_sec==c['new'] and o.setup_time_min==c['setup'] and o.machine_cycle_time_sec==c['machine_cycle']
    assert all(after[k].cycle_time_sec==v for k,v in before.items() if k not in changed_ids)
    audit['status']='committed_and_verified'
    audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(updated=len(changes),counts=audit['counts'],machine_cycles_present=sum(c['machine_cycle'] is not None for c in changes)),ensure_ascii=True))
