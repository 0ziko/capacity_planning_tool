"""Plan revizyonu: taslak, hesap, onay=uygula, ufuk kilidi."""

from datetime import date

from tests.test_revenue_modes import WEEK, _setup


def test_revision_calculate_apply_and_horizon_lock(client, auth):
    wc_id = _setup(client, auth)
    r = client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "RV-1", "due_date": "2026-09-18", "item_code": "UCUZ", "quantity": 20, "unit_price": 10},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["id"]

    body = {
        "reason_codes": ["customer_postpone"],
        "note": "Musteri 1 hafta oteledi",
        "start_week": WEEK.isoformat(),
        "weeks": 2,
        "work_center_ids": [wc_id],
        "mode": "due_date",
    }
    created = client.post("/api/plan/revisions", headers=auth, json=body)
    assert created.status_code == 200, created.text
    rev = created.json()
    assert rev["status"] == "draft" and rev["revision_no"].startswith("REV-")

    dup = client.post("/api/plan/revisions", headers=auth, json=body)
    assert dup.status_code == 409

    ch = client.post(
        f"/api/plan/revisions/{rev['id']}/changes",
        headers=auth,
        json={"entity_type": "order", "entity_id": order_id, "field": "revised_due_date", "new_value": "2026-09-25"},
    )
    assert ch.status_code == 200, ch.text
    assert ch.json()["changes"][0]["new_value"] == "2026-09-25"

    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    calc_body = calc.json()
    assert calc_body["status"] == "calculated"
    assert calc_body["compare"]["proposed"]["line_count"] >= 1

    live = client.get("/api/orders", headers=auth).json()
    o = next(x for x in live if x["id"] == order_id)
    assert o["revised_due_date"] in (None, "")

    applied = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert applied.json()["approved_by"]

    live = client.get("/api/orders", headers=auth).json()
    o = next(x for x in live if x["id"] == order_id)
    assert o["revised_due_date"] == "2026-09-25"

    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": [wc_id]}).json()
    assert lines and all(l.get("revision_id") == rev["id"] for l in lines)

    again = client.post("/api/plan/revisions", headers=auth, json=body)
    assert again.status_code == 200, again.text


def test_revision_cancel_frees_horizon(client, auth):
    wc_id = _setup(client, auth)
    body = {
        "reason_codes": ["other"],
        "note": "iptal deneme",
        "start_week": WEEK.isoformat(),
        "weeks": 3,
        "work_center_ids": [wc_id],
    }
    a = client.post("/api/plan/revisions", headers=auth, json=body)
    assert a.status_code == 200, a.text
    rid = a.json()["id"]
    c = client.post(f"/api/plan/revisions/{rid}/cancel", headers=auth)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    b = client.post("/api/plan/revisions", headers=auth, json=body)
    assert b.status_code == 200
