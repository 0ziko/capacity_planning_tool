"""Demo verisi yukler (calisan API uzerinden, Excel import akisiyla).

Kullanim:  .\\.venv\\Scripts\\python.exe seed_demo.py [http://127.0.0.1:8000]
Gercek pilot verisi icin bu scripti degil, arayuzdeki Excel Import ekranini kullanin.
"""

import datetime as dt
import io
import sys

import httpx
from openpyxl import Workbook

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
tok = httpx.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "admin123"}).json()["access_token"]
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
    r = httpx.post(f"{BASE}/api/imports/{kind}", headers=H, files={"file": (f"{kind}.xlsx", xlsx(header, rows))}, timeout=60)
    print(kind, r.status_code, r.json())


today = dt.date.today()
monday = today - dt.timedelta(days=today.weekday())
d = lambda n: (monday + dt.timedelta(days=n)).isoformat()  # noqa: E731

up("workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
   [["TZG-A", "A Tezgahı (Lazer Kesim)", "E", 10, 4], ["TZG-B", "B Tezgahı (Abkant)", "E", 10, 5], ["KAY-1", "Kaynak Hattı", "E", 10, 4.5], ["MON-1", "Montaj", "H", 10, 5]])
up("shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
   [["TZG-A", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4], ["TZG-B", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 4, 5],
    ["KAY-1", "Gündüz", "0,1,2,3,4,5", "08:00", "18:00", 6, 4.5], ["MON-1", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 8, 5]])

assignments = ["TZG-A"] * 10 + ["TZG-B"] * 4 + ["KAY-1"] * 6 + ["MON-1"] * 8
up("employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu", "Aktif (E/H)"],
   [[str(1001 + i), f"Personel {i + 1}", wc, "E"] for i, wc in enumerate(assignments)])

up("items", ["Stok Kodu", "Stok Adı", "Ürün Grubu", "Birim"],
   [["MAM-OCAK-4", "Endüstriyel Ocak 4 Gözlü", "OCAK", "AD"], ["MAM-TEZ-180", "Çalışma Tezgahı 180cm", "TEZGAH", "AD"], ["MAM-DVL-2", "Davlumbaz 2m", "DAVLUMBAZ", "AD"]])
up("bom", ["Stok Kodu", "Bileşen Kodu", "Bileşen Adı", "Miktar", "Birim"],
   [["MAM-OCAK-4", "HM-SAC-2MM", "Paslanmaz Sac 2mm", 6.5, "KG"], ["MAM-OCAK-4", "HM-BEK", "Bek Grubu", 4, "AD"],
    ["MAM-TEZ-180", "HM-SAC-1MM", "Paslanmaz Sac 1mm", 9, "KG"], ["MAM-DVL-2", "HM-SAC-1MM", "Paslanmaz Sac 1mm", 12, "KG"]])
up("routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Setup (dk)"],
   [["MAM-OCAK-4", 10, "Lazer kesim", "TZG-A", 900, 20], ["MAM-OCAK-4", 20, "Bükme", "TZG-B", 1200, 15], ["MAM-OCAK-4", 30, "Kaynak", "KAY-1", 2400, 10], ["MAM-OCAK-4", 40, "Montaj", "MON-1", 3600, 0],
    ["MAM-TEZ-180", 10, "Lazer kesim", "TZG-A", 600, 15], ["MAM-TEZ-180", 20, "Bükme", "TZG-B", 900, 15], ["MAM-TEZ-180", 30, "Kaynak", "KAY-1", 1500, 10],
    ["MAM-DVL-2", 10, "Lazer kesim", "TZG-A", 1200, 15], ["MAM-DVL-2", 20, "Bükme", "TZG-B", 1800, 15], ["MAM-DVL-2", 30, "Kaynak", "KAY-1", 2700, 10]])
up("orders", ["Sipariş No", "Müşteri", "Termin", "Stok Kodu", "Miktar"],
   [["SIP-1001", "ABC Otel", d(18), "MAM-OCAK-4", 120], ["SIP-1002", "XYZ Restoran", d(25), "MAM-TEZ-180", 300],
    ["SIP-1003", "Deniz Catering", d(32), "MAM-DVL-2", 90], ["SIP-1004", "ABC Otel", d(40), "MAM-OCAK-4", 200]])

r = httpx.post(f"{BASE}/api/plan/auto", headers=H, json={"start_week": monday.isoformat(), "weeks": 10})
print("auto plan", r.status_code, r.json())

up("production", ["Tarih", "İş Merkezi Kodu", "Stok Kodu", "Operasyon Sıra", "Sipariş No", "Miktar"],
   [[d(0), "TZG-A", "MAM-OCAK-4", 10, "SIP-1001", 150], [d(0), "TZG-B", "MAM-OCAK-4", 20, "SIP-1001", 50], [d(0), "KAY-1", "MAM-OCAK-4", 30, "SIP-1001", 30]])
up("downtime", ["Tarih", "İş Merkezi Kodu", "Sebep Kodu", "Sebep", "Süre (dk)"],
   [[d(0), "TZG-A", "MLZ", "Malzeme bekleme", 2400], [d(0), "TZG-A", "SET", "Setup", 900], [d(0), "TZG-A", "ARZ", "Arıza", 500]])
print("done")
