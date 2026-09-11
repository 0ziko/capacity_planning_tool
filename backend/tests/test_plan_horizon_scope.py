"""B7: yeniden planlama yalnizca secili ufku siler; ufuk disi korunur."""

from datetime import date

from sqlalchemy.orm import joinedload

from app.models import Order, PlanLine, RoutingOperation
from tests.test_capacity_flow import _upload
from tests.test_revenue_modes import _setup

START = date(2026, 9, 14)
NOV = date(2026, 11, 2)
BEFORE = date(2026, 9, 7)
UPPER_BOUND = date(2026, 9, 28)


def _insert_plan_line(db, order_no: str, wc_id: int, week_start: date, *, hours: float = 8.0, qty: float = 8.0, mode: str = "auto") -> int:
    order = (
        db.query(Order)
        .options(joinedload(Order.item))
        .filter(Order.order_no == order_no)
        .first()
    )
    op = db.query(RoutingOperation).filter(RoutingOperation.item_id == order.item_id).first()
    pl = PlanLine(
        order_id=order.id,
        operation_id=op.id,
        work_center_id=wc_id,
        week_start=week_start,
        planned_hours=hours,
        planned_qty=qty,
        mode=mode,
        created_by="test",
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return pl.id


def test_replan_preserves_future_auto_line(client, auth, db):
    """14.09.2026 baslangic, 1 hafta; 02.11.2026 otomatik satiri korunur."""
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "H-FUT", "due_date": "2026-11-15", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    future_id = _insert_plan_line(db, "H-FUT", wc_id, NOV, hours=12.0)

    client.post(
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 1, "work_center_ids": [wc_id], "replace_existing": True},
    )

    lines = client.get("/api/plan/lines", headers=auth, params={"start": BEFORE.isoformat()}).json()
    future = next((l for l in lines if l["id"] == future_id), None)
    assert future is not None, "Kasim otomatik satiri silinmemeli"
    assert future["week_start"] == NOV.isoformat()
    assert future["planned_hours"] == 12.0


def test_replan_preserves_before_other_wc_manual_forecast(client, auth, db):
    """Baslangictan onceki, baska merkezdeki ve manuel/forecast satirlari korunur."""
    wc_id = _setup(client, auth)
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [["CIRO-2", "Diger Hat", "E", 10, 4]],
    )
    wc2 = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "CIRO-2")["id"]
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["UCUZ", 20, "Op2", "CIRO-2", 3600]],
    )
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "H-KEEP", "due_date": "2026-09-30", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    order = next(o for o in client.get("/api/orders", headers=auth).json() if o["order_no"] == "H-KEEP")
    op = db.query(RoutingOperation).filter(RoutingOperation.item_id == order["item_id"], RoutingOperation.seq == 10).first()

    before_id = _insert_plan_line(db, "H-KEEP", wc_id, BEFORE)
    other_wc_id = _insert_plan_line(db, "H-KEEP", wc2, START)
    manual = client.post(
        "/api/plan/manual",
        headers=auth,
        json={"order_id": order["id"], "operation_id": op.id, "week_start": START.isoformat(), "planned_hours": 3, "planned_qty": 3},
    ).json()["id"]
    op2 = db.query(RoutingOperation).filter(RoutingOperation.item_id == order["item_id"], RoutingOperation.seq == 20).first()
    forecast_id = _insert_plan_line(db, "H-KEEP", wc_id, START, hours=2.0, qty=2.0, mode="forecast")
    assert op2 is not None

    client.post(
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 1, "work_center_ids": [wc_id], "replace_existing": True},
    )

    remaining_ids = {l["id"] for l in client.get("/api/plan/lines", headers=auth, params={"start": BEFORE.isoformat()}).json()}
    assert before_id in remaining_ids
    assert other_wc_id in remaining_ids
    assert manual in remaining_ids
    assert forecast_id in remaining_ids


def test_replan_upper_bound_week_preserved_inside_replaced(client, auth, db):
    """Ust sinirdaki haftanin satiri korunur; ufuk icindeki hedef auto satiri degisir."""
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "H-IN", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 5, "unit_price": 1},
    )
    inside_id = _insert_plan_line(db, "H-IN", wc_id, START, hours=99.0)
    upper_id = _insert_plan_line(db, "H-IN", wc_id, UPPER_BOUND, hours=7.0)

    client.post(
        "/api/plan/auto",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 2, "work_center_ids": [wc_id], "replace_existing": True},
    )

    lines = {l["id"]: l for l in client.get("/api/plan/lines", headers=auth, params={"start": START.isoformat()}).json()}
    assert upper_id in lines and lines[upper_id]["planned_hours"] == 7.0
    assert inside_id not in lines, "Ufuk icindeki eski auto satiri yenilenmeli"
    assert any(l["week_start"] == START.isoformat() and l["mode"] == "auto" and l["id"] != inside_id for l in lines.values())


def test_revision_apply_same_horizon_scope(client, auth, db):
    """Revizyon onayinda da ayni sinirli silme kapsami gecer."""
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "H-REV", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 10, "unit_price": 5},
    )
    order_id = r.json()["id"]
    future_id = _insert_plan_line(db, "H-REV", wc_id, NOV, hours=11.0)

    body = {
        "reason_codes": ["customer_postpone"],
        "note": "ufuk testi",
        "start_week": START.isoformat(),
        "weeks": 1,
        "work_center_ids": [wc_id],
        "mode": "due_date",
    }
    rev = client.post("/api/plan/revisions", headers=auth, json=body).json()
    client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={"entity_type": "order", "entity_id": order_id, "field": "revised_due_date", "new_value": "2026-09-25"},
    )
    client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)

    lines = client.get("/api/plan/lines", headers=auth, params={"start": BEFORE.isoformat()}).json()
    future = next((l for l in lines if l["id"] == future_id), None)
    assert future is not None
    assert future["week_start"] == NOV.isoformat()


def test_preflight_reports_replace_scope(client, auth, db):
    wc_id = _setup(client, auth)
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "H-PF", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 10, "unit_price": 1},
    )
    _insert_plan_line(db, "H-PF", wc_id, START, hours=4.0)
    _insert_plan_line(db, "H-PF", wc_id, NOV, hours=6.0)

    pf = client.post(
        "/api/plan/auto/preflight",
        headers=auth,
        json={"start_week": START.isoformat(), "weeks": 1, "work_center_ids": [wc_id], "replace_existing": True},
    ).json()
    scope = pf["replace_scope"]
    assert scope["horizon_start"] == START.isoformat()
    assert scope["horizon_end_inclusive"] == "2026-09-20"
    assert scope["lines_to_replace"] == 1
    assert scope["replace_modes"] == ["Otomatik"]
