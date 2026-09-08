"""Birlikte sevk (co-shipment) planlama modu testleri."""

from datetime import date, timedelta

from tests.test_capacity_flow import _upload

WEEK = date(2026, 9, 7)
DUE = date(2026, 9, 28)  # Pazar -> termin haftasi 28 Eylul civari


def _seed_wc(client, auth, code: str = "CS-WC"):
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [[code, "CoShip WC", "E", 10, 4]],
    )
    _upload(
        client,
        auth,
        "shifts",
        ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
        [[code, "G", "0,1,2,3,4", "08:00", "18:00", 5, 4]],
    )
    return next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == code)


def _seed_three_positions(client, auth, wc_code: str, order_no: str = "CS-1001"):
    items = ["CS-P10", "CS-P20", "CS-P30"]
    for it in items:
        _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [[it, it, "G"]])
        _upload(
            client,
            auth,
            "routing",
            ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
            [[it, 10, "Op", wc_code, 3600]],
        )
    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Poz No", "Müşteri", "Termin", "Stok Kodu", "Miktar"],
        [
            [order_no, "10", "ABC Auto", DUE.isoformat(), "CS-P10", 1],
            [order_no, "20", "ABC Auto", DUE.isoformat(), "CS-P20", 1],
            [order_no, "30", "ABC Auto", DUE.isoformat(), "CS-P30", 1],
        ],
    )


def test_co_shipment_disabled_unchanged(client, auth):
    wc = _seed_wc(client, auth, "CS-0")
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["CS-M", "M", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["CS-M", 10, "Op", "CS-0", 3600]])
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["CS-N1", DUE.isoformat(), "CS-M", 1]])

    base = client.post("/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [wc["id"]]}).json()
    with_flag = client.post(
        "/api/plan/auto",
        headers=auth,
        json={
            "start_week": WEEK.isoformat(),
            "weeks": 4,
            "work_center_ids": [wc["id"]],
            "co_shipment": {"enabled": False, "ready_before_delivery_days": 3, "selections": []},
        },
    ).json()
    assert base["created"] == with_flag["created"]
    assert with_flag["co_shipment_results"] == []


def test_co_shipment_same_week_and_target(client, auth):
    wc = _seed_wc(client, auth, "CS-1")
    _seed_three_positions(client, auth, "CS-1", "CS-1001")
    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["CS-OTHER", DUE.isoformat(), "CS-P10", 1]])

    r = client.post(
        "/api/plan/auto",
        headers=auth,
        json={
            "start_week": WEEK.isoformat(),
            "weeks": 8,
            "work_center_ids": [wc["id"]],
            "co_shipment": {
                "enabled": True,
                "ready_before_delivery_days": 3,
                "selections": [{"order_no": "CS-1001", "position_nos": None}],
            },
        },
    ).json()
    assert len(r["co_shipment_results"]) == 1
    res = r["co_shipment_results"][0]
    assert res["same_week_ok"] is True
    assert res["on_target"] is True
    target = date.fromisoformat(res["target_ready_date"])
    assert target == DUE - timedelta(days=3)

    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "weeks": 8}).json()
    cs_lines = [ln for ln in lines if ln["order_no"].startswith("CS-1001")]
    weeks_used = {ln["week_start"] for ln in cs_lines}
    assert len(weeks_used) == 1


def test_co_shipment_partial_positions(client, auth):
    wc = _seed_wc(client, auth, "CS-2")
    _seed_three_positions(client, auth, "CS-2", "CS-2001")
    r = client.post(
        "/api/plan/auto",
        headers=auth,
        json={
            "start_week": WEEK.isoformat(),
            "weeks": 6,
            "work_center_ids": [wc["id"]],
            "co_shipment": {
                "enabled": True,
                "ready_before_delivery_days": 3,
                "selections": [{"order_no": "CS-2001", "position_nos": ["10", "20"]}],
            },
        },
    ).json()
    assert len(r["co_shipment_results"]) == 1
    assert set(r["co_shipment_results"][0]["position_nos"]) == {"10", "20"}


def test_co_shipment_capacity_exception(client, auth):
    wc = _seed_wc(client, auth, "CS-3")
    items = ["CS3-P10", "CS3-P20", "CS3-P30"]
    for it in items:
        _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [[it, it, "G"]])
        _upload(
            client,
            auth,
            "routing",
            ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
            [[it, 10, "Op", "CS-3", 3600 * 45]],
        )
    _upload(
        client,
        auth,
        "orders",
        ["Sipariş No", "Poz No", "Müşteri", "Termin", "Stok Kodu", "Miktar"],
        [
            ["CS-3001", "10", "ABC", DUE.isoformat(), "CS3-P10", 1],
            ["CS-3001", "20", "ABC", DUE.isoformat(), "CS3-P20", 1],
            ["CS-3001", "30", "ABC", DUE.isoformat(), "CS3-P30", 1],
        ],
    )
    r = client.post(
        "/api/plan/auto",
        headers=auth,
        json={
            "start_week": WEEK.isoformat(),
            "weeks": 6,
            "work_center_ids": [wc["id"]],
            "co_shipment": {
                "enabled": True,
                "ready_before_delivery_days": 3,
                "selections": [{"order_no": "CS-3001", "position_nos": None}],
            },
        },
    ).json()
    assert any(e["code"] == "CAPACITY_SAME_WEEK" for e in r["co_shipment_exceptions"])
