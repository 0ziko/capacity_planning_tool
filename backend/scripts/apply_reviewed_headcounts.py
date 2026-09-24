import json
from pathlib import Path
from datetime import date
from collections import Counter
from app.db.session import SessionLocal
from app.models.master import WorkCenter, WorkCenterWeek

root = Path(__file__).resolve().parents[2]
rows = json.loads((root/'outputs/personel-eslestirme/reviewed.json').read_text(encoding='utf-8'))
def norm(s):
    return s.strip().upper().translate(str.maketrans('İŞÇĞÜÖ', 'ISCGUO'))
with SessionLocal() as db:
    centers = {norm(w.code):w for w in db.query(WorkCenter).all()}
    counts = Counter()
    excluded = []
    for r in rows:
        code = r[10] or r[6]
        if not code:
            assert r[1:3] == ['HALİL BARBAROS','GÖÇMEN'] and 'ÇIKARTALIM' in r[11]
            excluded.append(r[1:3])
            continue
        counts[centers[norm(code)].code] += 1
    assert len(rows)==164 and sum(counts.values())==163 and len(counts)==18
    changes=[]
    for row in db.query(WorkCenterWeek).filter(WorkCenterWeek.week_start>=date(2026,9,21)).with_for_update().all():
        wc = next(w for w in centers.values() if w.id==row.work_center_id)
        if wc.code not in counts: continue
        changes.append(dict(id=row.id,code=wc.code,week=str(row.week_start),old=row.headcount,new=counts[wc.code],efficient_hours=row.efficient_hours_per_person,working_days=row.working_days,note=row.note))
        row.headcount=counts[wc.code]
    assert len(changes)==198
    audit=dict(counts=dict(counts),excluded=excluded,unassigned_centers=[w.code for w in centers.values() if w.code not in counts],changes=changes)
    path=root/'outputs/personel-eslestirme/uygulama-kaydi.json'
    assert not path.exists(), 'Audit already exists; do not repeat mutation'
    path.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    db.commit()
    for change in changes:
        row=db.get(WorkCenterWeek,change['id'])
        assert (row.headcount,row.efficient_hours_per_person,row.working_days,row.note)==(change['new'],change['efficient_hours'],change['working_days'],change['note'])
    print(json.dumps(dict(verified=len(changes),counts=dict(counts),unassigned=audit['unassigned_centers'],first=min(c['week'] for c in changes),last=max(c['week'] for c in changes)),ensure_ascii=True))
