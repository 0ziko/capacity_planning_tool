"""Tekil siparis CRUD, plan sonucu bitis tarihleri, siparis ilerlemesi ve birlestirme."""

from datetime import date, timedelta

from tests.test_capacity_flow import _upload

WEEK = date(2026, 9, 7)  # Pazartesi


def _setup(client, auth):
    # test veritabani oturum boyunca paylasilir: onceki testlerin siparislerini temizle
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["TZG-A", "A Tezgahı", "E", 10, 4], ["MON-1", "Montaj", "E", 10, 4]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [["TZG-A", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4], ["MON-1", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 5, 4]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["MAM-1", "Ocak", "OCAK"]])
    # 36 sn kesim + 72 sn montaj => 1000 adet: 10 saat + 20 saat
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["MAM-1", 10, "Kesim", "TZG-A", 36], ["MAM-1", 20, "Montaj", "MON-1", 72]])


def test_single_order_crud(client, auth):
    _setup(client, auth)
    r = client.post("/api/orders", headers=auth, json={"order_no": "S-100", "customer": "ABC", "due_date": "2026-09-30", "item_code": "mam-1", "quantity": 1000})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["item_code"] == "MAM-1" and o["item_name"] == "Ocak" and o["status"] == "open"

    # ayni siparis no + stok tekrar eklenemez
    assert client.post("/api/orders", headers=auth, json={"order_no": "S-100", "due_date": "2026-09-30", "item_code": "MAM-1", "quantity": 5}).status_code == 400
    # bilinmeyen stok
    assert client.post("/api/orders", headers=auth, json={"order_no": "S-101", "due_date": "2026-09-30", "item_code": "YOK", "quantity": 5}).status_code == 400

    r = client.put(f"/api/orders/{o['id']}", headers=auth, json={"order_no": "S-100", "customer": "ABC Ltd", "due_date": "2026-10-05", "item_code": "MAM-1", "quantity": 1200, "note": "revize"})
    assert r.status_code == 200 and r.json()["quantity"] == 1200 and r.json()["customer"] == "ABC Ltd"

    assert client.delete(f"/api/orders/{o['id']}", headers=auth).status_code == 204
    assert client.get("/api/orders", headers=auth).json() == []


