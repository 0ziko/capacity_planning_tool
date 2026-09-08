"""Owner paneli testleri."""

from datetime import date

from tests.test_capacity_flow import _upload
from tests.test_plan_preflight import _seed_daily_imports

WEEK = date(2026, 9, 8)


def _owner_auth(client):
    r = client.post("/api/auth/login", data={"username": "owner", "password": "owner123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_owner_panel_requires_owner(client, auth):
    assert client.get("/api/owner/stats", headers=auth).status_code == 403
    oh = _owner_auth(client)
    assert client.get("/api/owner/stats", headers=oh).status_code == 200


def test_admin_cannot_assign_owner(client, auth):
    r = client.post("/api/users", headers=auth, json={"username": "xowner", "full_name": "X", "password": "secret12", "role": "owner"})
    assert r.status_code == 403


def test_owner_records_and_select_purge(client, auth):
    oh = _owner_auth(client)
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["OD-ITEM", "Test", "G"]])
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["OD-R1", "2026-11-01", "OD-ITEM", 1], ["OD-R2", "2026-11-02", "OD-ITEM", 2]])

    rec = client.get("/api/owner/records", headers=oh, params={"target": "orders", "limit": 50}).json()
    assert rec["filtered_count"] >= 2
    ids = [r["id"] for r in rec["records"][:1]]
    r = client.post("/api/owner/purge", headers=oh, json={"target": "orders", "confirm": "SIL", "ids": ids})
    assert r.status_code == 200
    assert r.json()["deleted"] == 1


def test_preflight_daily_data_in_preflight(client, auth):
    oh = _owner_auth(client)
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    client.post("/api/owner/purge", headers=oh, json={"target": "production", "confirm": "SIL", "delete_all": True})
    client.post("/api/owner/purge", headers=oh, json={"target": "stock_receipts", "confirm": "SIL", "delete_all": True})
    client.post("/api/owner/purge", headers=oh, json={"target": "import_logs", "confirm": "SIL", "delete_all": True})
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["PF-W", "WC", "E", 10, 4]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "PF-W")
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [["PFW1", "Ali", "PF-W"]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [["PF-W", "G", "0,1,2,3,4", "08:00", "18:00", 1, 4]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["PF-WIP", "M", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["PF-WIP", 10, "Op", "PF-W", 360]])
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["PF-W1", "2026-10-01", "PF-WIP", 1]])

    body = {"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc["id"]], "mode": "due_date"}
    pf = client.post("/api/plan/auto/preflight", headers=auth, json=body).json()
    assert pf["can_plan"] is True
    assert pf["needs_daily_data_ack"] is True
    assert any(c["key"] == "production_output" and c["status"] != "ok" for c in pf["daily_data"])

    _seed_daily_imports(client, auth, "PF-W", "PF-WIP", "PF-W1")
    pf2 = client.post("/api/plan/auto/preflight", headers=auth, json=body).json()
    assert pf2["needs_daily_data_ack"] is False
