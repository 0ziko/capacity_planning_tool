"""Gantt ve haftalik yuk gerceklesen entegrasyonu."""

from datetime import date, timedelta

from tests.test_capacity_flow import _upload


def _monday(offset_weeks: int = 0) -> str:
    d = date.today()
    return (d - timedelta(days=d.weekday()) + timedelta(weeks=offset_weeks)).isoformat()


def test_load_includes_actual_hours(client, auth):
    wk = _monday()
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"], [["GNT-1", "Gantt IM", "E", 8]])
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [["G1", "A", "GNT-1"]])
    _upload(client, auth, "items", ["Stok Kodu", "Ürün Grubu"], [["GNT-M", "GN"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"], [["GNT-M", 10, "OP1", "GNT-1", 36, "GNT-M-10"]])
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["G-S1", "2026-12-01", "GNT-M", 10]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "GNT-1")
    client.post("/api/plan/auto", headers=auth, json={"start_week": wk, "weeks": 2, "work_center_ids": [wc["id"]], "replace_existing": True, "mode": "due_date"})
    prod_day = wk
    _upload(client, auth, "production", ["Tarih", "Yarımamül Kodu", "Miktar", "Sipariş No"], [[prod_day, "GNT-M-10", 5, "G-S1"]])
    load = client.get("/api/plan/load", headers=auth, params={"start": wk, "weeks": 1, "work_center_ids": [wc["id"]]}).json()
    w0 = load[0]["weeks"][0]
    assert w0["planned_hours"] > 0
    assert w0["actual_hours"] > 0
    assert w0["actual_utilization"] > 0
    assert w0["remaining_hours"] == round(w0["planned_hours"] - w0["actual_hours"], 2)
    assert w0["idle_hours"] == round(max(w0["capacity_hours"] - w0["planned_hours"], 0), 2)
    if w0["capacity_hours"] > 0 and w0["remaining_hours"] > 0:
        assert w0["remaining_days"] > 0
        assert w0["remaining_days"] <= w0["remaining_hours"]


def test_gantt_bars_with_production(client, auth):
    wk = _monday()
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"], [["GNT-2", "Gantt2", "E", 8]])
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [["G2", "B", "GNT-2"]])
    _upload(client, auth, "items", ["Stok Kodu", "Ürün Grubu"], [["GNT-M2", "GN"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"], [["GNT-M2", 10, "OP1", "GNT-2", 36, "GNT-M2-10"]])
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["G-S2", "2026-12-01", "GNT-M2", 20]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "GNT-2")
    client.post("/api/plan/auto", headers=auth, json={"start_week": wk, "weeks": 2, "work_center_ids": [wc["id"]], "replace_existing": True, "mode": "due_date"})
    _upload(client, auth, "production", ["Tarih", "Yarımamül Kodu", "Miktar", "Sipariş No"], [[wk, "GNT-M2-10", 8, "G-S2"]])
    end = (date.fromisoformat(wk) + timedelta(days=13)).isoformat()
    g = client.get("/api/plan/gantt", headers=auth, params={"work_center_id": wc["id"], "start": wk, "end": end}).json()
    assert g["work_center_code"] == "GNT-2"
    assert len(g["bars"]) >= 1
    bar = next(b for b in g["bars"] if b["order_no"] == "G-S2")
    assert bar["semi_finished_code"] == "GNT-M2-10"
    assert bar["produced_qty"] == 8
    assert bar["remaining_qty"] == 12
    assert bar["status"] == "in_progress"


def test_progress_pct_quantity_when_fully_produced(client, auth):
    """Tum operasyonlar siparis miktari kadar uretildiyse ilerleme %100 (saat/setup farki dusurmez)."""
    wk = _monday()
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"], [["PCT-1", "Pct IM", "E", 8]])
    _upload(client, auth, "employees", ["Sicil No", "Ad Soyad", "İş Merkezi Kodu"], [["P1", "A", "PCT-1"]])
    _upload(client, auth, "items", ["Stok Kodu", "Ürün Grubu"], [["PCT-M", "GN"]])
    _upload(
        client, auth, "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Setup (dk)", "Yarımamül Kodu"],
        [["PCT-M", 10, "OP1", "PCT-1", 36, 15, "PCT-M-10"], ["PCT-M", 20, "OP2", "PCT-1", 36, 15, "PCT-M-20"]],
    )
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["PCT-S1", "2026-12-01", "PCT-M", 100]])
    _upload(
        client, auth, "production",
        ["Tarih", "Yarımamül Kodu", "Miktar", "Sipariş No"],
        [[wk, "PCT-M-10", 100, "PCT-S1"], [wk, "PCT-M-20", 100, "PCT-S1"]],
    )
    prog = client.get("/api/progress/orders", headers=auth, params={"as_of": wk}).json()
    row = next(p for p in prog if p["order_no"] == "PCT-S1")
    assert all(o["pct"] == 100 for o in row["ops"])
    assert row["pct"] == 100
    assert row["status"] == "completed"
    assert row["earned_hours"] < row["required_hours"]  # setup gunluk uretimde sayilmaz
