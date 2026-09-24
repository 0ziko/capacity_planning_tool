"""Is tasima: bos kapasite kaydirmaz, cakisan ayni IM kayar, tamamlanan op kilitlenir."""

from datetime import timedelta

from app.models import Order, Reservation
from app.models import ProductionActual, Shipment
from app.services.order_finished_netting import PROVENANCE_EXTERNAL, compute_order_demand_netting
from tests.test_capacity_flow import _upload, _weekly_staffing
from tests.test_revenue_modes import WEEK, _setup


def _wc_capacity_hours(client, auth, wc_id: int, week_start: str) -> float:
    load = client.get("/api/plan/load", headers=auth, params={"start": week_start, "weeks": 1, "work_center_ids": [wc_id]}).json()
    assert load and load[0]["weeks"], "kapasite yuklenemedi"
    return float(load[0]["weeks"][0]["capacity_hours"])


def _two_wcs(client, auth):
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [["MOVE-1", "Taşıma hattı", "E", 10, 4], ["MOVE-X", "İlgisiz hat", "E", 10, 4]],
    )
    _upload(
        client,
        auth,
        "shifts",
        ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
        [
            ["MOVE-1", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4],
            ["MOVE-X", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4],
        ],
    )
    _weekly_staffing(client, auth, [['MOVE-1', 10, 4, 5], ['MOVE-X', 10, 4, 5]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["MOVEA", "A", "G"], ["MOVEB", "B", "G"], ["MOVEC", "C", "G"], ["MOVE2", "İki op", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [
            ["MOVEA", 10, "Op", "MOVE-1", 3600],
            ["MOVEB", 10, "Op", "MOVE-1", 3600],
            ["MOVEC", 10, "Op", "MOVE-X", 3600],
            ["MOVE2", 10, "Kesim", "MOVE-1", 3600],
            ["MOVE2", 20, "Paket", "MOVE-1", 3600],
        ],
    )
    wcs = {w["code"]: w for w in client.get("/api/workcenters", headers=auth).json()}
    for code in ("MOVE-1", "MOVE-X"):
        wc = wcs[code]
        body = {k: v for k, v in wc.items() if k not in ("shifts", "machines", "employee_count", "machine_employee_count", "capacity_headcount")}
        body["planning_reserve_pct"] = 0.0
        client.put(f"/api/workcenters/{wc['id']}", headers=auth, json=body)
    wcs = {w["code"]: w for w in client.get("/api/workcenters", headers=auth).json()}
    wc1, wcx = wcs["MOVE-1"]["id"], wcs["MOVE-X"]["id"]
    week2 = (WEEK + timedelta(weeks=1)).isoformat()
    assert _wc_capacity_hours(client, auth, wc1, WEEK.isoformat()) == 200.0
    assert _wc_capacity_hours(client, auth, wc1, week2) == 200.0
    assert _wc_capacity_hours(client, auth, wcx, WEEK.isoformat()) == 200.0
    return wc1, wcx


def _hours_by_order_week(lines, week):
    out = {}
    for l in lines:
        if l["week_start"] == week:
            out[l["order_no"]] = out.get(l["order_no"], 0.0) + float(l["planned_hours"])
    return out


def _assert_job_move_clean_slate(db, order_nos: tuple[str, ...]):
    assert db.query(ProductionActual).count() == 0
    assert db.query(Shipment).count() == 0
    assert db.query(Reservation).count() == 0
    if order_nos:
        ids = [o.id for o in db.query(Order).filter(Order.order_no.in_(order_nos)).all()]
        if ids:
            assert db.query(Reservation).filter(Reservation.order_id.in_(ids)).count() == 0


def test_order_delete_clears_reservation_no_id_reuse_credit(client, auth, db):
    wc1, _wcx = _two_wcs(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "JM-RES", "due_date": "2026-09-11", "item_code": "MOVEA", "quantity": 50},
    )
    oid = r.json()["id"]
    order = db.get(Order, oid)
    db.add(
        Reservation(
            item_id=order.item_id,
            order_id=oid,
            quantity=30,
            source="manual",
            stock_provenance=PROVENANCE_EXTERNAL,
        )
    )
    db.commit()
    client.delete("/api/orders", headers=auth, params={"status": "open"})
    db.expire_all()
    assert db.query(Reservation).filter(Reservation.order_id == oid).count() == 0
    r2 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "JM-RES2", "due_date": "2026-09-11", "item_code": "MOVEA", "quantity": 50},
    )
    order2 = db.get(Order, r2.json()["id"])
    net = compute_order_demand_netting(db, order2)
    assert net.net_production_qty == 50.0


