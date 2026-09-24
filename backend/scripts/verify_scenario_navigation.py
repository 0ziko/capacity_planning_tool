"""Read-only live verification; credentials and tokens are never printed."""
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings

s = get_settings()
base = 'http://127.0.0.1:8000'
body = urlencode({'username': s.first_owner_username, 'password': s.first_owner_password}).encode()
with urlopen(Request(base+'/api/auth/login', data=body), timeout=20) as response:
    token = json.load(response)['access_token']

def get(path, **params):
    req = Request(base+path+('?' + urlencode(params) if params else ''),
                  headers={'Authorization': 'Bearer '+token})
    with urlopen(req, timeout=60) as response:
        return json.load(response)

groups = get('/api/scenarios/groups')
gn = next(g for g in groups if g['product_group']=='GN')
rules = get('/api/scenarios/rules', product_group='GN')
flow = get('/api/scenarios/flow', product_group='GN', item_code='6005510')
assert gn['rule_count']==len(rules)==8
assert any(i['code']=='6005510' and i['item_rules']==3 for i in flow['items'])
assert sorted(r['wait_minutes'] for r in rules if r['item_code']=='6005510')==[15,15,30]
snapshot = json.loads((Path(__file__).resolve().parents[2]/'outputs/operasyonel-alt-gruplar-20260922/current_rules.json').read_text(encoding='utf-8'))
actual = [[r['id'],r['scope'],r['product_group'],r['item_code'] or '',r['from_op'],r['to_op'],r['from_wip_code'],r['to_wip_code'],r['rule'],r['lag_cycles'],r['wait_minutes'],r['note']] for r in rules]
assert sorted(actual)==sorted(snapshot), 'Existing rules changed'
print(json.dumps({'group':gn,'item':'6005510','item_rules':3,'saved_rules_unchanged':True,
                  'visible_transitions':len(flow['transitions'])},ensure_ascii=True))
