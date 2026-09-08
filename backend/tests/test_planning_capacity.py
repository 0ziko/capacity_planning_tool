"""Planlama: atil kapasite, tahmin terminleme kapasite siniri, birlestirme toleransi."""

from datetime import date, timedelta

from tests.test_capacity_flow import WEEK, _upload


def _wc_setup(client, auth, code: str, reserve_pct: float = 0.0):
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [[code, f"{code} IM", "E", 10, 8]],
    )
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == code)
    if reserve_pct:
        body = {k: v for k, v in wc.items() if k not in ("shifts", "machines", "employee_count", "machine_employee_count", "capacity_headcount")}
        body["planning_reserve_pct"] = reserve_pct
        client.put(f"/api/workcenters/{wc['id']}", headers=auth, json=body)
        wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == code)
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [[f"{code}-1", "A", code]])
    _upload(
        client,
        auth,
        "shifts",
        ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
        [[code, "Gündüz", "0,1,2,3,4", "08:00", "18:00", 1, 8]],
    )
    return wc


def test_planning_reserve_pct_limits_utilization(client, auth):
    wc = _wc_setup(client, auth, "RSV-1", reserve_pct=10)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["RSV-M", "Rezerve", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["RSV-M", 10, "Op", "RSV-1", 288]])  # 8 sa/adet
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["RSV-O1", "2026-12-01", "RSV-M", 5]])  # 40 sa
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc["id"]]})
    load = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc["id"]]}).json()
    w0 = load[0]["weeks"][0]
    assert w0["capacity_hours"] == 40
    assert w0["planned_hours"] <= 36.01
    assert w0["utilization"] <= 1.001


def test_forecast_respects_capacity(client, auth):
    wc = _wc_setup(client, auth, "FC-1")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["FC-M", "Forecast", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["FC-M", 10, "Op", "FC-1", 360]])  # 10 sa/adet
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["FC-FILL", "2026-12-01", "FC-M", 4]])  # 40 sa = dolu hafta
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc["id"]]})
    before = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc["id"]]}).json()[0]["weeks"][0]["utilization"]
    assert before <= 1.001
    lt = client.post("/api/plan/leadtime", headers=auth, json={"item_code": "FC-M", "quantity": 5, "start": WEEK.isoformat()}).json()
    r = client.post(
        "/api/plan/leadtime/forecast",
        headers=auth,
        json={"item_code": lt["item_code"], "quantity": lt["quantity"], "steps": lt["steps"]},
    )
    assert r.status_code == 200
    listed = client.get("/api/plan/forecast", headers=auth).json()
    assert len(listed) == 1 and listed[0]["item_code"] == "FC-M"
    load = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc["id"]]}).json()
    for w in load[0]["weeks"]:
        assert w["utilization"] <= 1.001
    assert load[0]["weeks"][0]["utilization"] <= 1.001


def test_merge_tolerance_clusters(client, auth):
    _wc_setup(client, auth, "MG-1")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["MG-M", "Merge", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["MG-M", 10, "Op", "MG-1", 36]])
    base = date.today() + timedelta(days=30)
    for i, d in enumerate([0, 3, 20]):
        client.post(
            "/api/orders",
            headers=auth,
            json={"order_no": f"MG-{i}", "due_date": (base + timedelta(days=d)).isoformat(), "item_code": "MG-M", "quantity": 10},
        )
    tight = client.get("/api/plan/merge-suggestions", headers=auth, params={"tolerance_days": 5}).json()
    assert any(g["recommended"] and g["order_count"] == 2 and g["due_spread_days"] <= 5 for g in tight)
    assert any(not g["recommended"] and g["order_count"] == 3 for g in tight)


