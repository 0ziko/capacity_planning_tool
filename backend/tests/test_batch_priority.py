"""B6: tekil siparis ve uretim partisi ayni oncelik listesinde."""

from datetime import date

from app.services import planning
from app.schemas import AutoPlanRequest
from tests.test_capacity_flow import _upload, _weekly_staffing
from tests.test_revenue_modes import WEEK, _setup

START = date(2026, 9, 7)


def _setup_40h(client, auth):
    """1 hafta, 40 saat kapasite (2 kisi x 4 saat x 5 gun)."""
    for b in client.get("/api/plan/production-batches", headers=auth).json():
        client.delete(f"/api/plan/merge/{b['id']}", headers=auth)
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [["PRIO-1", "Oncelik Hatti", "E", 2, 4]],
    )
    _upload(
        client,
        auth,
        "shifts",
        ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
        [["PRIO-1", "G", "0,1,2,3,4", "08:00", "18:00", 2, 4]],
    )
    _weekly_staffing(client, auth, [['PRIO-1', 2, 4, 5]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["PRIO-A", "A", "G"], ["PRIO-B", "B", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["PRIO-A", 10, "Op", "PRIO-1", 3600], ["PRIO-B", 10, "Op", "PRIO-1", 3600]],
    )
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "PRIO-1")
    return wc["id"]


def _simulate(client, auth, db, **kwargs):
    from app.db.session import SessionLocal

    body = {"start_week": START.isoformat(), "weeks": 1, "replace_existing": True, **kwargs}
    session = SessionLocal()
    try:
        return planning.simulate(session, AutoPlanRequest(**body))
    finally:
        session.close()


def test_early_due_order_before_late_batch(client, auth, db):
    """40 saat hafta; 18 Eylul tekil 40 saat, 30 Ekim parti 40 saat -> tekil yerlesir."""
    wc_id = _setup_40h(client, auth)
    solo = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "SOLO-18", "due_date": "2026-09-18", "item_code": "PRIO-A", "quantity": 40, "unit_price": 1},
    ).json()
    b1 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "BAT-1", "due_date": "2026-10-30", "item_code": "PRIO-B", "quantity": 20, "unit_price": 1},
    ).json()
    b2 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "BAT-2", "due_date": "2026-10-30", "item_code": "PRIO-B", "quantity": 20, "unit_price": 1},
    ).json()
    batch = client.post("/api/plan/merge", headers=auth, json={"order_ids": [b1["id"], b2["id"]]}).json()

    sim = _simulate(client, auth, db, work_center_ids=[wc_id], mode="due_date")
    solo_lines = [l for l in sim.lines if l.order_id == solo["id"]]
    batch_lines = [l for l in sim.lines if l.production_batch_id == batch["id"]]
    assert round(sum(l.planned_hours for l in solo_lines), 1) == 40.0
    assert batch_lines == []


def test_batch_alone_does_not_boost_priority(client, auth, db):
    """Esdeger is partiye donusunce kendi basina sira yukselmez."""
    wc_id = _setup_40h(client, auth)
    solo = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "TEK-01", "due_date": "2026-10-15", "item_code": "PRIO-A", "quantity": 40, "unit_price": 1},
    ).json()
    p1 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "P-A", "due_date": "2026-10-15", "item_code": "PRIO-B", "quantity": 20, "unit_price": 1},
    ).json()
    p2 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "P-B", "due_date": "2026-10-15", "item_code": "PRIO-B", "quantity": 20, "unit_price": 1},
    ).json()
    batch = client.post("/api/plan/merge", headers=auth, json={"order_ids": [p1["id"], p2["id"]]}).json()

    sim = _simulate(client, auth, db, work_center_ids=[wc_id], mode="due_date")
    solo_h = sum(l.planned_hours for l in sim.lines if l.order_id == solo["id"])
    batch_h = sum(l.planned_hours for l in sim.lines if l.production_batch_id == batch["id"])
    assert round(solo_h + batch_h, 1) <= 40.0
    # Ayni termin: tekil kodu alfabetik once (P-A/P-B partiden once TEK degil; SOLO vs batch)
    # TEK-01 vs URT-* batch_no — tek kazanmali cunku display_code tie-break, parti onceligi yok
    assert solo_h > 0
    assert batch_h == 0.0


