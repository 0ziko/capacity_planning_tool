"""Count exactly the operations displayed for each FG, never material text."""
import json,re
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.orm import selectinload
from app.db.session import SessionLocal
from app.models import Item,norm_op
from app.services import bom_tree
from app.services.excel import read_rows,_str

out=Path('../outputs/stok_recete_20260921')
rows,errs=read_rows(Path('C:/Users/ozan.deniz/Downloads/6_ile_baslayan_stok_kodlari.xlsx').read_bytes(),'items')
assert not errs
result=[]; audit={}; names=Counter()
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
    fgs={i.code:i for i in db.query(Item).options(selectinload(Item.operations),selectinload(Item.bom_lines)).filter(Item.code.like('6%'))}
    assert set(fgs)=={_str(r['code']) for r in rows}
    # Batch the FG detail renderer's lookup; no recursive recipe traversal.
    links={b.component_code.strip().upper() for i in fgs.values() for b in i.bom_lines
           if bom_tree.is_wip_asm_link(b.component_code,b.source_wip,b.recipe_seq)}
    cached={i.code.upper():i for i in db.query(Item).options(selectinload(Item.operations)).filter(Item.code.in_(links))}
    flat_by={}
    with patch.object(bom_tree,'_load_wip_items',lambda session,codes:{c.upper():cached[c.upper()] for c in codes if c.upper() in cached}):
        for code,fg in fgs.items(): flat_by[code]=bom_tree.flatten_fg_operations(db,fg)
    # Compare representative outputs with the unmodified application loader.
    for code in ['6012282','6012289','6000006','6005510','6012095','6007172','6012265']:
        actual=bom_tree.flatten_fg_operations(db,fgs[code])
        assert [(x.operation.id,x.display_seq,x.wip_code) for x in actual]==[(x.operation.id,x.display_seq,x.wip_code) for x in flat_by[code]]
    for r in rows:
        code=_str(r['code']); flat=flat_by[code]; evidence=[]
        for f in flat:
            op=f.operation; name=norm_op(op.operation_name)
            for token,k in [('tavlama','t'),('yikama','y')]:
                if re.search(r'\b'+token+r'\b',name):
                    evidence.append(dict(kind=k,label=op.operation_name,id=op.id,display_seq=f.display_seq,output=op.semi_finished_code))
                    names[op.operation_name]+=1
        counts=Counter(e['kind'] for e in evidence)
        note='Operasyon Detayları listesi boş; var/yok belirlenemedi.' if not flat else ''
        audit[code]={'displayed_operations':len(flat),'matches':evidence}
        result.append({'code':code,'values':['Var' if counts['t'] else ('Yok' if flat else 'Belirsiz'),counts['t'] if flat else None,
                                            'Var' if counts['y'] else ('Yok' if flat else 'Belirsiz'),counts['y'] if flat else None,note]})
    db.rollback()
summary={'rows':len(result),'tavlama_var':sum(r['values'][0]=='Var' for r in result),'yikama_var':sum(r['values'][2]=='Var' for r in result),'operasyon_yok':sum(r['values'][0]=='Belirsiz' for r in result)}
old={r['code']:r['values'] for r in json.loads((out/'counts_before_correction.json').read_text(encoding='utf-8'))['rows']}
diff=[{'code':r['code'],'before':old[r['code']][:4],'after':r['values'][:4]} for r in result if old[r['code']][:4]!=r['values'][:4]]
assert next(r for r in result if r['code']=='6012282')['values'][2:4]==['Yok',0]
assert next(r for r in result if r['code']=='6012289')['values'][2:4]==['Yok',0]
(out/'counts.json').write_text(json.dumps({'rows':result,'summary':summary},ensure_ascii=False),encoding='utf-8')
(out/'count_evidence.json').write_text(json.dumps(audit,ensure_ascii=False),encoding='utf-8')
(out/'corrections.json').write_text(json.dumps(diff,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'summary':summary,'changed':len(diff),'changes':diff[:20],'operation_names':dict(names)},ensure_ascii=False))