def test_forecast_orders_in_list(client, auth):
    wc = _wc_setup(client, auth, "FO-1")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["FO-M", "ForecastOrd", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["FO-M", 10, "Op", "FO-1", 360]])
    lt = client.post("/api/plan/leadtime", headers=auth, json={"item_code": "FO-M", "quantity": 2, "start": WEEK.isoformat()}).json()
    client.post(
        "/api/plan/leadtime/forecast",
        headers=auth,
        json={"item_code": lt["item_code"], "quantity": lt["quantity"], "label": "Tahmin-A", "steps": lt["steps"]},
    )
    by_status = client.get("/api/orders", headers=auth, params={"status": "forecast"}).json()
    mine = [o for o in by_status if o["order_no"] == "Tahmin-A"]
    assert len(mine) == 1 and mine[0]["plan_status"] == "forecast"
    by_plan = client.get("/api/orders", headers=auth, params={"plan_status": "forecast"}).json()
    assert any(o["order_no"] == "Tahmin-A" and o["status"] == "forecast" for o in by_plan)
    load = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc["id"]]}).json()
    assert any(w["forecast_hours"] > 0 for w in load[0]["weeks"])


def test_merge_impact_preview(client, auth):
    wc = _wc_setup(client, auth, "MI-1")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["MI-M", "Impact", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["MI-M", 10, "Op", "MI-1", 36]])
    base = date.today() + timedelta(days=30)
    ids = []
    for i in range(2):
        o = client.post(
            "/api/orders",
            headers=auth,
            json={"order_no": f"MI-{i}", "due_date": (base + timedelta(days=i * 2)).isoformat(), "item_code": "MI-M", "quantity": 10},
        ).json()
        ids.append(o["id"])
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc["id"]]})
    r = client.post(
        "/api/plan/merge/impact",
        headers=auth,
        json={"merge_groups": [{"order_ids": ids}], "start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc["id"]], "mode": "due_date"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["merge_count"] == 1 and body["order_count"] == 2
    assert "delayed_orders" in body and "load_deltas" in body and "note" in body


def test_merge_impact_shows_delays(client, auth):
    """Dar is merkezinde birlestirme sonrasi baska siparislerin kaymasi."""
    wc = _wc_setup(client, auth, "MID-1")
    client.put(
        f"/api/workcenters/{wc['id']}",
        headers=auth,
        json={k: v for k, v in wc.items() if k not in ("shifts", "machines", "employee_count", "machine_employee_count", "capacity_headcount")},
    )
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["MID-M", "ImpactDemo", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["MID-M", 10, "Op", "MID-1", 3600]])
    base = WEEK
    ids = {}
    for no, qty, off in [("MID-A", 12, 5), ("MID-B", 15, 8), ("MID-C", 28, 7), ("MID-D", 10, 3)]:
        o = client.post(
            "/api/orders",
            headers=auth,
            json={"order_no": no, "due_date": (base + timedelta(days=off)).isoformat(), "item_code": "MID-M", "quantity": qty},
        ).json()
        ids[no] = o["id"]
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 8, "work_center_ids": [wc["id"]]})
    r = client.post(
        "/api/plan/merge/impact",
        headers=auth,
        json={"merge_groups": [{"order_ids": [ids["MID-A"], ids["MID-B"]]}], "start_week": WEEK.isoformat(), "weeks": 8, "work_center_ids": [wc["id"]], "mode": "due_date"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["delayed_count"] >= 1 or len(body["load_deltas"]) >= 1


def test_leadtime_skips_full_weeks(client, auth):
    """Terminleme, %100 dolu haftalari atlayip ilk bos haftadan baslar."""
    wc = _wc_setup(client, auth, "LT-FULL")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["LT-M", "Lead", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["LT-M", 10, "Op", "LT-FULL", 3600]])
    # Haftayi doldur (~40 sa)
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["LT-FILL", "2026-12-01", "LT-M", 40]])
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc["id"]]})
    load0 = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc["id"]]}).json()[0]["weeks"][0]
    assert load0["utilization"] >= 0.99
    lt = client.post("/api/plan/leadtime", headers=auth, json={"item_code": "LT-M", "quantity": 5, "start": WEEK.isoformat()}).json()
    start_day = lt["start"][:10]
    assert start_day >= (date.fromisoformat(WEEK.isoformat()) + timedelta(days=7)).isoformat(), f"Baslangic dolu haftada: {start_day}"
