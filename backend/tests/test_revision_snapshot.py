"""FAZ 06 / T1: revizyon onay = hesaplanan snapshot; fingerprint 409."""

from tests.test_revenue_modes import WEEK, _setup


def _create_rev_with_due(client, auth, wc_id, order_id, new_due="2026-09-25"):
    body = {
        "reason_codes": ["customer_postpone"],
        "note": "test",
        "start_week": WEEK.isoformat(),
        "weeks": 2,
        "work_center_ids": [wc_id],
        "mode": "due_date",
    }
    created = client.post("/api/plan/revisions", headers=auth, json=body)
    assert created.status_code == 200, created.text
    rev = created.json()
    ch = client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={"entity_type": "order", "entity_id": order_id, "field": "revised_due_date", "new_value": new_due},
    )
    assert ch.status_code == 200, ch.text
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    return calc.json()


def _apply_snapshot_lines(client, auth, rev_id):
    from app.db.session import SessionLocal
    from app.models.planning import PlanRevisionSnapshot
    import json

    s = SessionLocal()
    try:
        snap = (
            s.query(PlanRevisionSnapshot)
            .filter(PlanRevisionSnapshot.revision_id == rev_id, PlanRevisionSnapshot.kind == "apply")
            .one()
        )
        return json.loads(snap.payload_json or "{}").get("plan_lines") or []
    finally:
        s.close()


def _line_key(row):
    return (
        row.get("order_id"),
        row.get("operation_id"),
        row.get("work_center_id"),
        str(row.get("week_start", ""))[:10],
    )


def test_approve_uses_snapshot_not_resimulate(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RS-1", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 20, "unit_price": 10},
    )
    order_id = r.json()["id"]
    rev = _create_rev_with_due(client, auth, wc_id, order_id)
    assert rev["input_fingerprint"]
    assert rev["compare"]["proposed"]["line_count"] >= 1
    expected = _apply_snapshot_lines(client, auth, rev["id"])
    assert expected
    ap = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert ap.status_code == 200, ap.text
    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json()
    assert lines
    assert all(l.get("revision_id") == rev["id"] for l in lines)
    applied = sorted(
        [
            {
                "order_id": l["order_id"],
                "operation_id": l["operation_id"],
                "work_center_id": l["work_center_id"],
                "week_start": str(l["week_start"])[:10],
                "planned_hours": round(float(l["planned_hours"]), 6),
                "planned_qty": round(float(l.get("planned_qty") or 0), 6),
            }
            for l in lines
        ],
        key=_line_key,
    )
    snap_sorted = sorted(
        [
            {
                "order_id": x["order_id"],
                "operation_id": x["operation_id"],
                "work_center_id": x["work_center_id"],
                "week_start": str(x["week_start"])[:10],
                "planned_hours": round(float(x["planned_hours"]), 6),
                "planned_qty": round(float(x.get("planned_qty") or 0), 6),
            }
            for x in expected
        ],
        key=_line_key,
    )
    assert applied == snap_sorted


def test_stale_fingerprint_409(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RS-2", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 20, "unit_price": 10},
    )
    order_id = r.json()["id"]
    rev = _create_rev_with_due(client, auth, wc_id, order_id)
    before_lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json()
    o0 = next(x for x in client.get("/api/orders", headers=auth).json() if x["id"] == order_id)
    upd = client.put(
        f"/api/orders/{order_id}",
        headers=auth,
        json={
            "order_no": "RS-2",
            "due_date": "2026-09-18",
            "item_code": o0["item_code"],
            "quantity": 25,
            "customer": "T",
        },
    )
    assert upd.status_code == 200, upd.text
    ap = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert ap.status_code == 409
    assert "degisti" in ap.json()["detail"].lower() or "değişti" in ap.json()["detail"].lower()
    after = client.get("/api/orders", headers=auth).json()
    o = next(x for x in after if x["id"] == order_id)
    assert o["revised_due_date"] in (None, "")
    assert client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json() == before_lines


def test_double_approve_409(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RS-3", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 10, "unit_price": 10},
    )
    order_id = r.json()["id"]
    rev = _create_rev_with_due(client, auth, wc_id, order_id)
    assert client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth).status_code == 200
    lines_count = len(client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json())
    again = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert again.status_code == 409
    assert "uygulandi" in again.json()["detail"].lower() or "uygulandı" in again.json()["detail"].lower()
    assert len(client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json()) == lines_count


def test_calculate_failure_does_not_mutate_live(client, auth, db):
    from app.models import Item

    wc_id = _setup(client, auth)
    db.add(Item(code="NOROUTE", name="No route", product_group="test"))
    db.commit()
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RS-NR", "due_date": "2026-09-18", "item_code": "NOROUTE", "quantity": 5, "unit_price": 1},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["id"]
    body = {
        "reason_codes": ["customer_postpone"],
        "note": "fail calc",
        "start_week": WEEK.isoformat(),
        "weeks": 2,
        "work_center_ids": [wc_id],
        "mode": "due_date",
    }
    rev = client.post("/api/plan/revisions", headers=auth, json=body).json()
    ch = client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={"entity_type": "order", "entity_id": order_id, "field": "revised_due_date", "new_value": "2026-09-25"},
    )
    assert ch.status_code == 200, ch.text
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 400
    o = next(x for x in client.get("/api/orders", headers=auth).json() if x["id"] == order_id)
    assert o["revised_due_date"] in (None, "")
    assert client.get(f"/api/plan/revisions/{rev['id']}", headers=auth).json()["status"] == "draft"


def test_wc_week_change_after_calculate_409(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RS-4", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 10, "unit_price": 10},
    )
    order_id = r.json()["id"]
    rev = _create_rev_with_due(client, auth, wc_id, order_id)
    client.put(
        f"/api/workcenters/{wc_id}/weeks/{WEEK.isoformat()}",
        headers=auth,
        json={"headcount": 99, "efficient_hours_per_person": 8, "working_days": 5},
    )
    ap = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert ap.status_code == 409
