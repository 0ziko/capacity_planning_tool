"""Veri butunlugu: bagli kaydi olan is merkezi silinemez; yetim kayitlar acilista temizlenir."""

from sqlalchemy import text

from app.db.migrate import repair_orphans
from app.db.session import engine
from tests.test_capacity_flow import _upload


def test_workcenter_with_routing_cannot_be_deleted(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["INT-1", "Bütünlük", "E", 10, 4]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["INT-ITEM", "Bütünlük ürünü", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["INT-ITEM", 10, "Op", "INT-1", 60]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "INT-1")

    r = client.delete(f"/api/workcenters/{wc['id']}", headers=auth)
    assert r.status_code == 400
    assert "rota operasyonu" in r.json()["detail"]

    # rota hala saglam; urun ihtiyac ekrani 500 vermez
    r = client.get("/api/requirements/item", headers=auth, params={"item_code": "INT-ITEM", "quantity": 10})
    assert r.status_code == 200 and r.json()["operations"][0]["work_center_code"] == "INT-1"

    # rota silinince is merkezi silinebilir (vardiyalar cascade)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM routing_operations WHERE work_center_id = :i"), {"i": wc["id"]})
    assert client.delete(f"/api/workcenters/{wc['id']}", headers=auth).status_code == 204
    assert all(w["code"] != "INT-1" for w in client.get("/api/workcenters", headers=auth).json())


def test_orphan_repair_and_tolerance(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["ORP-1", "Yetim", "E", 10, 4]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["ORP-ITEM", "Yetim ürünü", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["ORP-ITEM", 10, "Op", "ORP-1", 60]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "ORP-1")

    # FK denetimi kapaliyken olusmus yetim rota satirini taklit et
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("DELETE FROM work_center_shifts WHERE work_center_id = :i"), {"i": wc["id"]})
        conn.execute(text("DELETE FROM work_centers WHERE id = :i"), {"i": wc["id"]})
    # servisler yetim operasyonu tolere eder (500 yok)
    r = client.get("/api/requirements/item", headers=auth, params={"item_code": "ORP-ITEM", "quantity": 1})
    assert r.status_code == 200 and r.json()["operations"][0]["work_center_code"].startswith("(silinmi")

    removed = repair_orphans(engine)
    assert removed.get("routing_operations.work_center_id") == 1
    r = client.get("/api/requirements/item", headers=auth, params={"item_code": "ORP-ITEM", "quantity": 1})
    assert r.status_code == 200 and r.json()["operations"] == []
