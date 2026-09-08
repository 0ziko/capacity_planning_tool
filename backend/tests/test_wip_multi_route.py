"""Coklu rota yarimamul kodu ve Excel tarih seri import."""

from datetime import date

from tests.test_capacity_flow import _upload

WEEK = date(2026, 9, 7)
WIP = "5005510-01"


def _setup_multi_route(client, auth):
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Kişi Başı Verimli Saat"],
        [["PRS3", "PRESHANE 3", "E", 8]],
    )
    _upload(
        client,
        auth,
        "items",
        ["Stok Kodu", "Stok Adı", "Ürün Grubu"],
        [
            ["6005510", "Urun A", "GN"],
            ["6005575", "Urun B", "GN"],
            ["6005984", "Urun C", "GN"],
        ],
    )
    for code in ("6005510", "6005575", "6005984"):
        _upload(
            client,
            auth,
            "routing",
            ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"],
            [[code, 10, "SIVAMA", "PRS3", 50, WIP]],
        )


def test_production_import_multi_route_wip_fifo(client, auth):
    _setup_multi_route(client, auth)
    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Termin", "Stok Kodu", "Miktar"],
        [
            ["S-LATE", "2026-12-31", "6005984", 50],
            ["S-MID", "2026-10-15", "6005575", 40],
            ["S-EARLY", "2026-09-15", "6005510", 30],
        ],
    )
    cols = [
        "Tarih",
        "Yarımamül Kodu",
        "Miktar",
        "İş Merkezi Kodu (opsiyonel)",
        "Stok Kodu (opsiyonel)",
        "Operasyon Sıra (opsiyonel)",
        "Fiili Süre (saat)",
        "Sipariş No (opsiyonel)",
    ]
    r = _upload(client, auth, "production", cols, [[WEEK.isoformat(), WIP, 100, "", "", "", "", ""]])
    assert not r["errors"], r["errors"]
    assert r["inserted"] >= 1

    prog = {p["order_no"]: p for p in client.get("/api/progress/orders", headers=auth).json()}
    assert prog["S-EARLY"]["ops"][0]["produced_qty"] == 30
    assert prog["S-MID"]["ops"][0]["produced_qty"] == 40
    assert prog["S-LATE"]["ops"][0]["produced_qty"] == 30


def test_production_import_excel_serial_date(client, auth):
    _setup_multi_route(client, auth)
    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Termin", "Stok Kodu", "Miktar"],
        [["S-1", "2026-12-01", "6005510", 100]],
    )
    cols = ["Tarih", "Yarımamül Kodu", "Miktar", "Sipariş No (opsiyonel)"]
    # 46273 = 2026-09-08 (Excel serial)
    r = _upload(client, auth, "production", cols, [[46273, WIP, 10, ""]])
    assert not r["errors"], r["errors"]

    prog = client.get("/api/progress/orders", headers=auth, params={"as_of": "2026-09-08"}).json()
    row = next(p for p in prog if p["order_no"] == "S-1")
    assert row["ops"][0]["produced_qty"] == 10


def test_production_import_multi_route_with_item_hint(client, auth):
    """Stok kodu verildiginde coklu rota hatasi olmamali."""
    _setup_multi_route(client, auth)
    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Termin", "Stok Kodu", "Miktar"],
        [["S-HINT", "2026-12-01", "6005575", 100]],
    )
    cols = [
        "Tarih",
        "Yarımamül Kodu",
        "Miktar",
        "Stok Kodu (opsiyonel)",
        "Sipariş No (opsiyonel)",
    ]
    r = _upload(
        client,
        auth,
        "production",
        cols,
        [[WEEK.isoformat(), WIP, 15, "6005575", "S-HINT"]],
    )
    assert not r["errors"], r["errors"]
    assert r["inserted"] == 1
    prog = client.get("/api/progress/orders", headers=auth).json()
    row = next(p for p in prog if p["order_no"] == "S-HINT")
    assert row["ops"][0]["produced_qty"] == 15
