"""Otomatik plan sonrasi plan raporu: uretim, listeleme, Excel."""
from datetime import timedelta

from tests.test_capacity_flow import _plan_with_ack, _upload
from tests.test_flow_pull_forward import RT_HDR, WEEK, _clear, _wc


def test_auto_plan_creates_report_and_exports(client, auth):
    a = _wc(client, auth, "RPT-A", 2)  # 40 sa/hafta (darboğaz): 200 sa talep, 3 hafta = 120 sa
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["RPT-P", "Rapor ürünü", "RPT"]])
    _upload(client, auth, "routing", RT_HDR, [["RPT-P", 10, "Pres", "RPT-A", 3600, "RPT-P-10"]])
    _clear(client, auth)
    client.post("/api/orders", headers=auth, json={"order_no": "RPT-X", "due_date": (WEEK + timedelta(weeks=2)).isoformat(), "item_code": "RPT-P", "quantity": 200})
    res = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 3, "work_center_ids": [a], "replace_existing": True, "use_overtime": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("report_id"), body
    rep = client.get(f"/api/plan/reports/{body['report_id']}", headers=auth).json()
    assert rep["kind"] == "auto" and rep["summary"]["planned_hours"] == 120
    codes = {r["code"]: r for r in rep["work_centers"]}
    assert codes["RPT-A"]["capacity_hours"] == 120 and codes["RPT-A"]["bottleneck"]
    assert rep["summary"]["unplanned_hours"] == 80 and any(f["code"] == "bottleneck" for f in rep["findings"])
    assert len(rep["weekly"]) == 3 and rep["orders"]["open_orders"] == 1
    lst = client.get("/api/plan/reports", headers=auth).json()
    assert lst[0]["id"] == body["report_id"]
    x = client.get(f"/api/plan/reports/{body['report_id']}.xlsx", headers=auth)
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_revision_approval_creates_report(client, auth):
    a = _wc(client, auth, "RPR-A", 2)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["RPR-P", "Rapor ürünü", "RPR"]])
    _upload(client, auth, "routing", RT_HDR, [["RPR-P", 10, "Pres", "RPR-A", 3600, "RPR-P-10"]])
    _clear(client, auth)
    o = client.post("/api/orders", headers=auth, json={"order_no": "RPR-X", "due_date": (WEEK + timedelta(weeks=2)).isoformat(), "item_code": "RPR-P", "quantity": 20}).json()
    rev = client.post("/api/plan/revisions", headers=auth, json={"reason_codes": ["customer_postpone"], "note": "rapor", "start_week": WEEK.isoformat(), "weeks": 3, "work_center_ids": [a], "mode": "due_date"}).json()
    client.post(f"/api/plan/revisions/{rev['id']}/changes", headers=auth, json={"entity_type": "order", "entity_id": o["id"], "field": "revised_due_date", "new_value": (WEEK + timedelta(weeks=1)).isoformat()})
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    ack = calc.json().get("missing_headcount_token")
    applied = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth, json={"missing_headcount_ack": ack} if ack else None)
    assert applied.status_code == 200, applied.text
    lst = client.get("/api/plan/reports", headers=auth).json()
    assert lst and lst[0]["kind"] == "revision" and lst[0]["revision_id"] == rev["id"]


def test_report_has_slips_overtime_and_decision_flow(client, auth):
    """Aşama 3: rapor kayan sipariş + FM ihtiyacı satırları taşır; onay/ret ucu 'onay bekliyor' işaretini çözer."""
    a = _wc(client, auth, "RPT-OT", 10)  # 200 sa; 230 sa talep -> 30 sa FM önerisi
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["RPT-OTP", "Ürün", "RPT"]])
    _upload(client, auth, "routing", RT_HDR, [["RPT-OTP", 10, "Op", "RPT-OT", 3600, "RPT-OTP-10"]])
    _clear(client, auth)
    due = (WEEK + timedelta(days=6)).isoformat()
    client.post("/api/orders", headers=auth, json={"order_no": "RO-1", "due_date": due, "item_code": "RPT-OTP", "quantity": 200})
    client.post("/api/orders", headers=auth, json={"order_no": "RO-2", "due_date": due, "item_code": "RPT-OTP", "quantity": 30})
    client.post("/api/orders", headers=auth, json={"order_no": "RO-3", "due_date": due, "item_code": "RPT-OTP", "quantity": 100})  # FM de yetmez -> kayar
    body = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [a], "replace_existing": True}).json()
    rep = client.get(f"/api/plan/reports/{body['report_id']}", headers=auth).json()
    s = rep["summary"]
    assert s["overtime_hours"] > 0 and s["overtime_cells"] == 1 and s["slip_orders"] == 1 and s["overtime_line_hours"] > 0
    ot = rep["overtime"][0]
    assert ot["work_center_code"] == "RPT-OT" and ot["pending"] is True and ot["approved"] is False and ot["person_hours_ytd"] > 0
    slip = next(r for r in rep["slips"] if r["order_no"] == "RO-3")
    assert slip["slipped_qty"] > 0 and slip["bottleneck"] == "RPT-OT" and slip["due_date"] == due and slip["days_late"] >= 0
    assert any(f["code"] == "overtime" for f in rep["findings"]) and any(f["code"] == "slip" for f in rep["findings"])
    x = client.get(f"/api/plan/reports/{body['report_id']}.xlsx", headers=auth)
    assert x.status_code == 200
    # Onay bekleyenler listesi -> onay -> liste boşalır, işaret kalkar
    pend = client.get("/api/wc-weeks/overtime-pending", headers=auth, params={"start": WEEK.isoformat(), "weeks": 2}).json()
    assert [p for p in pend if p["work_center_id"] == a]
    d = client.post("/api/wc-weeks/overtime-decision", headers=auth, json={"decision": "approve", "cells": [{"work_center_id": a, "week_start": WEEK.isoformat()}]})
    assert d.status_code == 200 and d.json()["updated"] == 1
    assert not [p for p in client.get("/api/wc-weeks/overtime-pending", headers=auth, params={"start": WEEK.isoformat(), "weeks": 2}).json() if p["work_center_id"] == a]
    wk = client.get(f"/api/workcenters/{a}/weeks", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1}).json()[0]
    assert wk["overtime_proposed"] is False and wk["overtime_capacity_hours"] > 0
    # Ret: yeniden plan öneri yazar, ret alanları temizler
    _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [a], "replace_existing": True})
    pend = [p for p in client.get("/api/wc-weeks/overtime-pending", headers=auth, params={"start": WEEK.isoformat(), "weeks": 2}).json() if p["work_center_id"] == a]
    if pend:  # onaylı FM zaten 30 sa: yeni öneri olmayabilir; varsa reddet ve temizlendiğini doğrula
        client.post("/api/wc-weeks/overtime-decision", headers=auth, json={"decision": "reject", "cells": [{"work_center_id": a, "week_start": pend[0]["week_start"]}]})
        assert not [p for p in client.get("/api/wc-weeks/overtime-pending", headers=auth, params={"start": WEEK.isoformat(), "weeks": 2}).json() if p["work_center_id"] == a]
