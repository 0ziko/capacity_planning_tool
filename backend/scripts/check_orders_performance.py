"""Read-only order-list timing and result equivalence across performance changes."""
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from app.services.orders import list_orders

label = sys.argv[1]
rows = []
for code in (None, '6010527'):
    with SessionLocal() as db:
        started = time.perf_counter()
        result = [r.model_dump(mode='json') for r in list_orders(db, item_code=code)]
        rows.append(dict(filter=code, seconds=round(time.perf_counter()-started, 3), count=len(result),
                         sha256=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()))
        db.rollback()
out = Path(__file__).resolve().parents[2] / 'outputs' / 'orders-performance'
out.mkdir(exist_ok=True)
(out / (label+'.json')).write_text(json.dumps(rows, indent=2), encoding='utf-8')
print(json.dumps(rows))