def test_high_revenue_order_before_low_revenue_batch(client, auth, db):
    """Yuksek ciro/saat tekil, dusuk ciro/saat partiden once gelir."""
    wc_id = _setup_40h(client, auth)
    rich = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RICH-1", "due_date": "2026-10-30", "item_code": "PRIO-A", "quantity": 10, "unit_price": 1000},
    ).json()
    cheap1 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "CH-1", "due_date": "2026-09-18", "item_code": "PRIO-B", "quantity": 20, "unit_price": 10},
    ).json()
    cheap2 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "CH-2", "due_date": "2026-09-18", "item_code": "PRIO-B", "quantity": 20, "unit_price": 10},
    ).json()
    batch = client.post("/api/plan/merge", headers=auth, json={"order_ids": [cheap1["id"], cheap2["id"]]}).json()

    sim = _simulate(client, auth, db, work_center_ids=[wc_id], mode="revenue")
    rich_h = sum(l.planned_hours for l in sim.lines if l.order_id == rich["id"])
    batch_h = sum(l.planned_hours for l in sim.lines if l.production_batch_id == batch["id"])
    assert round(rich_h, 1) == 10.0
    # Yuksek ciro/saat tekil once tam sigar; parti yalnizca kalan kapasiteyi alir
    assert batch_h < 40.0
    assert rich_h > 0 and (batch_h == 0.0 or batch_h < rich_h * 4)


def test_overflow_batch_reported_skipped(client, auth, db):
    """Sigmayan parti acikca skipped/unplanned raporlanir."""
    wc_id = _setup_40h(client, auth)
    solo = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "FILL-1", "due_date": "2026-09-11", "item_code": "PRIO-A", "quantity": 40, "unit_price": 100},
    ).json()
    b1 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "SKIP-1", "due_date": "2026-10-30", "item_code": "PRIO-B", "quantity": 20, "unit_price": 10},
    ).json()
    b2 = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "SKIP-2", "due_date": "2026-10-30", "item_code": "PRIO-B", "quantity": 20, "unit_price": 10},
    ).json()
    batch = client.post("/api/plan/merge", headers=auth, json={"order_ids": [b1["id"], b2["id"]]}).json()

    sim = _simulate(client, auth, db, work_center_ids=[wc_id], mode="revenue")
    assert sum(l.planned_hours for l in sim.lines if l.order_id == solo["id"]) > 0
    batch_skipped = [s for s in sim.skipped if s.get("kind") == "batch" or s.get("batch_no")]
    assert batch_skipped, "Parti skipped listesinde olmali"
    assert any(s.get("batch_no") == batch["batch_no"] or batch["batch_no"] in str(s) for s in batch_skipped)


def test_deterministic_candidate_order(client, auth, db):
    """Ayni girdi uc calistirmada ayni aday ve plan sirasi."""
    for b in client.get("/api/plan/production-batches", headers=auth).json():
        client.delete(f"/api/plan/merge/{b['id']}", headers=auth)
    wc_id = _setup(client, auth)
    for no, due, qty, price in [
        ("D-1", "2026-09-11", 30, 50),
        ("D-2", "2026-09-18", 30, 50),
        ("D-3", "2026-09-25", 30, 50),
    ]:
        client.post(
            "/api/orders",
            headers=auth,
            json={"order_no": no, "due_date": due, "item_code": "UCUZ", "quantity": qty, "unit_price": price},
        )

    signatures = []
    for _ in range(3):
        sim = _simulate(client, auth, db, work_center_ids=[wc_id], mode="due_date")
        sig = tuple(
            (l.order_id, l.production_batch_id, l.operation_id, round(l.planned_hours, 4))
            for l in sim.lines
        )
        signatures.append(sig)
    assert signatures[0] == signatures[1] == signatures[2]
