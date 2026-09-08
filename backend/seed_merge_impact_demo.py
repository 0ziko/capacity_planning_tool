"""Birlestirme etki analizi demo — gercek stok 6005510, PRESHANE 3 darbogazi.

Kullanim (backend calisirken):
  .\\.venv\\Scripts\\python.exe seed_merge_impact_demo.py [http://127.0.0.1:8000]

Planlama ekrani:
  - Is merkezi: PRESHANE 3
  - Uretim partisi, stok filtresi 6005510 veya siparis SIM-5510
  - Tolerans 10 gun, «Etki analizi»
"""

import datetime as dt
import io
import sys

import httpx
from openpyxl import Workbook

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ITEM = "6005510"
WC_CODE = "PRESHANE 3"
ORDER_PREFIX = "SIM-5510"

try:
    tok = httpx.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "admin123"}, timeout=15).json()["access_token"]
except Exception as e:
    print(f"Giris basarisiz ({BASE}): {e}")
    sys.exit(1)

H = {"Authorization": f"Bearer {tok}"}


def xlsx(header, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def up(kind, header, rows):
    r = httpx.post(f"{BASE}/api/imports/{kind}", headers=H, files={"file": (f"{kind}.xlsx", xlsx(header, rows))}, timeout=120)
    print(kind, r.status_code, r.text[:200] if r.status_code >= 400 else r.json())
    if r.status_code >= 400:
        sys.exit(1)


today = dt.date.today()
monday = today - dt.timedelta(days=today.weekday())
d = lambda n: (monday + dt.timedelta(days=n)).isoformat()  # noqa: E731

items = httpx.get(f"{BASE}/api/items", headers=H, params={"q": ITEM}, timeout=30).json()
if not any(i.get("code") == ITEM for i in items):
    print(f"HATA: {ITEM} stok karti yok. Once Excel import ile tanimlayin.")
    sys.exit(1)

wcs = httpx.get(f"{BASE}/api/workcenters", headers=H, timeout=30).json()
wc = next((w for w in wcs if w["code"] == WC_CODE), None)
if not wc:
    print(f"HATA: {WC_CODE} is merkezi bulunamadi.")
    sys.exit(1)

print(f"Demo birlestirme senaryosu: {ITEM} @ {WC_CODE} (hafta {monday})")

# 10 siparis — PRESHANE 3'te kapasite catismasi; birlestirme adaylari: 01+02, 05+06, 09+10
orders = [
    [f"{ORDER_PREFIX}-01", "Sim Musteri A", d(5), ITEM, 180],
    [f"{ORDER_PREFIX}-02", "Sim Musteri A", d(8), ITEM, 220],
    [f"{ORDER_PREFIX}-03", "Sim Musteri B", d(7), ITEM, 280],
    [f"{ORDER_PREFIX}-04", "Sim Musteri C", d(11), ITEM, 260],
    [f"{ORDER_PREFIX}-05", "Sim Musteri D", d(3), ITEM, 150],
    [f"{ORDER_PREFIX}-06", "Sim Musteri D", d(6), ITEM, 170],
    [f"{ORDER_PREFIX}-07", "Sim Musteri E", d(18), ITEM, 320],
    [f"{ORDER_PREFIX}-08", "Sim Musteri F", d(14), ITEM, 240],
    [f"{ORDER_PREFIX}-09", "Sim Musteri G", d(4), ITEM, 160],
    [f"{ORDER_PREFIX}-10", "Sim Musteri G", d(9), ITEM, 190],
]
up("orders", ["Sipariş No", "Müşteri", "Termin", "Stok Kodu", "Miktar"], orders)

r = httpx.post(
    f"{BASE}/api/plan/auto",
    headers=H,
    json={"start_week": monday.isoformat(), "weeks": 10, "work_center_ids": [wc["id"]], "replace_existing": True, "mode": "due_date"},
    timeout=180,
)
print("auto plan", r.status_code, r.json() if r.status_code == 200 else r.text[:300])

ords = httpx.get(f"{BASE}/api/orders", headers=H, params={"status": "open", "order_no": ORDER_PREFIX}, timeout=30).json()
by_no = {o["order_no"]: o["id"] for o in ords if o["order_no"].startswith(ORDER_PREFIX)}

# Tek grup
if f"{ORDER_PREFIX}-01" in by_no and f"{ORDER_PREFIX}-02" in by_no:
    impact1 = httpx.post(
        f"{BASE}/api/plan/merge/impact",
        headers=H,
        json={
            "merge_groups": [{"order_ids": [by_no[f"{ORDER_PREFIX}-01"], by_no[f"{ORDER_PREFIX}-02"]]}],
            "start_week": monday.isoformat(),
            "weeks": 10,
            "work_center_ids": [wc["id"]],
            "mode": "due_date",
        },
        timeout=180,
    )
    if impact1.status_code == 200:
        j = impact1.json()
        print(f"Onizleme {ORDER_PREFIX}-01+02: {j['delayed_count']} kayma, {len(j['load_deltas'])} yuk farki")
        for row in j["delayed_orders"][:8]:
            print(f"  {row['order_no']}: {row['before_end']} -> {row['after_end']} (+{row['delay_days']} gun)")

# Coklu grup (ekranda gorulen senaryo)
groups = []
for a, b in [("01", "02"), ("05", "06"), ("09", "10")]:
    if f"{ORDER_PREFIX}-{a}" in by_no and f"{ORDER_PREFIX}-{b}" in by_no:
        groups.append({"order_ids": [by_no[f"{ORDER_PREFIX}-{a}"], by_no[f"{ORDER_PREFIX}-{b}"]]})
if len(groups) >= 2:
    impact2 = httpx.post(
        f"{BASE}/api/plan/merge/impact",
        headers=H,
        json={"merge_groups": groups, "start_week": monday.isoformat(), "weeks": 10, "work_center_ids": [wc["id"]], "mode": "due_date"},
        timeout=180,
    )
    if impact2.status_code == 200:
        j = impact2.json()
        print(f"Onizleme 3 grup (6 sip.): {j['delayed_count']} kayma, {len(j['load_deltas'])} yuk farki")
        for row in j["delayed_orders"][:8]:
            print(f"  {row['order_no']}: {row['before_end']} -> {row['after_end']} (+{row['delay_days']} gun)")

print()
print(f"Tamamlandi. Planlama > filtre {WC_CODE} > Uretim partisi > {ORDER_PREFIX}-* / {ITEM}")
