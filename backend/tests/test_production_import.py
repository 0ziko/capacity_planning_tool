"""Günlük üretim import — siparis no opsiyonel."""

from datetime import date

from tests.test_capacity_flow import _upload

WEEK = date(2026, 9, 7)


def test_production_template_order_no_optional(client, auth):
    kinds = {k["kind"]: k for k in client.get("/api/imports/kinds", headers=auth).json()}
    prod = kinds["production"]
    assert "Sipariş No (opsiyonel)" in prod["columns"]
    assert "Sipariş No" not in prod["required"]
    assert "Tarih" in prod["required"]
    assert "Miktar" in prod["required"]

    r = client.get("/api/imports/template/production", headers=auth)
    assert r.status_code == 200


def test_production_import_without_order_no(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["PR-WC", "WC", "E", 10, 4]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [["PR-WC", "G", "0,1,2,3,4", "08:00", "18:00", 1, 4]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["PR-M", "Mamul", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"],
        [["PR-M", 10, "Op", "PR-WC", 3600, "PR-M-10"]],
    )
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["PR-S1", "2026-09-30", "PR-M", 100]])

    cols = ["Tarih", "Yarımamül Kodu", "Miktar", "İş Merkezi Kodu (opsiyonel)", "Stok Kodu (opsiyonel)", "Operasyon Sıra (opsiyonel)", "Fiili Süre (saat)", "Sipariş No (opsiyonel)"]
    _upload(client, auth, "production", cols, [[WEEK.isoformat(), "PR-M-10", 25, "", "", "", "", ""]])

    prog = client.get("/api/progress/orders", headers=auth, params={"as_of": WEEK.isoformat()}).json()
    row = next(p for p in prog if p["order_no"] == "PR-S1")
    assert row["produced_qty"] >= 25
