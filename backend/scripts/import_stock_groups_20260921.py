import json
import hashlib
from pathlib import Path
from datetime import datetime
from app.db.session import SessionLocal
from app.models import Item, ImportLog
from app.services.excel import read_rows, import_items, _str

source = Path('C:/Users/ozan.deniz/Downloads/6_ile_baslayan_stok_kodlari.xlsx')
out = Path('../outputs/stok_recete_20260921')
out.mkdir(exist_ok=True)
content = source.read_bytes()
rows, errors = read_rows(content, 'items')
assert not errors, errors
codes = [_str(r['code']) for r in rows]
assert len(codes) == len(set(codes)) == 5722 and all(c.startswith('6') for c in codes)
with SessionLocal() as db:
    items = {i.code:i for i in db.query(Item).filter(Item.code.like('6%')).with_for_update()}
    assert set(items) == set(codes)
    fields = ['name','main_group','sub_group','product_group','unit']
    before = {code:{f:getattr(i,f) for f in fields} for code,i in items.items()}
    changes = [{'code':_str(r['code']),'field':f,'before':before[_str(r['code'])][f],'after':_str(r.get(f))}
               for r in rows for f in fields if _str(r.get(f)) and _str(r.get(f)) != before[_str(r['code'])][f]]
    audit = {'source':str(source),'sha256':hashlib.sha256(content).hexdigest(),'time':datetime.now().isoformat(),'before':before,'changes':changes,'status':'prepared'}
    audit_path = out/'import_audit.json'
    assert not audit_path.exists(), 'Audit exists; verify before repeating import'
    audit_path.write_text(json.dumps(audit,ensure_ascii=False),encoding='utf-8')
    inserted, updated, errors = import_items(db,rows)
    assert inserted == 0 and updated == 5722 and not errors, (inserted,updated,errors)
    db.flush()
    for r in rows:
        i = items[_str(r['code'])]
        for f in fields:
            assert getattr(i,f) == (_str(r.get(f)) or before[i.code][f])
    db.add(ImportLog(kind='items',filename=source.name,username='ozan.deniz',inserted=inserted,updated=updated,errors=''))
    db.commit()
    db.expire_all()
    for r in rows:
        i = items[_str(r['code'])]
        assert all(getattr(i,f) == (_str(r.get(f)) or before[i.code][f]) for f in fields)
    audit['status'] = 'committed_and_verified'
    audit_path.write_text(json.dumps(audit,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'updated':updated,'changes':{f:sum(c['field']==f for c in changes) for f in fields},'status':audit['status']}))
