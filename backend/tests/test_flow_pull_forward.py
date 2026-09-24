"""Akış (flow) yerleştirme modu ve tüm ufuk öne çekme taslağı."""
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


def test_flow_keeps_finish_early_and_aligns_predecessors(client, auth):
    a = _wc(client, auth, "FLW-A", 10)  # öncül: 200 sa/hafta
    b = _wc(client, auth, "FLW-B", 5)   # ardıl (darboğaz): 100 sa/hafta
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["FLW-P", "Akış ürünü", "FLW"]])
    _upload(client, auth, "routing", RT_HDR, [["FLW-P", 10, "Pres", "FLW-A", 3600, "FLW-P-10"], ["FLW-P", 20, "Montaj", "FLW-B", 3600, "FLW-P-20"]])
    _clear(client, auth)
    due = (WEEK + timedelta(weeks=5)).isoformat()
    client.post("/api/orders", headers=auth, json={"order_no": "FLW-X", "due_date": due, "item_code": "FLW-P", "quantity": 100})
    client.post("/api/orders", headers=auth, json={"order_no": "FLW-Y", "due_date": due, "item_code": "FLW-P", "quantity": 100})
    base = {"start_week": WEEK.isoformat(), "weeks": 6, "work_center_ids": [a, b], "replace_existing": True}
    # ASAP: öncüller 1. haftada (200 sa), ardıl 1.+2. hafta -> Y için 100 adet ara stok bir hafta bekler
    _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "placement": "asap"})
    asap = _lines(client, auth, [a, b])
    assert {l["week_start"] for l in asap if l["work_center_id"] == a} == {WEEK.isoformat()}
    # Akış: bitişler aynı kalır (maksimum çıktı), Y'nin öncülü ardılının haftasına yaslanır (ara stok sıfır)
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "placement": "flow"}).json()
    assert r["placement"] == "flow" and any(n["kind"] == "flow_aligned" for n in r["placement_notes"]), r["placement_notes"]
    flow = _lines(client, auth, [a, b])
    by = lambda rows, wc, no: sorted(l["week_start"] for l in rows if l["work_center_id"] == wc and l["order_no"] == no)  # noqa: E731
    assert by(flow, b, "FLW-X") == by(asap, b, "FLW-X") and by(flow, b, "FLW-Y") == by(asap, b, "FLW-Y")
    assert by(flow, a, "FLW-Y") == [(WEEK + timedelta(weeks=1)).isoformat()] and by(flow, a, "FLW-X") == [WEEK.isoformat()]
    assert round(sum(l["planned_hours"] for l in flow), 1) == round(sum(l["planned_hours"] for l in asap), 1) == 400.0
    # Boşalan erken kapasite: A'nın 1. haftasında 100 sa serbest; yeni bir sipariş oraya yerleşir
    client.post("/api/orders", headers=auth, json={"order_no": "FLW-Z", "due_date": due, "item_code": "FLW-P", "quantity": 100})
    _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "placement": "flow"})
    flow2 = _lines(client, auth, [a, b])
    week_a = {}
    for l in flow2:
        if l["work_center_id"] == a:
            week_a[l["week_start"]] = week_a.get(l["week_start"], 0) + l["planned_hours"]
    assert all(v <= 200 + 1e-6 for v in week_a.values())
    sched = {s["order_no"]: s for s in client.get("/api/plan/orders", headers=auth).json() if s["order_no"].startswith("FLW-")}
    assert all(s["plan_status"] == "on_time" for s in sched.values()), {k: v["plan_status"] for k, v in sched.items()}


def test_pull_forward_preview_and_draft_over_horizon(client, auth):
    a = _wc(client, auth, "PFW-A", 10)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["PFW-P", "Öne çekme ürünü", "PFW"]])
    _upload(client, auth, "routing", RT_HDR, [["PFW-P", 10, "Op", "PFW-A", 3600, "PFW-P-10"]])
    _clear(client, auth)
    for i, wk in enumerate((3, 3, 4)):
        client.post("/api/orders", headers=auth, json={"order_no": f"PFW-{i}", "due_date": (WEEK + timedelta(weeks=wk, days=4)).isoformat(), "item_code": "PFW-P", "quantity": 90})
    base = {"start_week": WEEK.isoformat(), "weeks": 6, "work_center_ids": [a], "replace_existing": True, "placement": "jit"}
    _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base)  # JIT: işler 4. ve 5. haftada, ilk 3 hafta atıl
    pv = client.post("/api/plan/pull-forward/preview", headers=auth, json=base).json()
    assert pv["idle_cells"] >= 3 and len(pv["moves"]) == 3 and pv["moved_hours"] >= 200  # satır bölünmüş olabilir; taşıma tüm kalanı alır
    assert {m["to_week"] for m in pv["moves"]} <= {WEEK.isoformat(), (WEEK + timedelta(weeks=1)).isoformat()}
    assert len({m["order_no"] for m in pv["moves"]}) == 3  # sipariş başına tek taşıma
    rev = client.post("/api/plan/pull-forward/draft", headers=auth, json=base)
    assert rev.status_code == 200, rev.text
    rev = rev.json()
    assert rev["status"] == "draft" and len(rev["changes"]) == 3 and all(c["field"] == "job_move" for c in rev["changes"])
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    assert calc.status_code == 200, calc.text
    assert client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth).status_code == 200
    lines = _lines(client, auth, [a])
    assert max(l["week_start"] for l in lines) <= (WEEK + timedelta(weeks=1)).isoformat()
    again = client.post("/api/plan/pull-forward/draft", headers=auth, json=base)
    assert again.status_code in (400, 409)  # öne çekilecek iş kalmadı
