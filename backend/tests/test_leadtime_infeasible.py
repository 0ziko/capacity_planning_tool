"""FAZ 05 / B4: termin basarisizligi acik; uydurma tarih yok."""

from datetime import date, timedelta

from tests.test_capacity_flow import WEEK, _upload
from tests.test_planning_capacity import _wc_setup


def test_leadtime_zero_capacity_10h_infeasible(client, auth):
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [["LT-ZERO", "Kapasitesiz IM", "E", 10, 8]],
    )
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["LT-ZERO-M", "Test", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["LT-ZERO-M", 10, "Op", "LT-ZERO", 36000]],
    )
    lt = client.post(
        "/api/plan/leadtime",
        headers=auth,
        json={"item_code": "LT-ZERO-M", "quantity": 1, "start": WEEK.isoformat()},
    ).json()
    assert lt["status"] == "infeasible"
    assert lt["end"] is None
    assert lt["remaining_hours"] == 10.0
    assert "2040" not in str(lt)
    assert lt["steps"][0]["status"] == "insufficient_capacity"


def test_leadtime_15min_job_has_duration(client, auth):
    wc = _wc_setup(client, auth, "LT-15M")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["LT-15M-M", "Q", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["LT-15M-M", 10, "Op", "LT-15M", 900]],
    )
    lt = client.post(
        "/api/plan/leadtime",
        headers=auth,
        json={"item_code": "LT-15M-M", "quantity": 1, "start": WEEK.isoformat()},
    ).json()
    assert lt["status"] == "complete"
    s = lt["steps"][0]
    assert abs(s["scheduled_hours"] - 0.25) < 0.02
    assert s["start"] and s["end"]
    assert s["start"] != s["end"]


def test_leadtime_horizon_exceeded_not_success(client, auth):
    wc = _wc_setup(client, auth, "LT-HZ")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["LT-HZ-M", "H", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["LT-HZ-M", 10, "Op", "LT-HZ", 3600]],
    )
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["LT-HZ-F", "2026-12-01", "LT-HZ-M", 40]])
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc["id"]]})
    lt = client.post(
        "/api/plan/leadtime",
        headers=auth,
        json={"item_code": "LT-HZ-M", "quantity": 50, "start": WEEK.isoformat(), "horizon_days": 14},
    ).json()
    assert lt["status"] != "complete"
    assert lt["end"] is None


def test_leadtime_respects_future_planned_load(client, auth):
    wc = _wc_setup(client, auth, "LT-PLN")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["LT-PLN-M", "P", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["LT-PLN-M", 10, "Op", "LT-PLN", 3600]],
    )
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["LT-PLN-F", "2026-12-01", "LT-PLN-M", 40]])
    client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc["id"]]})
    load0 = client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc["id"]]}).json()[0]["weeks"][0]
    assert load0["utilization"] >= 0.99
    week2 = (WEEK + timedelta(weeks=1)).isoformat()
    lt_empty = client.post(
        "/api/plan/leadtime",
        headers=auth,
        json={"item_code": "LT-PLN-M", "quantity": 2, "start": week2},
    ).json()
    lt_after_fill = client.post(
        "/api/plan/leadtime",
        headers=auth,
        json={"item_code": "LT-PLN-M", "quantity": 2, "start": WEEK.isoformat()},
    ).json()
    assert lt_empty["status"] == "complete"
    assert lt_after_fill["status"] == "complete"
    assert lt_after_fill["start"][:10] >= week2
    assert lt_empty["start"][:10] == week2


def test_forecast_rejects_infeasible_leadtime(client, auth):
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [["LT-FC", "Kapasitesiz", "E", 10, 8]],
    )
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["LT-FC-M", "F", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["LT-FC-M", 10, "Op", "LT-FC", 3600]],
    )
    lt = client.post(
        "/api/plan/leadtime",
        headers=auth,
        json={"item_code": "LT-FC-M", "quantity": 1, "start": WEEK.isoformat()},
    ).json()
    r = client.post(
        "/api/plan/leadtime/forecast",
        headers=auth,
        json={"item_code": lt["item_code"], "quantity": lt["quantity"], "steps": lt["steps"], "status": lt["status"]},
    )
    assert r.status_code == 400


def test_gantt_uses_effective_due_order(client, auth):
    wk = WEEK.isoformat()
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"], [["LT-GNT", "Gantt", "E", 8]])
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [["LG1", "A", "LT-GNT"]])
    _upload(client, auth, "items", ["Stok Kodu", "Ürün Grubu"], [["LT-G-M", "GN"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"],
        [["LT-G-M", 10, "OP", "LT-GNT", 3600, "LT-G-M-10"]],
    )
    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Termin", "Stok Kodu", "Miktar"],
        [["LT-G-LATE", "2026-12-31", "LT-G-M", 5], ["LT-G-EARLY", "2026-12-31", "LT-G-M", 5]],
    )
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "LT-GNT")
    early_id = next(o["id"] for o in client.get("/api/orders", headers=auth).json() if o["order_no"] == "LT-G-EARLY")
    client.put(
        f"/api/orders/{early_id}",
        headers=auth,
        json={
            "order_no": "LT-G-EARLY",
            "due_date": "2026-12-31",
            "revised_due_date": (WEEK + timedelta(days=7)).isoformat(),
            "item_id": next(i["id"] for i in client.get("/api/items", headers=auth).json() if i["code"] == "LT-G-M"),
            "quantity": 5,
            "customer": "T",
        },
    )
    client.post("/api/plan/auto", headers=auth, json={"start_week": wk, "weeks": 1, "work_center_ids": [wc["id"]], "replace_existing": True})
    end = (WEEK + timedelta(days=6)).isoformat()
    g = client.get("/api/plan/gantt", headers=auth, params={"work_center_id": wc["id"], "start": wk, "end": end}).json()
    bars = [b for b in g["bars"] if b["order_no"] in ("LT-G-EARLY", "LT-G-LATE")]
    assert len(bars) == 2
    early = next(b for b in bars if b["order_no"] == "LT-G-EARLY")
    late = next(b for b in bars if b["order_no"] == "LT-G-LATE")
    assert early["planned_start"] <= late["planned_start"]
