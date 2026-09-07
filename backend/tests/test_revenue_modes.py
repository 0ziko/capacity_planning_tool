"""Birim fiyat, ciro raporu, termin / maksimum ciro planlama modlari ve karsilastirma."""

from datetime import date

from tests.test_capacity_flow import _upload

WEEK = date(2026, 9, 7)  # Pazartesi


def _setup(client, auth):
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    # tek is merkezi, 10 kisi x 4 saat x 5 gun = 200 saat/hafta; ufuk 1 hafta
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["CIRO-1", "Ciro Hattı", "E", 10, 4]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [["CIRO-1", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4]])
    # 3600 sn/adet => 1 saat/adet
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["UCUZ", "Ucuz ürün", "G"], ["PAHALI", "Pahalı ürün", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["UCUZ", 10, "Op", "CIRO-1", 3600], ["PAHALI", 10, "Op", "CIRO-1", 3600]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "CIRO-1")
    # diger testlerin is merkezlerini plan disina al (yalnizca CIRO-1 uzerinden hesaplansin)
    return wc["id"]


def test_unit_price_import_and_form(client, auth):
    _setup(client, auth)
    _upload(client, auth, "orders", ["Sipariş No", "Müşteri", "Termin", "Stok Kodu", "Miktar", "Birim Fiyat"], [["F-1", "A", "2026-09-11", "UCUZ", 10, 250]])
    o = next(x for x in client.get("/api/orders", headers=auth).json() if x["order_no"] == "F-1")
    assert o["unit_price"] == 250 and o["revenue"] == 2500
    r = client.post("/api/orders", headers=auth, json={"order_no": "F-2", "due_date": "2026-09-11", "item_code": "PAHALI", "quantity": 4, "unit_price": 1000})
    assert r.status_code == 201 and r.json()["revenue"] == 4000
    r = client.put(f"/api/orders/{r.json()['id']}", headers=auth, json={"order_no": "F-2", "due_date": "2026-09-11", "item_code": "PAHALI", "quantity": 4, "unit_price": 1500})
    assert r.json()["revenue"] == 6000


def test_modes_and_compare(client, auth):
    wc_id = _setup(client, auth)
    # Kapasite 200 saat. Siparisler (saat = adet):
    #  E-1 UCUZ  120 adet, termin 11.09, fiyat 10  -> 1200 ciro, 10/saat
    #  E-2 PAHALI 100 adet, termin 18.09, fiyat 100 -> 10000 ciro, 100/saat
    #  E-3 PAHALI  60 adet, termin 25.09, fiyat 100 -> 6000 ciro, 100/saat
    for no, due, item, qty, price in [("E-1", "2026-09-11", "UCUZ", 120, 10), ("E-2", "2026-09-18", "PAHALI", 100, 100), ("E-3", "2026-09-25", "PAHALI", 60, 100)]:
        assert client.post("/api/orders", headers=auth, json={"order_no": no, "due_date": due, "item_code": item, "quantity": qty, "unit_price": price}).status_code == 201

    body = {"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc_id]}
    cmp_ = client.post("/api/plan/compare", headers=auth, json=body)
    assert cmp_.status_code == 200, cmp_.text
    c = cmp_.json()

    # Termin modu: E-1 (120) + E-2'nin 80 saati -> E-1 tamam, E-2 kismi, E-3 planlanmadi
    due = {o["order_no"]: o for o in c["due"]["orders"]}
    assert due["E-1"]["plan_status"] == "on_time"
    assert due["E-2"]["plan_status"] == "partial"
    assert due["E-3"]["plan_status"] == "unplanned"
    assert c["due"]["planned_revenue"] == 1200

    # Ciro modu: yogunluk sirasi E-2, E-3 (100/saat) sonra E-1 (10/saat).
    # E-2 (100) + E-3 (60) = 160 saat sigar; E-1 (120) tamamen sigmaz -> atlanir, kalan 40 saat kismen E-1
    rev = {o["order_no"]: o for o in c["revenue"]["orders"]}
    assert rev["E-2"]["plan_status"] == "on_time" and rev["E-3"]["plan_status"] == "on_time"
    assert rev["E-1"]["plan_status"] == "partial" and rev["E-1"]["planned_hours"] == 40
    assert c["revenue"]["planned_revenue"] == 16000
    assert c["revenue"]["utilization_pct"] == 100.0 and c["due"]["utilization_pct"] == 100.0

    # karsilastirma listeleri
    assert c["rev_drops"] == ["E-1"]  # termin planinda tamamlanan, ciro planinda disarida
    assert sorted(c["due_drops"]) == ["E-2", "E-3"]  # ciro planinda tamamlanan, termin planinda degil
    assert c["rev_misses_due"] == []
    diffs = {r["order_no"]: r["diff"] for r in c["rows"]}
    assert diffs == {"E-1": "rev_drops", "E-2": "due_drops", "E-3": "due_drops"}

    # ciro planini uygula ve haftalik/aylik ciro raporunu al
    r = client.post("/api/plan/auto", headers=auth, json={**body, "mode": "revenue"}).json()
    assert r["mode"] == "revenue" and r["created"] >= 3
    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json()
    assert all(l["strategy"] == "revenue" for l in lines)
    rep = client.get("/api/plan/revenue", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc_id]}).json()
    assert rep["planned_revenue"] == 16000 and rep["partial_revenue"] == 1200 and rep["total_open_revenue"] == 17200
    wk = rep["weeks"][0]
    assert wk["period"] == WEEK.isoformat() and wk["completed_revenue"] == 16000 and wk["completed_orders"] == 2
    # oransal: 10000 + 6000 + 1200*(40/120)=400 => 16400
    assert wk["earned_revenue"] == 16400
    assert rep["months"][0]["period"] == "2026-09" and rep["months"][0]["completed_revenue"] == 16000

    # termin planini uygula: ciro dusuk ama E-1 zamaninda
    r = client.post("/api/plan/auto", headers=auth, json={**body, "mode": "due_date"}).json()
    assert r["mode"] == "due_date"
    rep = client.get("/api/plan/revenue", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc_id]}).json()
    assert rep["planned_revenue"] == 1200

    # plan excel'i ciro sayfalariyla aciliyor
    r = client.get(f"/api/plan/export.xlsx?start={WEEK}&weeks=1&work_center_ids={wc_id}", headers=auth)
    assert r.status_code == 200 and r.content[:2] == b"PK"
