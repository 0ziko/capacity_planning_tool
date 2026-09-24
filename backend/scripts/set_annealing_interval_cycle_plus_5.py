"""Set previously updated annealing intervals to saved cycle seconds plus five."""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.models import RoutingOperation
from app.services.requirements import item_total_hours

out = Path(__file__).resolve().parents[2] / 'outputs'
original = json.loads((out / 'annealing_interval_backup_20260922_101822.json').read_text(encoding='utf-8'))
by_id = {row['id']: row for row in original}
with SessionLocal() as db:
    ops = db.query(RoutingOperation).filter(RoutingOperation.id.in_(by_id)).with_for_update().all()
    assert len(ops) == len(by_id)
    for op in ops:
        assert op.line_interval_sec == 5.0, f'Interval changed: {op.id}'
        assert op.cycle_time_sec == by_id[op.id]['cycle_time_sec'], f'Cycle changed: {op.id}'
        assert op.cycle_time_sec is not None and math.isfinite(op.cycle_time_sec) and op.cycle_time_sec >= 0
    backup = out / ('annealing_cycle_plus_5_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json')
    backup.write_text(json.dumps([dict(id=op.id, cycle_time_sec=op.cycle_time_sec, line_interval_sec=op.line_interval_sec) for op in ops], indent=2), encoding='utf-8')
    for op in ops:
        op.line_interval_sec = round(op.cycle_time_sec + 5.0, 6)
    db.commit()
    db.expire_all()
    for op in ops:
        assert op.line_interval_sec == round(by_id[op.id]['cycle_time_sec'] + 5.0, 6)
        assert op.cycle_time_sec == by_id[op.id]['cycle_time_sec']
    result = item_total_hours(db, '6005738', 1)
    print(json.dumps(dict(updated=len(ops), missing=result['missing_operation_count'], line_hours=result['line_hours'],
        intervals=[o['line_interval_sec'] for o in result['operations'] if o['planning_mode']=='line'])))
