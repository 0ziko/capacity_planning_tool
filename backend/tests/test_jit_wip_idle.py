"""JIT (termine yakın) yerleştirme, yarımamül ara stok sınırı ve atıl kapasite önerisi."""
from datetime import date, timedelta

from tests.test_capacity_flow import _plan_with_ack, _upload, _weekly_staffing

WEEK = date(2026, 9, 7)
WC_HDR = ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"]
SH_HDR = ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"]
RT_HDR = ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"]


def _wc(client, auth, code, people):
    _upload(client, auth, "workcenters", WC_HDR, [[code, code, "E", 10, 4]])
    _upload(client, auth, "shifts", SH_HDR, [[code, "G", "0,1,2,3,4", "08:00", "18:00", people, 4]])
    _weekly_staffing(client, auth, [[code, people, 4, 5]])
    return next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == code)["id"]


def _clear(client, auth):
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})


def _lines(client, auth, wc_ids):
    return client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": wc_ids}).json()


def test_jit_places_near_due_and_keeps_precedence(client, auth):
    a = _wc(client, auth, "JIT-A", 10)  # 200 sa/hafta
    b = _wc(client, auth, "JIT-B", 10)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["JIT-P", "JIT ürün", "JIT"]])
    _upload(client, auth, "routing", RT_HDR, [["JIT-P", 10, "Kesim", "JIT-A", 3600, "JIT-P-10"], ["JIT-P", 20, "Montaj", "JIT-B", 3600, "JIT-P-20"]])
    _clear(client, auth)
    due = WEEK + timedelta(weeks=3, days=4)  # 4. haftanın Cuması -> hedef (−2 gün) Çarşamba, 4. hafta
    o = client.post("/api/orders", headers=auth, json={"order_no": "JIT-1", "due_date": due.isoformat(), "item_code": "JIT-P", "quantity": 50}).json()
    assert o["id"]
    base = {"start_week": WEEK.isoformat(), "weeks": 6, "work_center_ids": [a, b], "replace_existing": True}
    # ASAP: her şey 1. haftada
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "placement": "asap"}).json()
    assert r["placement"] == "asap" and r["placement_notes"] == []
    assert {l["week_start"] for l in _lines(client, auth, [a, b])} == {WEEK.isoformat()}
    sched = next(s for s in client.get("/api/plan/orders", headers=auth).json() if s["order_no"] == "JIT-1")
    assert sched["slack_days"] is not None and sched["slack_days"] >= 20  # çok erken üretim görünür
    # JIT: son operasyon hedef haftaya, öncül aynı veya önceki haftaya; toplam saat aynı
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "placement": "jit", "jit_buffer_days": 2}).json()
    assert r["placement"] == "jit" and any(n["kind"] == "jit_moved" for n in r["placement_notes"]), r["placement_notes"]
    lines = _lines(client, auth, [a, b])
    target = (WEEK + timedelta(weeks=3)).isoformat()
    wk_b = {l["week_start"] for l in lines if l["work_center_id"] == b}
    wk_a = {l["week_start"] for l in lines if l["work_center_id"] == a}
    assert wk_b == {target} and max(wk_a) <= target and min(wk_a) >= WEEK.isoformat()
    assert round(sum(l["planned_hours"] for l in lines), 1) == 100.0
    sched = next(s for s in client.get("/api/plan/orders", headers=auth).json() if s["order_no"] == "JIT-1")
    assert sched["plan_status"] == "on_time" and 0 <= sched["slack_days"] <= 6
    # Kapasite hedef haftada dolarsa erken kalan iş kalır (ötelenmez, sessizce kaybolmaz)
    client.post("/api/orders", headers=auth, json={"order_no": "JIT-2", "due_date": due.isoformat(), "item_code": "JIT-P", "quantity": 200})
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "placement": "jit"}).json()
    lines = _lines(client, auth, [a, b])
    assert round(sum(l["planned_hours"] for l in lines), 1) == 500.0
    assert max(l["week_start"] for l in lines) <= target and r["unplanned"] == []


