import json,re
from collections import defaultdict,Counter
from pathlib import Path
from app.models import norm_op
from app.services.excel import read_rows,_str

out=Path('../outputs/stok_recete_20260921')
d=json.loads((out/'fg_recipe_snapshot.json').read_text(encoding='utf-8'))
source=Path('C:/Users/ozan.deniz/Downloads/6_ile_baslayan_stok_kodlari.xlsx')
rows,errs=read_rows(source.read_bytes(),'items')
assert not errs
bom=defaultdict(list); ops=defaultdict(list)
for b in d['bom']: bom[b['item_code']].append(b)
for o in d['operations']: ops[o['item_code']].append(o)
def kind(name):
    n=norm_op(name)
    if re.fullmatch(r'(?:\d+\s*[.]?\s*)?(?:(?:ara|agiz)\s+)?tavlama',n): return 't'
    if re.fullmatch(r'(?:\d+\s*[.]?\s*)?(?:ara\s+)?yikama',n): return 'y'
    return None

result=[]; audit={}
for r in rows:
    code=_str(r['code']); evidence=[]; seen=set(); notes=[]
    # Only rows owned by this 6-code; no query or traversal of WIP recipes.
    for b in bom[code]:
        label=b['name'].rsplit('-',1)[-1].strip()
        k=kind(label) if '-' in b['name'] and b['code'].startswith('5') else None
        if k and b['seq']%10==0:
            evidence.append(dict(kind=k,label=label,output=b['code'],source=b['source'],record=b['id'],origin='reçete'))
            seen.add(b['code'])
    for o in ops[code]:
        k=kind(o['name'])
        if k and o['output'] not in seen:
            evidence.append(dict(kind=k,label=o['name'],output=o['output'],record=o['id'],origin='operasyon'))
            seen.add(o['output'])
    # Material descriptions are not operation definitions. Flag, never count.
    excluded=[]
    for b in bom[code]:
        label=b['name'].rsplit('-',1)[-1].strip()
        k=kind(label) if '-' in b['name'] and re.fullmatch(r'5\d+-\d+',b['code']) else None
        if k and b['code'] not in seen:
            if b['code'] not in excluded: excluded.append(b['code'])
    counts=Counter(e['kind'] for e in evidence)
    missing=not bom[code] and not ops[code]
    if missing: notes.append('Reçete/operasyon kaydı yok; var/yok belirlenemedi.')
    elif not ops[code]: notes.append('Mamul operasyon listesi yok; sayım mevcut reçete satırlarından.')
    if excluded: notes.append('Malzeme açıklamasındaki işlem adı sayılmadı; operasyon kaydı yok: '+', '.join(excluded))
    vals=['Belirsiz' if missing else ('Var' if counts['t'] else 'Yok'),None if missing else counts['t'],
          'Belirsiz' if missing else ('Var' if counts['y'] else 'Yok'),None if missing else counts['y'],'; '.join(notes)]
    result.append(dict(code=code,values=vals))
    audit[code]=evidence
summary={'rows':len(result),'tavlama_var':sum(r['values'][0]=='Var' for r in result),'yikama_var':sum(r['values'][2]=='Var' for r in result),'recete_yok':sum(r['values'][0]=='Belirsiz' for r in result),'notlu':sum(bool(r['values'][4]) for r in result)}
(out/'counts.json').write_text(json.dumps({'rows':result,'summary':summary},ensure_ascii=False),encoding='utf-8')
(out/'count_evidence.json').write_text(json.dumps(audit,ensure_ascii=False),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
print(json.dumps([r for r in result if r['code'] in ['6000006','6005510','6012095']],ensure_ascii=False))
