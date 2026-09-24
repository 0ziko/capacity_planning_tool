"""E2E-03: Senaryo matrisi (operasyon geçiş kuralları) → terminleme; günlük detaylı çizelge → segment kilidi → Gantt."""
from datetime import date, datetime, timedelta

from tests.e2e.conftest import WEEK, plan_auto, upload, wc_by_code, weekly_staffing

FMT = "%Y-%m-%d %H:%M"


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT)


def test_scenario_rules_change_leadtime(client, auth, master, sc):
    """Grup kuralı 'Kesim→Büküm 5 çevrim sonra' iç içe başlangıç verir ve toplam termini kısaltır; Excel ile kural yükleme ve silme."""
    groups = client.get("/api/scenarios/groups", headers=auth).json()
    g = next((x for x in groups if x["product_group"] == "E2EGRP"), None)
    sc.step("Ürün grubu akışı bulundu", g is not None and g["operations"] == ["Kesim", "Büküm", "Montaj"], ops=g["operations"] if g else None)
    flow = client.get("/api/scenarios/flow", headers=auth, params={"product_group": "E2EGRP"}).json()
    sc.step("Akış düğümleri", [n["name"] for n in flow["nodes"]] == ["Kesim", "Büküm", "Montaj"], transitions=len(flow["transitions"]))
    sc.step("Varsayılan geçiş: önceki bitince", all(t["effective"]["source"] == "default" for t in flow["transitions"]))

    start = (WEEK + timedelta(weeks=6)).isoformat()  # boş hafta

    def lt():
        r = client.post("/api/plan/leadtime", headers=auth, json={"item_code": "E2E-MAM", "quantity": 1000, "start": start})
        assert r.status_code == 200, r.text
        return r.json()

    base = lt()
    s0, e0, s1 = _dt(base["steps"][0]["start"]), _dt(base["steps"][0]["end"]), _dt(base["steps"][1]["start"])
    sc.step("Kuralsız: Büküm, Kesim bitince başlar", s1 >= e0, kesim_end=e0.isoformat(), bukum_start=s1.isoformat())

    r = client.put("/api/scenarios/rules", headers=auth, json={"scope": "group", "product_group": "E2EGRP", "from_op": "Kesim", "to_op": "Büküm", "rule": "cycles", "lag_cycles": 5})
    sc.step("Grup kuralı kaydedildi", r.status_code == 200 and "5 çevrim" in r.json()["description"], desc=r.json().get("description"))
    over = lt()
    s1b, e1b = _dt(over["steps"][1]["start"]), _dt(over["steps"][1]["end"])
    sc.step("Kurallı: Büküm iç içe başladı", s0 <= s1b < e0, bukum_start=s1b.isoformat())
    sc.step("Büküm, Kesim'den önce bitmez", e1b >= e0)
    sc.step("Toplam termin kısaldı", _dt(over["end"]) < _dt(base["end"]), base_end=base["end"], rule_end=over["end"])

    res = upload(client, auth, "op_rules", ["Ürün Grubu", "Stok Kodu", "Önceki Operasyon", "Sonraki Operasyon", "Kural", "Çevrim Sayısı"],
                 [["E2EGRP", "", "Büküm", "Montaj", "Çevrim", 10]])
    sc.step("Excel ile kural yüklendi", res.get("errors") in ([], None))
    rules = client.get("/api/scenarios/rules", headers=auth, params={"product_group": "E2EGRP"}).json()
    sc.step("İki kural listede", len(rules) == 2, count=len(rules))
    flow2 = client.get("/api/scenarios/flow", headers=auth, params={"product_group": "E2EGRP"}).json()
    sc.step("Akışta etkin kaynak grup", all(t["effective"]["source"] == "group" for t in flow2["transitions"]))
    sc.step("Senaryo stok listesi", client.get("/api/scenarios/items", headers=auth, params={"product_group": "E2EGRP"}).status_code == 200)
    for rule in rules:
        assert client.delete(f"/api/scenarios/rules/{rule['id']}", headers=auth).status_code == 204
    sc.step("Kurallar temizlendi", client.get("/api/scenarios/rules", headers=auth, params={"product_group": "E2EGRP"}).json() == [])