def test_wip_cap_delays_predecessor(client, auth, db):
    a = _wc(client, auth, "WIP-A", 10)  # 200 sa
    b = _wc(client, auth, "WIP-B", 2)   # 40 sa: bitiş işi 100 adet -> 3 hafta
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu", "Azami Ara Stok (adet)"],
            [["600777", "Mamul", "WIPT", ""], ["500777-01", "Yarımamül", "WIPT", 20]])
    _upload(client, auth, "bom", ["Stok Kodu", "Bileşen Kodu", "Bileşen Adı", "Miktar", "Kaynak Yarımamül", "Reçete Sıra"], [["600777", "500777-01", "WIP", 1, "500777-01", 0]])
    _upload(client, auth, "routing", RT_HDR, [["500777-01", 10, "Pres", "WIP-A", 3600, "500777-01"], ["600777", 10, "Montaj", "WIP-B", 3600, "600777-50"]])
    it = next(i for i in client.get("/api/items", headers=auth, params={"q": "500777-01"}).json() if i["code"] == "500777-01")
    assert it["max_wip_qty"] == 20
    _clear(client, auth)
    client.post("/api/orders", headers=auth, json={"order_no": "WIP-1", "due_date": (WEEK + timedelta(weeks=5)).isoformat(), "item_code": "600777", "quantity": 100})
    base = {"start_week": WEEK.isoformat(), "weeks": 6, "work_center_ids": [a, b], "replace_existing": True}
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    lines = _lines(client, auth, [a, b])
    pred = sorted([l for l in lines if l["work_center_id"] == a], key=lambda l: l["week_start"])
    succ = sorted([l for l in lines if l["work_center_id"] == b], key=lambda l: l["week_start"])
    # Ardıl haftada 40 adet; öncül her haftada ardıl kümülatifinden en fazla 20 adet önde
    cum_p = cum_s = 0.0
    weeks = sorted({l["week_start"] for l in lines})
    for wk in weeks:
        cum_p += sum(l["planned_qty"] for l in pred if l["week_start"] == wk)
        cum_s += sum(l["planned_qty"] for l in succ if l["week_start"] == wk)
        assert cum_p - cum_s <= 20 + 1e-6, (wk, cum_p, cum_s)
    assert round(sum(l["planned_qty"] for l in pred), 1) == 100.0 and len(pred) >= 2
    assert not [n for n in r["placement_notes"] if n["kind"] == "wip_cap_violation"]
    # Sınırı 0'a çekmek (kaldırmak) sonra API ile 10'a: PATCH çalışır
    p = client.patch(f"/api/items/{it['id']}/wip-limits", headers=auth, json={"max_wip_qty": 10, "max_wip_days": 7})
    assert p.status_code == 200 and p.json()["max_wip_qty"] == 10 and p.json()["max_wip_days"] == 7


def test_idle_suggestions_and_pull_forward_revision(client, auth):
    a = _wc(client, auth, "IDL-A", 10)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["IDL-P", "Atıl ürün", "IDL"]])
    _upload(client, auth, "routing", RT_HDR, [["IDL-P", 10, "Op", "IDL-A", 3600, "IDL-P-10"]])
    _clear(client, auth)
    client.post("/api/orders", headers=auth, json={"order_no": "IDL-1", "due_date": (WEEK + timedelta(weeks=3, days=4)).isoformat(), "item_code": "IDL-P", "quantity": 60})
    client.post("/api/orders", headers=auth, json={"order_no": "IDL-2", "due_date": (WEEK + timedelta(weeks=2, days=4)).isoformat(), "item_code": "IDL-P", "quantity": 30,
                                                     "material_status": "expected", "material_ready_date": (WEEK + timedelta(weeks=2)).isoformat()})
    base = {"start_week": WEEK.isoformat(), "weeks": 6, "work_center_ids": [a], "replace_existing": True, "placement": "jit"}
    _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base)
    s = client.get("/api/plan/idle-suggestions", headers=auth, params={"work_center_id": a, "week_start": WEEK.isoformat()}).json()
    assert s["idle_hours"] == 200 and s["capacity_hours"] == 200
    rows = {r["order_no"]: r for r in s["rows"]}
    assert rows["IDL-1"]["eligible"] and rows["IDL-1"]["fits"] and rows["IDL-1"]["from_week"] == (WEEK + timedelta(weeks=3)).isoformat()
    assert rows["IDL-2"]["eligible"] is False and "malzeme" in rows["IDL-2"]["blocker"]
    assert s["eligible_count"] == 1 and s["fits_hours"] == 60
    # Tek tıkla iş taşıma: revizyon + job_move + hesap + onay -> iş 1. haftada
    rev = client.post("/api/plan/revisions", headers=auth, json={"reason_codes": ["other"], "note": "atıl kapasite", "start_week": WEEK.isoformat(), "weeks": 6, "work_center_ids": [a], "mode": "due_date", "placement": "jit"}).json()
    assert rev["placement"] == "jit"
    ch = client.post(f"/api/plan/revisions/{rev['id']}/changes/bulk", headers=auth, json={"changes": [
        {"entity_type": "order", "entity_id": rows["IDL-1"]["order_id"], "extra_key": "IDL-P", "field": "job_move",
         "new_value": '{"item_code":"IDL-P","start_date":"%s","qty_mode":"remaining","quantity":null}' % WEEK.isoformat()}]})
    assert ch.status_code == 200
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    assert client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth).status_code == 200
    lines = [l for l in _lines(client, auth, [a]) if l["order_no"] == "IDL-1"]
    assert lines and all(l["week_start"] == WEEK.isoformat() for l in lines)
    s2 = client.get("/api/plan/idle-suggestions", headers=auth, params={"work_center_id": a, "week_start": WEEK.isoformat()}).json()
    assert s2["idle_hours"] == 140 and all(r["order_no"] != "IDL-1" for r in s2["rows"])