def test_job_move_free_capacity_keeps_unrelated_and_same_wc(client, auth, db):
    wc1, wcx = _two_wcs(client, auth)
    week2 = (WEEK + timedelta(weeks=1)).isoformat()
    order_nos = ("JM-A", "JM-B", "JM-C")
    for no, due, item, qty in [
        ("JM-A", "2026-09-11", "MOVEA", 50),
        ("JM-B", "2026-09-18", "MOVEB", 50),
        ("JM-C", "2026-09-11", "MOVEC", 80),
    ]:
        assert client.post("/api/orders", headers=auth, json={"order_no": no, "due_date": due, "item_code": item, "quantity": qty}).status_code == 201
    _assert_job_move_clean_slate(db, order_nos)

    planned = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc1, wcx], "mode": "due_date"}).json()
    assert planned["created"] >= 3

    orders = {o["order_no"]: o for o in client.get("/api/orders", headers=auth).json()}
    bid = orders["JM-B"]["id"]

    preview = client.get("/api/plan/revisions/move-preview", headers=auth, params={"order_id": bid}).json()
    assert preview["movable_qty"] == 50
    assert preview["ops"][0]["locked"] is False
    assert "MOVE-1" in preview["consumed_work_centers"]

    body = {
        "reason_codes": ["vip_pull_in"],
        "note": "bos kapasite",
        "start_week": WEEK.isoformat(),
        "weeks": 2,
        "work_center_ids": [wc1, wcx],
        "mode": "due_date",
    }
    rev = client.post("/api/plan/revisions", headers=auth, json=body).json()
    ch = client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={
            "entity_type": "order",
            "entity_id": bid,
            "extra_key": "MOVEB",
            "field": "job_move",
            "new_value": '{"item_code":"MOVEB","start_date":"%s","qty_mode":"remaining","quantity":null}' % WEEK.isoformat(),
        },
    )
    assert ch.status_code == 200, ch.text

    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    cmp = calc.json()["compare"]
    assert cmp["bumped_orders"] == []

    applied = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert applied.status_code == 200, applied.text

    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc1, wcx]}).json()
    w1 = _hours_by_order_week(lines, WEEK.isoformat())
    assert round(w1.get("JM-B", 0), 1) == 50
    assert round(w1.get("JM-A", 0), 1) == 50
    assert round(w1.get("JM-C", 0), 1) == 80
    w2 = _hours_by_order_week(lines, week2)
    assert round(w2.get("JM-C", 0), 1) == 0
    assert round(w2.get("JM-A", 0), 1) == 0


def test_job_move_overflow_bumps_only_same_wc(client, auth, db):
    wc1, wcx = _two_wcs(client, auth)
    week2 = (WEEK + timedelta(weeks=1)).isoformat()
    order_nos = ("JM-A", "JM-B", "JM-C")
    for no, due, item, qty in [
        ("JM-A", "2026-09-11", "MOVEA", 150),
        ("JM-B", "2026-09-18", "MOVEB", 80),
        ("JM-C", "2026-09-11", "MOVEC", 100),
    ]:
        assert client.post("/api/orders", headers=auth, json={"order_no": no, "due_date": due, "item_code": item, "quantity": qty}).status_code == 201
    _assert_job_move_clean_slate(db, order_nos)

    _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc1, wcx], "mode": "due_date"})
    orders = {o["order_no"]: o for o in client.get("/api/orders", headers=auth).json()}
    bid = orders["JM-B"]["id"]

    rev = client.post(
        "/api/plan/revisions",
        headers=auth,
        json={"reason_codes": ["vip_pull_in"], "note": "dolu hafta", "start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc1, wcx], "mode": "due_date"},
    ).json()
    ch = client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={
            "entity_type": "order",
            "entity_id": bid,
            "field": "job_move",
            "new_value": '{"item_code":"MOVEB","start_date":"%s","qty_mode":"remaining"}' % WEEK.isoformat(),
        },
    )
    assert ch.status_code == 200, ch.text
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    assert "JM-A" in calc.json()["compare"]["bumped_orders"]
    assert "JM-C" not in calc.json()["compare"]["bumped_orders"]

    assert client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth).status_code == 200
    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc1, wcx]}).json()
    w1 = _hours_by_order_week(lines, WEEK.isoformat())
    w2 = _hours_by_order_week(lines, week2)
    assert round(w1.get("JM-B", 0), 1) == 80
    assert round(w1.get("JM-A", 0), 1) == 120
    assert round(w2.get("JM-A", 0), 1) == 30
    assert round(w1.get("JM-C", 0), 1) == 100


def test_job_move_preview_locks_completed_op(client, auth):
    wc1, _wcx = _two_wcs(client, auth)
    r = client.post("/api/orders", headers=auth, json={"order_no": "JM-2", "due_date": "2026-09-18", "item_code": "MOVE2", "quantity": 10})
    assert r.status_code == 201
    oid = r.json()["id"]
    _upload(
        client,
        auth,
        "production",
        ["Tarih", "İş Merkezi Kodu", "Stok Kodu", "Operasyon Sıra", "Sipariş No", "Miktar"],
        [[WEEK.isoformat(), "MOVE-1", "MOVE2", 10, "JM-2", 10]],
    )
    preview = client.get("/api/plan/revisions/move-preview", headers=auth, params={"order_id": oid}).json()
    assert preview["movable_qty"] == 10
    by_seq = {op["operation_seq"]: op for op in preview["ops"]}
    assert by_seq[10]["locked"] is True
    assert by_seq[20]["locked"] is False
    assert by_seq[20]["status"] == "current"


def test_due_date_revision_still_works(client, auth):
    wc_id = _setup(client, auth)
    r = client.post("/api/orders", headers=auth, json={"order_no": "RV-DUE", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 20, "unit_price": 10})
    assert r.status_code == 201
    order_id = r.json()["id"]
    body = {"reason_codes": ["customer_postpone"], "note": "vade", "start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "mode": "due_date"}
    rev = client.post("/api/plan/revisions", headers=auth, json=body).json()
    ch = client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={"entity_type": "order", "entity_id": order_id, "field": "revised_due_date", "new_value": "2026-09-25"},
    )
    assert ch.status_code == 200
    assert client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth).status_code == 200
    assert client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth).json()["status"] == "applied"

from tests.test_capacity_flow import _plan_with_ack
