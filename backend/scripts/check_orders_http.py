"""Authenticated read-only checks through the frontend proxy; no tokens printed."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
s=get_settings()
base='http://localhost:5173'
with urlopen(Request(base+'/api/auth/login', data=urlencode({'username':s.first_owner_username,'password':s.first_owner_password}).encode()),timeout=20) as response:
    token=json.load(response)['access_token']
def check(path):
    start=time.perf_counter()
    with urlopen(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=20) as response:
        rows=json.load(response)
    return {'path':path,'seconds':round(time.perf_counter()-start,3),'rows':len(rows)}
paths=['/api/orders?status=open','/api/orders?status=open&item_code=6010527','/api/orders?status=open&item_code=6005226']
with ThreadPoolExecutor(max_workers=3) as pool:
    print(json.dumps(list(pool.map(check,paths))))
