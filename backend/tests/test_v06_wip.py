"""v0.6: yarimamul kodu — rota, senaryo matrisi, gunluk uretim import."""

from datetime import date, timedelta

from tests.test_capacity_flow import _upload


def _monday(offset_weeks: int = 0) -> str:
    d = date.today()
    return (d - timedelta(days=d.weekday()) + timedelta(weeks=offset_weeks)).isoformat()


def test_routing_wip_import_and_display(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"], [["PRS3", "PRESHANE 3", "E", 8]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["6005510", "GN Kuvet", "GN"]])
    r = _upload(
        client, auth, "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Setup (dk)", "Yarımamül Kodu"],
        [
            ["6005510", 10, "SIVAMA", "PRS3", 50, 15, "6005510-10"],
            ["6005510", 11, "FORMA", "PRS3", 60, 20, "6005510-11"],
            ["6005510", 12, "ETEK KESME", "PRS3", 40, 15, "6005510-12"],
        ],
    )
    assert not r["errors"], r["errors"]
    items = client.get("/api/items", headers=auth, params={"q": "6005510"}).json()
    detail = client.get(f"/api/items/{items[0]['id']}", headers=auth).json()
    wips = [op["semi_finished_code"] for op in detail["operations"]]
    assert wips == ["6005510-10", "6005510-11", "6005510-12"]


def test_scenario_rule_by_wip_code(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)"], [["PRS3", "PRESHANE 3", "E"]])
    _upload(client, auth, "items", ["Stok Kodu", "Ürün Grubu"], [["6005510", "GN"]])
    _upload(
        client, auth, "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"],
        [["6005510", 10, "SIVAMA", "PRS3", 50, "6005510-10"], ["6005510", 11, "FORMA", "PRS3", 60, "6005510-11"]],
    )
    r = client.put(
        "/api/scenarios/rules", headers=auth,
        json={
            "scope": "group", "product_group": "GN", "from_op": "SIVAMA", "to_op": "FORMA",
            "from_wip_code": "6005510-10", "to_wip_code": "6005510-11",
            "rule": "cycles", "lag_cycles": 5, "wait_minutes": 0, "note": "5 cevrim",
        },
    )
    assert r.status_code == 200, r.text
    flow = client.get("/api/scenarios/flow", headers=auth, params={"product_group": "GN", "item_code": "6005510"}).json()
    t = flow["transitions"][0]
    assert t["from_wip_code"] == "6005510-10" and t["to_wip_code"] == "6005510-11"
    assert t["effective"]["rule"] == "cycles" and t["effective"]["lag_cycles"] == 5


def test_production_import_by_wip_updates_progress(client, auth):
    wk = _monday()
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"], [["PRS3", "PRESHANE 3", "E", 8]])
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [["P1", "A", "PRS3"]])
    _upload(client, auth, "items", ["Stok Kodu", "Ürün Grubu"], [["6005510", "GN"]])
    _upload(
        client, auth, "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["6005510", 10, "SIVAMA", "PRS3", 36], ["6005510", 11, "FORMA", "PRS3", 36]],
    )
    # yarimamul kodlarini sonradan ekle
    _upload(
        client, auth, "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"],
        [["6005510", 10, "SIVAMA", "PRS3", 36, "6005510-10"], ["6005510", 11, "FORMA", "PRS3", 36, "6005510-11"]],
    )
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["S-GN-1", "2026-12-01", "6005510", 100]])
    prod_day = wk  # hafta ici uretim
    r = _upload(
        client, auth, "production",
        ["Tarih", "Yarımamül Kodu", "Miktar", "Sipariş No"],
        [[prod_day, "6005510-10", 40, "S-GN-1"], [prod_day, "6005510-11", 20, "S-GN-1"]],
    )
    assert not r["errors"], r["errors"]

    prog = client.get("/api/progress/orders", headers=auth).json()
    row = next(x for x in prog if x["order_no"] == "S-GN-1")
    assert row["ops"][0]["produced_qty"] == 40
    assert row["ops"][1]["produced_qty"] == 20

    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "PRS3")
    from datetime import timedelta
    as_of = (date.fromisoformat(wk) + timedelta(days=1)).isoformat()
    pr = client.get("/api/progress", headers=auth, params={"week": wk, "as_of": as_of, "work_center_ids": [wc["id"]]}).json()
    assert pr[0]["actual_hours_to_date"] > 0
