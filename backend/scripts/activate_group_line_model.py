import json,sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.models import WorkCenter,RoutingOperation
from app.services.requirements import item_total_hours
out=Path(__file__).resolve().parents[2]/'outputs'
with SessionLocal() as db:
    centers=db.query(WorkCenter).filter(WorkCenter.code.in_(['TAVLAMA','YIKAMA'])).with_for_update().all()
    assert len(centers)==2
    ops=db.query(RoutingOperation).filter(RoutingOperation.work_center_id.in_([w.id for w in centers])).with_for_update().all()
    backup={'centers':[dict(id=w.id,mode=w.planning_mode) for w in centers], 'operations':[dict(id=o.id,cycle=o.cycle_time_sec,interval=o.line_interval_sec,units=o.units_per_cycle) for o in ops]}
    (out/('line_group_migration_backup_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')).write_text(json.dumps(backup,indent=2),encoding='utf-8')
    for w in centers:w.planning_mode='line'
    for o in ops:o.line_interval_sec=5.0
    db.commit()
    print(json.dumps({'updated':len(ops),'centers':[w.code for w in centers],'missing_cycle':sum(not o.cycle_time_sec or o.cycle_time_sec<=0 for o in ops),'stations':[dict(code=m.code,crew=m.required_crew_size) for w in centers for m in w.machines],'example_1':item_total_hours(db,'6005738',1)['line_hours'],'example_5':item_total_hours(db,'6005738',5)['line_hours']}))
