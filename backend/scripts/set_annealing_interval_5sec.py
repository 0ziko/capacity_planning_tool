"""User-authorized fill of missing annealing output intervals; saves prior values."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.models import RoutingOperation, WorkCenter, norm_op
from app.services.requirements import item_total_hours

with SessionLocal() as db:
    ops = db.query(RoutingOperation).join(WorkCenter).filter(
        WorkCenter.code == 'TAVLAMA',
        (RoutingOperation.line_interval_sec.is_(None)) | (RoutingOperation.line_interval_sec <= 0),
    ).with_for_update().all()
    ops = [op for op in ops if re.search(r'\btavlama\b', norm_op(op.operation_name))]
    backup = Path(__file__).resolve().parents[2] / 'outputs' / ('annealing_interval_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json')
    backup.write_text(json.dumps([dict(id=op.id, name=op.operation_name, line_interval_sec=op.line_interval_sec,
        cycle_time_sec=op.cycle_time_sec, units_per_cycle=op.units_per_cycle) for op in ops], ensure_ascii=False, indent=2), encoding='utf-8')
    for op in ops:
        op.line_interval_sec = 5.0
    db.commit()
    result = item_total_hours(db, '6005738', 1)
    print(json.dumps(dict(updated=len(ops), backup=str(backup), missing=result['missing_operation_count'],
        line_hours=result['line_hours'], operations=[o for o in result['operations'] if o['planning_mode']=='line']), ensure_ascii=True))
