"""Revizyon karşılaştırması: sipariş bazlı önce/sonra farkı ve toplu girdi geri çekme."""
from datetime import date, timedelta
from types import SimpleNamespace

from app.services.plan_revisions import build_order_diffs


def _row(oid, no, end, status, late, due="2026-11-01", customer="M", item="P"):
    return {"order_id": oid, "order_no": no, "position_no": "", "customer": customer, "item_code": item, "quantity": 10,
            "due_date": due, "planned_end": end, "plan_status": status, "lateness_days": late}


def test_build_order_diffs_flags_and_summary():
    base = [
        _row(1, "A", "2026-10-30", "on_time", -2),   # talep: 2 hafta öne -> karşılanır
        _row(2, "B", "2026-10-30", "on_time", -2),   # talep: öne -> karşılanamaz (geç)
        _row(3, "C", "2026-10-20", "on_time", -12),  # talep dışı: ötelenir ve yeni geç
        _row(4, "D", "2026-10-20", "on_time", -12),  # talep dışı: ötelenir ama zamanında
        _row(5, "E", "2026-10-20", "on_time", -12),  # değişmez
        _row(6, "F", "2026-10-20", "on_time", -12),  # öneride plansız (kayıp) -> pushed
    ]
    prop = [
        _row(1, "A", "2026-10-15", "on_time", -3),
        _row(2, "B", "2026-10-22", "late", 4),
        _row(3, "C", "2026-11-05", "late", 4),
        _row(4, "D", "2026-10-25", "on_time", -7),
        _row(5, "E", "2026-10-20", "on_time", -12),
        _row(6, "F", None, "unplanned", None),
    ]
    changes = [
        SimpleNamespace(id=11, entity_type="order", entity_id=1, field="revised_due_date", new_value="2026-10-18", old_value=""),
        SimpleNamespace(id=12, entity_type="order", entity_id=2, field="revised_due_date", new_value="2026-10-18", old_value=""),
        SimpleNamespace(id=13, entity_type="wc_week", entity_id=0, field="headcount", new_value="5", old_value="", extra_key="1|2026-10-05"),
    ]
    diffs, summary = build_order_diffs(base, prop, changes, bumped_orders=["D"])
    by = {d.order_no: d for d in diffs}
    assert by["A"].requested and by["A"].met is True and by["A"].change_id == 11
    assert by["A"].due_before == date(2026, 11, 1) and by["A"].due_after == date(2026, 10, 18)
    assert by["A"].delta_days == -15 and by["A"].pulled_forward
    assert by["B"].requested and by["B"].met is False and by["B"].status_after == "late"
    assert by["C"].pushed and by["C"].newly_late and by["C"].delta_days == 16 and not by["C"].requested
    assert by["C"].due_before == date(2026, 11, 1) and by["C"].due_after == date(2026, 11, 1)  # bitiş − gecikme
    assert by["D"].pushed and not by["D"].newly_late and by["D"].bumped
    assert by["E"].delta_days == 0 and not by["E"].pushed and not by["E"].pulled_forward
    assert by["F"].pushed and by["F"].end_after is None and by["F"].status_after == "unplanned"
    assert not by["B"].newly_late  # talep edilen sipariş 'karşılanamayan'dır, 'yeni geç' sayılmaz
    assert summary.model_dump() == {"requested": 2, "met": 1, "unmet": 1, "pushed": 3, "newly_late": 1, "pulled_forward": 2, "unchanged": 1, "bumped": 1}
    # Sıra: önce karşılanamayan talep, sonra karşılanan, sonra yeni geç, sonra en çok kayan
    assert [d.order_no for d in diffs][:4] == ["B", "A", "C", "D"]


