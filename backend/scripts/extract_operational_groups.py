"""Read-only routing snapshot for operational subgroup review."""
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter
from sqlalchemy import text
from sqlalchemy.orm import selectinload
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.models import Item, RoutingOperation, OpTransitionRule, Machine
from app.services.bom_tree import is_wip_asm_link
from app.services.routing_resource import standard_unit_hours, planning_load_hours
from app.services.scenarios import RuleLookup

out = Path(__file__).resolve().parents[2] / 'outputs' / 'operasyonel-alt-gruplar-20260922'
out.mkdir(exist_ok=True)
with SessionLocal() as db:
    db.connection(execution_options={'isolation_level':'REPEATABLE READ'})
    db.execute(text('SET TRANSACTION READ ONLY'))
    items = db.query(Item).options(selectinload(Item.operations).selectinload(RoutingOperation.work_center),
        selectinload(Item.operations).selectinload(RoutingOperation.primary_machine),
        selectinload(Item.operations).selectinload(RoutingOperation.alt_stations),
        selectinload(Item.bom_lines)).all()
    by_code = {i.code:i for i in items}
    machine_codes = {m.id:m.code for m in db.query(Machine).all()}
    lookup = RuleLookup(db)
    def branch(owner, quantity, role):
        ops=[]
        for op in owner.operations:
            try:
                p100=planning_load_hours(op, 100*quantity, setup_required=False)*60
                route100=planning_load_hours(op, 100, setup_required=False)*60
                err=''
            except ValueError as e:
                p100=None;route100=None;err=str(e)
            ops.append(dict(id=op.id, seq=op.seq, name=op.operation_name, wc=op.work_center.code,
                mode=op.work_center.planning_mode, planned=op.work_center.is_planned,
                cycle=op.cycle_time_sec, units=op.units_per_cycle, interval=op.line_interval_sec,
                setup=op.setup_time_min, time_basis=op.time_basis, crew=op.crew_size,
                standard_seconds=standard_unit_hours(op)*3600, plan100_minutes=p100, route100_minutes=route100, error=err,
                wip=op.semi_finished_code, machine=op.primary_machine.code if op.primary_machine else '',
                alternatives=sorted(machine_codes.get(s.machine_id,str(s.machine_id)) for s in op.alt_stations)))
        transitions=[]
        for a,b in zip(owner.operations,owner.operations[1:]):
            rule=lookup.get(owner,a,b)
            transitions.append(dict(from_id=a.id,to_id=b.id,rule=rule.rule,lag=rule.lag_cycles,
                                    wait=rule.wait_minutes,source=rule.source,rule_id=rule.id))
        return dict(owner=owner.code,quantity=quantity,role=role,ops=ops,transitions=transitions)
    rows=[]
    for item in sorted(items,key=lambda i:i.code):
        if not item.code.startswith('6'):
            continue
        branches=[];issues=[]
        for bl in sorted(item.bom_lines,key=lambda x:(-(x.branch_listing_sira or 0),x.component_code)):
            if is_wip_asm_link(bl.component_code,bl.source_wip,bl.recipe_seq):
                linked=by_code.get(bl.component_code)
                if not linked or not linked.operations:
                    issues.append('Yarımamul rotası eksik: '+bl.component_code)
                else:
                    branches.append(branch(linked,float(bl.quantity or 1),'Yarımamul'))
                if not bl.quantity or bl.quantity<=0:
                    issues.append('BOM miktarı geçersiz: '+bl.component_code)
        if item.operations:
            branches.append(branch(item,1.,'Mamul'))
        else:
            issues.append('Mamul bitiş rotası yok')
        rows.append(dict(code=item.code,name=item.name,main=item.main_group,sub=item.sub_group,
                         product_group=item.product_group,branches=branches,issues=issues))
    rules=[{c.name:getattr(r,c.name) for c in OpTransitionRule.__table__.columns} for r in db.query(OpTransitionRule).all()]
    result=dict(extracted_at=datetime.now().isoformat(),source='Yerel PostgreSQL; stok kartları, BOM bağlantıları, rotalar ve senaryo kuralları',
                items=rows,rules=rules)
    (out/'source.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(items=len(rows),groups=Counter(r['main'] or '(Ana grup boş)' for r in rows),
                         no_route=sum(not r['branches'] for r in rows),rules=len(rules)),ensure_ascii=True))
