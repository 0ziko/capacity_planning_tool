"""Spec'teki ornek: 10 kisi x 4 saat x 5 gun = 200 saat = 20 birim (birim = 10 saat)."""

import io
from datetime import date, timedelta

from openpyxl import Workbook


def _xlsx(header, rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, auth, kind, header, rows):
    r = client.post(f"/api/imports/{kind}", headers=auth, files={"file": (f"{kind}.xlsx", _xlsx(header, rows), "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == [], body
    return body


WEEK = date(2026, 9, 7)  # Pazartesi


def test_full_flow(client, auth):
    # is merkezi + vardiya (10 kisi, 4 saat verimli, Pzt-Cum 08-18)
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["TZG-A", "A Tezgahı", "E", 10, 4]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [["TZG-A", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4]])

    wcs = client.get("/api/workcenters", headers=auth).json()
    assert len(wcs) == 1
    wc_id = wcs[0]["id"]

    cap = client.get("/api/capacity", headers=auth, params={"start": WEEK.isoformat()}).json()
    assert cap[0]["capacity_hours"] == 200
    assert cap[0]["capacity_units"] == 20
    assert len(cap[0]["days"]) == 5

    # stok + rota (50 sn/adet) + siparis (10000 adet => 138.9 saat)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["MAM-1", "Ocak", "OCAK"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["MAM-1", 10, "Kesim", "TZG-A", 50]])
    _upload(client, auth, "orders", ["Sipariş No", "Müşteri", "Termin", "Stok Kodu", "Miktar"], [["S-1", "ABC", "2026-09-30", "MAM-1", 10000], ["S-2", "XYZ", "2026-10-15", "MAM-1", 10000]])

    req = client.post("/api/requirements", headers=auth, json={}).json()
    assert round(req["total_hours"], 1) == round(20000 * 50 / 3600, 1)

    # otomatik plan: 277.8 saat -> 1. hafta 200, 2. hafta 77.8
    r = client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 4}).json()
    assert r["created"] >= 2 and r["unplanned"] == []
    load = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc_id]}).json()
    weeks = load[0]["weeks"]
    assert weeks[0]["planned_hours"] == 200 and weeks[0]["utilization"] == 1.0
    assert round(weeks[1]["planned_hours"], 1) == round(20000 * 50 / 3600 - 200, 1)

    # gunluk uretim: pazartesi 2880 adet => 40 saat (200/5 = 40 beklenen)
    _upload(client, auth, "production", ["Tarih", "İş Merkezi Kodu", "Stok Kodu", "Operasyon Sıra", "Sipariş No", "Miktar"], [[WEEK.isoformat(), "TZG-A", "MAM-1", 10, "S-1", 2880]])
    prog = client.get("/api/progress", headers=auth, params={"week": WEEK.isoformat(), "as_of": (WEEK + timedelta(days=1)).isoformat()}).json()[0]
    assert prog["planned_hours"] == 200
    assert prog["expected_hours_to_date"] == 40
    assert prog["actual_hours_to_date"] == 40
    assert prog["status"] == "on_track"
    assert prog["remaining_hours"] == 160 and prog["remaining_days"] == 4

    # durus: beklenen (10-4)*10*60 = 3600 dk/gun; 3700 dk gerceklesen => 100 dk fazla
    _upload(client, auth, "downtime", ["Tarih", "İş Merkezi Kodu", "Sebep Kodu", "Sebep", "Süre (dk)"], [[WEEK.isoformat(), "TZG-A", "MLZ", "Malzeme bekleme", 3000], [WEEK.isoformat(), "TZG-A", "SET", "Setup", 700]])
    dt = client.get("/api/analysis/downtime", headers=auth, params={"start": WEEK.isoformat(), "end": WEEK.isoformat()}).json()
    assert dt["totals"][0]["excess_minutes"] == 100
    assert dt["reasons"][0]["reason_code"] == "MLZ"

    # terminleme
    lt = client.post("/api/plan/leadtime", headers=auth, json={"item_code": "MAM-1", "quantity": 720, "start": (WEEK + timedelta(weeks=3)).isoformat()}).json()
    assert lt["total_hours"] == 10 and len(lt["steps"]) == 1

    # yedek + rapor + sablon
    for url in ("/api/backup.xlsx", "/api/imports/template/orders", f"/api/analysis/downtime.xlsx?start={WEEK}&end={WEEK}", "/api/analysis/cycletime.xlsx", f"/api/plan/export.xlsx?start={WEEK}"):
        r = client.get(url, headers=auth)
        assert r.status_code == 200 and r.content[:2] == b"PK", url


def test_all_templates_download(client, auth):
    kinds = [k["kind"] for k in client.get("/api/imports/kinds", headers=auth).json()]
    assert "routing" in kinds
    for kind in kinds:
        r = client.get(f"/api/imports/template/{kind}", headers=auth)
        assert r.status_code == 200 and r.content[:2] == b"PK", kind


def test_roles(client, auth):
    r = client.post("/api/users", headers=auth, json={"username": "izleyici", "password": "123456", "role": "user"})
    assert r.status_code == 201
    tok = client.post("/api/auth/login", data={"username": "izleyici", "password": "123456"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/workcenters", headers=h).status_code == 200
    assert client.post("/api/workcenters", headers=h, json={"code": "X", "name": "X"}).status_code == 403
    assert client.get("/api/users", headers=h).status_code == 403