def test_build_order_diffs_job_move_uses_effective_due():
    base = [_row(1, "A", "2026-10-30", "on_time", -2)]
    prop = [_row(1, "A", "2026-10-10", "on_time", -22)]
    ch = [SimpleNamespace(id=5, entity_type="order", entity_id=1, field="job_move", new_value='{"start_date":"2026-10-05"}', old_value="")]
    diffs, summary = build_order_diffs(base, prop, ch, [])
    d = diffs[0]
    assert d.change_kind == "job_move" and d.met is True and d.due_after == date(2026, 11, 1)
    assert summary.requested == 1 and summary.met == 1


def _wc(client, auth, code):
    from tests.test_capacity_flow import _upload, _weekly_staffing

    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [[code, code, "E", 10, 4]])
    _upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"], [[code, "G", "0,1,2,3,4", "08:00", "18:00", 10, 4]])
    _weekly_staffing(client, auth, [[code, 10, 4, 5]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["RDF-P", "Diff ürün", "RDF"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["RDF-P", 10, "Op", code, 3600]])
    return next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == code)["id"]


def test_api_compare_has_order_diffs_and_bulk_remove(client, auth):
    from tests.test_capacity_flow import _plan_with_ack

    week = date(2026, 9, 7)
    wc_id = _wc(client, auth, "RDF-WC")
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    a = client.post("/api/orders", headers=auth, json={"order_no": "RDF-A", "due_date": (week + timedelta(weeks=1)).isoformat(), "item_code": "RDF-P", "quantity": 150}).json()
    b = client.post("/api/orders", headers=auth, json={"order_no": "RDF-B", "due_date": (week + timedelta(weeks=3)).isoformat(), "item_code": "RDF-P", "quantity": 150}).json()
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": week.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "replace_existing": True, "use_overtime": False})
    assert r.status_code == 200, r.text
    rev = client.post("/api/plan/revisions", headers=auth, json={"reason_codes": ["vip_pull_in"], "note": "", "start_week": week.isoformat(), "weeks": 4, "work_center_ids": [wc_id], "mode": "due_date", "use_overtime": False}).json()  # sözleşme testi: otomatik FM kapalı
    # B'nin terminini 1. haftaya çek: A ile 200 saatlik haftaya sığmaz -> biri ötelenir
    ch = client.post(f"/api/plan/revisions/{rev['id']}/changes/bulk", headers=auth, json={"changes": [
        {"entity_type": "order", "entity_id": b["id"], "field": "revised_due_date", "new_value": (week + timedelta(days=4)).isoformat()},
        {"entity_type": "order", "entity_id": a["id"], "field": "revised_due_date", "new_value": (week + timedelta(days=4)).isoformat()},
    ]}).json()
    assert len(ch["changes"]) == 2
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    cmp_ = calc.json()["compare"]
    assert cmp_["diff_summary"]["requested"] == 2
    diffs = {d["order_no"]: d for d in cmp_["order_diffs"]}
    assert set(diffs) == {"RDF-A", "RDF-B"}
    assert all(d["requested"] and d["change_id"] for d in diffs.values())
    assert all(d["end_before"] and d["status_before"] for d in diffs.values())
    assert cmp_["diff_summary"]["met"] + cmp_["diff_summary"]["unmet"] == 2
    assert cmp_["diff_summary"]["unmet"] >= 1  # 300 saat tek haftaya sığmaz
    unmet_ids = [d["change_id"] for d in diffs.values() if d["met"] is False]
    # Toplu geri çekme: yanlış id reddedilir, doğru id'ler tek işlemde çıkar ve taslak 'draft' olur
    bad = client.post(f"/api/plan/revisions/{rev['id']}/changes/remove", headers=auth, json={"change_ids": [999999]})
    assert bad.status_code in (400, 404, 409)
    out = client.post(f"/api/plan/revisions/{rev['id']}/changes/remove", headers=auth, json={"change_ids": unmet_ids}).json()
    assert out["status"] == "draft" and len(out["changes"]) == 2 - len(unmet_ids)
    assert out["compare"] is None
    assert any("toplu silindi" in (e.get("detail") or "") for e in out["events"])
    calc2 = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth).json()
    assert calc2["compare"]["diff_summary"]["unmet"] == 0