def test_daily_detailed_schedule_segments_lock_gantt(client, auth, sc):
    """Makine bazlı rota + vardiya → günlük detaylı çizelge segment üretir; segment kilitlenir, yeniden planda korunur, Gantt gösterir."""
    r = client.post("/api/workcenters", headers=auth, json={"code": "E2E-D11", "name": "E2E Günlük Pilot", "is_planned": True, "capacity_source": "work_center"})
    assert r.status_code in (200, 201, 400, 409), r.text
    wc = wc_by_code(client, auth, "E2E-D11")
    client.post(f"/api/workcenters/{wc['id']}/shifts", headers=auth, json={"name": "G", "weekdays": "0,1,2,3,4", "start_time": "08:00", "end_time": "12:00", "headcount": 2, "efficient_hours_per_person": 4})
    m = client.post(f"/api/workcenters/{wc['id']}/machines", headers=auth, json={"code": "E2E-D11-M1", "name": "Pilot Makine"})
    assert m.status_code in (200, 201, 400, 409), m.text
    weekly_staffing(client, auth, [("E2E-D11", 2, 4, 5)])
    upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["E2E-DLY", "E2E Günlük Ürün", "E2EDLY"]])
    upload(client, auth, "routing",
           ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Setup (dk)", "Yarımamül Kodu", "Birincil Makine Kodu", "Süre Türü", "Çevrim Başına Adet", "Setup Makine (dk)"],
           [["E2E-DLY", 10, "Pres", "E2E-D11", 600, 15, "E2E-DLY-10", "E2E-D11-M1", "machine_seconds_per_cycle", 1, 15]])
    o = client.post("/api/orders", headers=auth, json={"order_no": "E2E-DLY-1", "due_date": (WEEK + timedelta(weeks=3)).isoformat(), "item_code": "E2E-DLY", "quantity": 10,
                                                        "material_status": "ready", "material_ready_date": (date.today() - timedelta(days=7)).isoformat()})
    sc.step("Sipariş oluşturuldu", o.status_code in (200, 201), status=o.status_code)

    req = dict(start_week=WEEK.isoformat(), weeks=2, work_center_ids=[wc["id"]], replace_existing=True, planning_granularity="daily_detailed")
    out = plan_auto(client, auth, **req)
    ds = out.get("daily_schedule") or {}
    sc.step("Günlük çizelge üretildi", ds.get("segments_created", 0) >= 1, segments=ds.get("segments_created"), skipped=ds.get("skipped"), remaining=ds.get("remaining_qty"), version=ds.get("version_id"))

    end = (WEEK + timedelta(days=13)).isoformat()
    segs = client.get("/api/plan/segments", headers=auth, params={"work_center_id": wc["id"], "start": WEEK.isoformat(), "end": end}).json()
    sc.step("Segment listesi", len(segs) >= 1, count=len(segs), first=({k: segs[0].get(k) for k in ("id", "start_at", "end_at", "machine_code", "segment_kind", "good_qty", "crew_size", "is_locked")} if segs else None))
    sc.step("Makine segmentleri çakışmıyor", all(a["end_at"] <= b["start_at"] for a, b in zip(sorted(segs, key=lambda s: s["start_at"]), sorted(segs, key=lambda s: s["start_at"])[1:]) if a["machine_id"] == b["machine_id"]))
    first = segs[0]
    lock = client.patch(f"/api/plan/segments/{first['id']}/lock", headers=auth, params={"locked": True})
    sc.step("Segment kilitlendi", lock.status_code == 200 and lock.json().get("is_locked") is True, status=lock.status_code)

    out2 = plan_auto(client, auth, **req)
    segs2 = client.get("/api/plan/segments", headers=auth, params={"work_center_id": wc["id"], "start": WEEK.isoformat(), "end": end}).json()
    kept = next((s for s in segs2 if s.get("is_locked")), None)
    sc.step("Yeniden planda kilitli segment korundu", kept is not None and kept.get("start_at") == first.get("start_at") and kept.get("end_at") == first.get("end_at"),
            kept_start=kept.get("start_at") if kept else None, orig_start=first.get("start_at"), segments=out2.get("daily_schedule", {}).get("segments_created"))
    unlock = client.patch(f"/api/plan/segments/{kept['id']}/lock", headers=auth, params={"locked": False})
    sc.step("Kilit kaldırıldı", unlock.status_code == 200 and unlock.json().get("is_locked") is False)

    g = client.get("/api/plan/gantt", headers=auth, params={"work_center_id": wc["id"], "start": WEEK.isoformat(), "end": end}).json()
    bar = next((b for b in g.get("bars", []) if b.get("order_no") == "E2E-DLY-1"), None)
    sc.step("Gantt çubuğu sipariş için var", bar is not None, bars=len(g.get("bars", [])), status=bar.get("status") if bar else None, remaining=bar.get("remaining_qty") if bar else None)
    gf = client.get("/api/plan/gantt", headers=auth, params={"work_center_id": wc["id"], "start": WEEK.isoformat(), "end": end, "selection_kind": "item", "selection_codes": "E2E-DLY"}).json()
    sc.step("Gantt stok filtresi tarihleri değiştirmez", gf.get("bars") == g.get("bars"))
    gz = client.get("/api/plan/gantt", headers=auth, params={"work_center_id": wc["id"], "start": WEEK.isoformat(), "end": end, "selection_kind": "item", "selection_codes": "YOK"}).json()
    sc.step("Eşleşmeyen filtre boş", gz.get("bars") == [])