def test_schedule_progress_and_merge(client, auth):
    _setup(client, auth)
    for no, cust, due, qty in [("SP-1", "Musteri A", "2026-09-18", 1000), ("SP-2", "Musteri B", "2026-09-25", 500), ("SP-3", "Musteri C", "2026-10-02", 250)]:
        assert client.post("/api/orders", headers=auth, json={"order_no": no, "customer": cust, "due_date": due, "item_code": "MAM-1", "quantity": qty}).status_code == 201

    # plan yokken: unplanned
    sched = client.get("/api/plan/orders", headers=auth).json()
    assert len(sched) == 3 and all(s["plan_status"] == "unplanned" for s in sched)

    r = client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 6}).json()
    assert r["unplanned"] == []
    sched = client.get("/api/plan/orders", headers=auth).json()
    s1 = next(s for s in sched if s["order_no"] == "SP-1")
    assert s1["required_hours"] == 30 and s1["planned_hours"] == 30 and s1["coverage_pct"] == 100
    assert s1["planned_start"] == WEEK.isoformat()
    assert s1["planned_end"] is not None and s1["plan_status"] == "on_time"
    assert s1["lateness_days"] <= 0
    # tumu ilk haftaya sigar (52.5 saat < 200 / 100 kapasite); bitis gunu hafta icinde
    assert WEEK <= date.fromisoformat(s1["planned_end"]) <= WEEK + timedelta(days=4)

    # siparis ilerlemesi: S-1 icin 500 adet kesim (siparis no ile), 250 adet montaj (siparis no'suz -> FIFO S-1)
    _upload(client, auth, "production", ["Tarih", "İş Merkezi Kodu", "Stok Kodu", "Operasyon Sıra", "Sipariş No", "Miktar"], [[WEEK.isoformat(), "TZG-A", "MAM-1", 10, "SP-1", 500], [WEEK.isoformat(), "MON-1", "MAM-1", 20, "", 250]])
    prog = client.get("/api/progress/orders", headers=auth, params={"as_of": WEEK.isoformat()}).json()
    p1 = next(p for p in prog if p["order_no"] == "SP-1")
    assert p1["status"] == "in_progress"
    assert p1["ops"][0]["produced_qty"] == 500 and p1["ops"][0]["pct"] == 50
    assert p1["ops"][1]["produced_qty"] == 250 and p1["ops"][1]["pct"] == 25
    assert p1["produced_qty"] == 250  # son operasyondan cikan
    assert p1["earned_hours"] == 10  # 5 saat kesim + 5 saat montaj
    assert p1["last_prod_date"] == WEEK.isoformat()
    p2 = next(p for p in prog if p["order_no"] == "SP-2")
    assert p2["status"] == "not_started"
    r = client.get("/api/progress/orders.xlsx", headers=auth)
    assert r.status_code == 200 and r.content[:2] == b"PK"

    # birlestirme onerisi: 3 siparis ayni stok
    groups = client.get("/api/plan/merge-suggestions", headers=auth).json()
    assert len(groups) == 1
    g = groups[0]
    assert g["item_code"] == "MAM-1" and g["order_count"] == 3 and g["total_qty"] == 1750 and g["has_progress"] is True
    assert g["customers"] == ["Musteri A", "Musteri B", "Musteri C"]

    # S-2 + S-3 birlestir (uretimi baslamamis olanlar)
    ids = [o["id"] for o in g["orders"] if o["order_no"] in ("SP-2", "SP-3")]
    r = client.post("/api/plan/merge", headers=auth, json={"order_ids": ids})
    assert r.status_code == 201, r.text
    merged = r.json()
    assert merged["quantity"] == 750 and merged["due_date"] == "2026-09-25" and merged["customer"] == "Musteri B + Musteri C"
    assert merged["order_no"].startswith("BRL-MAM-1-")
    open_nos = sorted(o["order_no"] for o in client.get("/api/orders", headers=auth).json())
    assert open_nos == ["BRL-MAM-1-20260925", "SP-1"]
    merged_list = client.get("/api/orders", headers=auth, params={"status": "merged"}).json()
    assert {o["order_no"] for o in merged_list} == {"SP-2", "SP-3"} and all(o["merged_into_id"] == merged["id"] for o in merged_list)
    # birlesik siparis silinemez / kaynaklar duzenlenemez
    assert client.delete(f"/api/orders/{merged['id']}", headers=auth).status_code == 400
    assert client.put(f"/api/orders/{ids[0]}", headers=auth, json={"order_no": "SP-2", "due_date": "2026-09-25", "item_code": "MAM-1", "quantity": 1}).status_code == 400
    # farkli stoklar birlestirilemez / tek siparis birlestirilemez
    assert client.post("/api/plan/merge", headers=auth, json={"order_ids": [merged["id"], merged["id"]]}).status_code == 400

    # geri al
    r = client.delete(f"/api/plan/merge/{merged['id']}", headers=auth)
    assert r.status_code == 200 and r.json()["reopened"] == 2
    open_nos = sorted(o["order_no"] for o in client.get("/api/orders", headers=auth).json())
    assert open_nos == ["SP-1", "SP-2", "SP-3"]

    # plan excel'i yeni sayfayla acilir
    r = client.get(f"/api/plan/export.xlsx?start={WEEK}", headers=auth)
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_user_role_cannot_write_orders(client, auth):
    _setup(client, auth)
    client.post("/api/users", headers=auth, json={"username": "izleyici2", "password": "123456", "role": "user"})
    tok = client.post("/api/auth/login", data={"username": "izleyici2", "password": "123456"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/orders", headers=h, json={"order_no": "X", "due_date": "2026-09-30", "item_code": "MAM-1", "quantity": 1}).status_code == 403
    assert client.post("/api/plan/merge", headers=h, json={"order_ids": [1, 2]}).status_code == 403
    assert client.get("/api/plan/merge-suggestions", headers=h).status_code == 200
