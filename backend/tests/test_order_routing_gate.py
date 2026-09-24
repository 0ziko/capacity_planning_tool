"""Sipariş giriş kapısı (rota tanımı eksik ürün) ve revizyon hesabı arka plan işi."""
import time
from datetime import date, timedelta

from tests.test_capacity_flow import _plan_with_ack, _upload, _weekly_staffing, _xlsx

WEEK = date(2026, 9, 7)


def _master(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["ORG-WC", "Gate WC", "E", 10, 4]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [["ORG-WC", "G", "0,1,2,3,4", "08:00", "18:00", 10, 4]])
    _weekly_staffing(client, auth, [["ORG-WC", 10, 4, 5]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"],
            [["ORG-OK", "Rotalı", "G"], ["ORG-NOROUTE", "Rotasız", "G"], ["600901", "WIP'li mamul", "G"], ["500901-01", "WIP", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["ORG-OK", 10, "Op", "ORG-WC", 3600], ["600901", 10, "Montaj", "ORG-WC", 60]])
    # 600901 mamulü yarımamül dalına bağlı (montaj bağlantısı: reçete sıra 0) ama 500901-01 rotası yok
    _upload(client, auth, "bom", ["Stok Kodu", "Bileşen Kodu", "Bileşen Adı", "Miktar", "Kaynak Yarımamül", "Reçete Sıra"], [["600901", "500901-01", "WIP", 1, "500901-01", 0]])
    return next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "ORG-WC")["id"]


def _post_order(client, auth, no, item, qty=10):
    return client.post("/api/orders", headers=auth, json={"order_no": no, "due_date": (WEEK + timedelta(weeks=2)).isoformat(), "item_code": item, "quantity": qty})


def test_order_entry_gate_api_and_excel(client, auth, db):
    _master(client, auth)
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    assert _post_order(client, auth, "ORG-1", "ORG-OK").status_code == 201
    r = _post_order(client, auth, "ORG-2", "ORG-NOROUTE")
    assert r.status_code == 400 and "rota tanımı yok" in r.json()["detail"], r.text
    r = _post_order(client, auth, "ORG-3", "600901")
    assert r.status_code == 400 and "500901-01" in r.json()["detail"], r.text  # eksik yarımamül dalı
    # Güncelleme: aynı ürün kalırken serbest, rotasız ürüne taşıma kapıda
    o = next(x for x in client.get("/api/orders", headers=auth).json() if x["order_no"] == "ORG-1")
    ok = client.put(f"/api/orders/{o['id']}", headers=auth, json={"order_no": "ORG-1", "due_date": (WEEK + timedelta(weeks=3)).isoformat(), "item_code": "ORG-OK", "quantity": 12})
    assert ok.status_code == 200 and ok.json()["quantity"] == 12
    bad = client.put(f"/api/orders/{o['id']}", headers=auth, json={"order_no": "ORG-1", "due_date": (WEEK + timedelta(weeks=3)).isoformat(), "item_code": "ORG-NOROUTE", "quantity": 12})
    assert bad.status_code == 400
    # Excel: rotasız ve tanımsız satırlar reddedilir, rotalı satır aktarılır; stok kartı otomatik açılmaz
    hdr = ["Sipariş No", "Termin", "Stok Kodu", "Miktar"]
    rows = [["ORG-X1", "2026-10-01", "ORG-OK", 5], ["ORG-X2", "2026-10-01", "ORG-NOROUTE", 5], ["ORG-X3", "2026-10-01", "ORG-YENI", 5]]
    pv = client.post("/api/imports/orders/preview", headers=auth, files={"file": ("o.xlsx", _xlsx(hdr, rows), "application/octet-stream")}).json()
    assert pv["no_routing_item_codes"] == ["ORG-NOROUTE"] and "ORG-YENI" in pv["missing_item_codes"]
    assert len(pv["error_rows"]) == 2 and len(pv["only_in_file"]) == 1
    res = client.post("/api/imports/orders", headers=auth, files={"file": ("o.xlsx", _xlsx(hdr, rows), "application/octet-stream")}).json()
    assert res["inserted"] == 1 and len(res["errors"]) == 2
    from app.models import Item

    assert db.query(Item).filter(Item.code == "ORG-YENI").first() is None
    nos = {x["order_no"] for x in client.get("/api/orders", headers=auth).json()}
    assert "ORG-X1" in nos and "ORG-X2" not in nos and "ORG-X3" not in nos


def test_revision_calculate_job(client, auth):
    wc_id = _master(client, auth)
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    a = _post_order(client, auth, "ORG-J1", "ORG-OK", 50).json()
    assert _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "replace_existing": True}).status_code == 200
    rev = client.post("/api/plan/revisions", headers=auth, json={"reason_codes": ["vip_pull_in"], "note": "", "start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "mode": "due_date"}).json()
    client.post(f"/api/plan/revisions/{rev['id']}/changes", headers=auth, json={"entity_type": "order", "entity_id": a["id"], "field": "revised_due_date", "new_value": (WEEK + timedelta(days=4)).isoformat()})
    job = client.post(f"/api/plan/revisions/{rev['id']}/calculate/jobs", headers=auth)
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    for _ in range(60):
        j = client.get(f"/api/plan/revisions/{rev['id']}/calculate/jobs/{jid}", headers=auth).json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert j["status"] == "done", j
    assert j["result"]["status"] == "calculated" and j["result"]["compare"]["diff_summary"]["requested"] == 1
    detail = client.get(f"/api/plan/revisions/{rev['id']}", headers=auth).json()
    assert detail["status"] == "calculated" and detail["compare"]["order_diffs"][0]["order_no"] == "ORG-J1"
    assert client.get(f"/api/plan/revisions/{rev['id']}/calculate/jobs/yok", headers=auth).status_code == 404
    # onaylanmış/iptal revizyon için iş başlatılamaz
    client.post(f"/api/plan/revisions/{rev['id']}/cancel", headers=auth)
    assert client.post(f"/api/plan/revisions/{rev['id']}/calculate/jobs", headers=auth).status_code in (400, 409)
