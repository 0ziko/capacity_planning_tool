"""Aşama 2: dengeli yerleşim (yetim parça yok), fazla mesai katmanı, kayan adet (chain / defer)."""
from datetime import timedelta

from tests.test_capacity_flow import _plan_with_ack, _upload
from tests.test_flow_pull_forward import RT_HDR, WEEK, _clear, _lines, _wc

ITEM_HDR = ["Stok Kodu", "Stok Adı", "Ürün Grubu"]
BOM_HDR = ["Stok Kodu", "Bileşen Kodu", "Bileşen Adı", "Miktar", "Kaynak Yarımamül", "Reçete Sıra"]


def _assembly(client, auth, tag):
    """Mamul 6{tag}: gövde (hızlı hat) + kapak (yavaş hat) -> montaj. 1 hafta ufuk, hedef 1. hafta."""
    a = _wc(client, auth, f"BAL-G{tag}", 10)  # gövde: 200 sa/hafta
    b = _wc(client, auth, f"BAL-K{tag}", 2)   # kapak: 40 sa/hafta (darboğaz)
    m = _wc(client, auth, f"BAL-M{tag}", 10)  # montaj: 200 sa/hafta
    fg, g, k = f"6008{tag}", f"5008{tag}-01", f"5008{tag}-02"  # mamul 6xxxxx, montaj parçaları 5xxxxx-nn (reçete sıra 0)
    _upload(client, auth, "items", ITEM_HDR, [[fg, "Mamul", "BAL"], [g, "Gövde", "BAL"], [k, "Kapak", "BAL"]])
    _upload(client, auth, "bom", BOM_HDR, [[fg, g, "Gövde", 1, g, 0], [fg, k, "Kapak", 1, k, 0]])
    _upload(client, auth, "routing", RT_HDR, [[g, 10, "Pres", f"BAL-G{tag}", 3600, g],
                                             [k, 10, "Kesim", f"BAL-K{tag}", 3600, k],
                                             [fg, 10, "Montaj", f"BAL-M{tag}", 3600, f"{fg}-50"]])
    return a, b, m


def _qty(lines, wc):
    return round(sum(l["planned_qty"] for l in lines if l["work_center_id"] == wc), 1)


def test_balanced_assembly_no_orphan_parts_chain_and_defer(client, auth):
    a, b, m = _assembly(client, auth, "01")
    _clear(client, auth)
    # 200 adet, termin 1. hafta içinde: kapak 40 adet/hafta -> hedefte 40 adet dengeli; fazla mesai kapalı
    client.post("/api/orders", headers=auth, json={"order_no": "BAL-1", "due_date": (WEEK + timedelta(days=6)).isoformat(), "item_code": "600801", "quantity": 200})
    base = {"start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": [a, b, m], "replace_existing": True, "use_overtime": False, "prep_fill": False}  # dengeli yerleşim sözleşmesi; hazırlık ayrı testte
    # chain: kalan 160 adet sonraki haftalara dengeli yerleşir, satırlar 'slip' etiketli
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "slip_mode": "chain"}).json()
    lines = _lines(client, auth, [a, b, m])
    wk1 = [l for l in lines if l["week_start"] == WEEK.isoformat()]
    # hedef haftada kapak ve montaj 40 (darboğaz kapak); gövde 40 hedef + kalan 160 kayan adet (boş kapasite, slip etiketli)
    assert _qty(wk1, b) == 40 and _qty(wk1, m) == 40 and _qty(wk1, a) == 40, [(l["work_center_id"], l["tag"], l["planned_qty"]) for l in wk1]  # gövde montajdan önde birikmez
    assert _qty(lines, a) == _qty(lines, b) == _qty(lines, m) == 160.0  # ufukta 4 hafta × 40: parça ve montaj aynı adette, yetim gövde yok
    # kapak ve montajın hedef sonrası satırları 'slip'; gövde satırları akış hizalamasıyla montaja yaslandığı için etiketi karışık olabilir
    assert all(l["tag"] == "slip" for l in lines if l["week_start"] != WEEK.isoformat() and l["work_center_id"] in (b, m))
    note = next(n for n in r["placement_notes"] if n["kind"] == "slip" and n["label"] == "BAL-1")
    assert note["target_qty"] == 40 and note["bottleneck"] == "BAL-K01" and note["slip_mode"] == "chain"
    # defer: kalan adet plana yazılmaz, termin_kaydi olarak raporlanır
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "slip_mode": "defer"}).json()
    lines = _lines(client, auth, [a, b, m])
    assert _qty(lines, a) == _qty(lines, b) == _qty(lines, m) == 40.0 and all(l["week_start"] == WEEK.isoformat() for l in lines)
    deferred = [u for u in r["unplanned"] if u["reason"] == "termin_kaydi"]
    assert deferred and round(sum(u["hours"] for u in deferred)) == 3 * 120  # 3 hafta daha sığan 120 adet × 3 op
    assert "tahmini bitiş" in deferred[0]["detail"]
    note = next(n for n in r["placement_notes"] if n["kind"] == "slip" and n["label"] == "BAL-1")
    assert note["qty"] == 160 and note["est_finish_week"] == (WEEK + timedelta(weeks=3)).isoformat()


def test_overtime_tier_closes_gap_and_writes_proposal(client, auth):
    """230 sa talep / 200 sa normal: 30 sa fazla mesaiyle hedef tutar; öneri haftalık iş gücüne 'onay bekliyor' yazılır."""
    wc = _wc(client, auth, "BAL-OT", 10)  # 10 kişi × 4 sa × 5 gün = 200; verim 0,4
    _upload(client, auth, "items", ITEM_HDR, [["BAL-OTP", "Ürün", "BAL"]])
    _upload(client, auth, "routing", RT_HDR, [["BAL-OTP", 10, "Op", "BAL-OT", 3600, "BAL-OTP-10"]])
    _clear(client, auth)
    due = (WEEK + timedelta(days=6)).isoformat()
    client.post("/api/orders", headers=auth, json={"order_no": "OT-1", "due_date": due, "item_code": "BAL-OTP", "quantity": 200})
    client.post("/api/orders", headers=auth, json={"order_no": "OT-2", "due_date": due, "item_code": "BAL-OTP", "quantity": 30})
    base = {"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc], "replace_existing": True}
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    assert r["unplanned"] == [] and len(r["overtime_proposals"]) == 1, r
    p = r["overtime_proposals"][0]
    # 30 sa açık: kişi-gün 2,5 × 0,4 = 1 sa -> 6 kişi × 5 gün
    assert p["work_center_code"] == "BAL-OT" and p["hours"] == 30 and p["weekday_persons"] == 6 and p["weekday_days"] == 5 and p["weekend_days"] == 0
    lines = _lines(client, auth, [wc])
    assert all(l["week_start"] == WEEK.isoformat() for l in lines)
    assert [l["order_no"] for l in lines if l["tag"] == "overtime"] == ["OT-2"]
    wk = client.get(f"/api/workcenters/{wc}/weeks", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1}).json()[0]
    assert wk["overtime_proposed"] is True and wk["overtime_headcount"] == 6 and wk["overtime_capacity_hours"] == 30 and wk["capacity_hours"] == 230
    # Yeniden plan: eski öneri taban kapasite sayılmaz, yeniden hesaplanır (aynı sonuç, çift sayım yok)
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    assert len(r["overtime_proposals"]) == 1 and r["overtime_proposals"][0]["hours"] == 30
    # Onay: haftalık iş gücünde işaret kalkar; sonraki plan öneriyi onaylı sayar, yeni öneri gerekmez
    ok = client.put(f"/api/workcenters/{wc}/weeks/{WEEK.isoformat()}", headers=auth,
                    json={"headcount": 10, "efficient_hours_per_person": 4, "working_days": 5, "overtime_headcount": 6, "overtime_days": 5, "overtime_hours_per_person": 2.5, "overtime_proposed": False})
    assert ok.status_code == 200 and ok.json()["overtime_proposed"] is False
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    assert r["overtime_proposals"] == [] and r["unplanned"] == []
    # Fazla mesai kapalı: 30 sa termin sonrasına kayar (chain) ve slip etiketi alır
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={**base, "use_overtime": False}).json()
    lines = _lines(client, auth, [wc])
    wk = client.get(f"/api/workcenters/{wc}/weeks", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1}).json()[0]
    assert wk["overtime_headcount"] == 6  # onaylı fazla mesai korunur, hâlâ 230 sa
    assert r["overtime_proposals"] == [] and all(l["week_start"] == WEEK.isoformat() for l in lines)


def test_weekend_overtime_used_when_weekday_insufficient(client, auth):
    """Hafta içi FM (10 kişi × 1 sa × 5 gün = 50) yetmezse hafta sonu (8,5 × 0,4 = 3,4 sa/kişi-gün) devreye girer."""
    wc = _wc(client, auth, "BAL-WE", 10)
    _upload(client, auth, "items", ITEM_HDR, [["BAL-WEP", "Ürün", "BAL"]])
    _upload(client, auth, "routing", RT_HDR, [["BAL-WEP", 10, "Op", "BAL-WE", 3600, "BAL-WEP-10"]])
    _clear(client, auth)
    client.post("/api/orders", headers=auth, json={"order_no": "WE-1", "due_date": (WEEK + timedelta(days=6)).isoformat(), "item_code": "BAL-WEP", "quantity": 280})
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": [wc], "replace_existing": True}).json()
    assert r["unplanned"] == [], r["unplanned"]
    p = r["overtime_proposals"][0]
    # 80 sa açık: hafta içi 50 + hafta sonu 30 -> 10 kişi × 1 gün × 3,4 = 34 ≥ 30
    assert p["hours"] == 80 and p["weekday_persons"] == 10 and p["weekday_days"] == 5 and p["weekend_days"] == 1 and p["weekend_persons"] == 9
    wk = client.get(f"/api/workcenters/{wc}/weeks", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1}).json()[0]
    assert wk["weekend_overtime_headcount"] == 9 and wk["weekend_overtime_days"] == 1 and wk["overtime_proposed"] is True


def test_overtime_rules_overdue_lateness_and_extra_headcount(client, auth):
    """Fazla mesai kural seti: (1) termini geçmiş sipariş mesai alır, (2) hedefi tam kurtarmasa da gecikmeyi azaltan mesai kabul,
    (3) ek kişi (komşu merkez) havuzu büyütür."""
    wc = _wc(client, auth, "BAL-RL", 4)  # 4 kişi × 4 sa × 5 gün = 80 sa/hafta; verim 0,4
    _upload(client, auth, "items", ITEM_HDR, [["BAL-RLP", "Ürün", "BAL"]])
    _upload(client, auth, "routing", RT_HDR, [["BAL-RLP", 10, "Op", "BAL-RL", 3600, "BAL-RLP-10"]])
    _clear(client, auth)
    base = {"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [wc], "replace_existing": True, "prep_fill": False}
    # (1) termini ufuk öncesi: 100 sa iş, 80 sa normal -> 20 sa mesaiyle 1. haftada biter (mesai havuzu 4 kişi: 4×(12,5+17)×0,4×0,78 ≈ 37 sa)
    client.post("/api/orders", headers=auth, json={"order_no": "OD-1", "due_date": (WEEK - timedelta(days=10)).isoformat(), "item_code": "BAL-RLP", "quantity": 100})
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    lines = _lines(client, auth, [wc])
    assert all(l["week_start"] == WEEK.isoformat() for l in lines) and r["overtime_proposals"] and r["overtime_proposals"][0]["hours"] == 20
    note = next(n for n in r["placement_notes"] if n["kind"] == "slip" and n["label"] == "OD-1")
    assert note["overdue"] and note["overtime_saved_weeks"] == 1  # mesai adedi değil bitişi öne aldı (2. hafta -> 1. hafta)
    # (2) gecikmeyi azaltan mesai: 2 haftalık ufukta 200 sa iş (hedef 1. hafta). Normal: 80+80=160 -> 2. haftaya sarkar, 40 sa ufka sığmaz.
    #     Mesai (~37 sa/hafta) hedefi kurtarmaz ama ufukta biten adedi artırır -> kabul, 'overtime' etiketli satırlar.
    _clear(client, auth)
    client.post("/api/orders", headers=auth, json={"order_no": "LT-1", "due_date": (WEEK + timedelta(days=6)).isoformat(), "item_code": "BAL-RLP", "quantity": 200})
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    lines = _lines(client, auth, [wc])
    planned = sum(l["planned_qty"] for l in lines)
    assert planned > 160 and any(l["tag"] == "overtime" for l in lines) and len(r["overtime_proposals"]) == 2
    note = next(n for n in r["placement_notes"] if n["kind"] == "slip" and n["label"] == "LT-1")
    assert note["overtime_extra_qty"] > 0 and note["qty"] > 0
    # (3) ek kişi: haftaya 6 ek kişi -> havuz 10 kişi; 200 sa artık ufka sığar, mesai tamamen açığı kapatır
    for wk in (WEEK, WEEK + timedelta(weeks=1)):
        ok = client.put(f"/api/workcenters/{wc}/weeks/{wk.isoformat()}", headers=auth, json={"headcount": 4, "efficient_hours_per_person": 4, "working_days": 5, "overtime_extra_headcount": 6})
        assert ok.status_code == 200 and ok.json()["overtime_extra_headcount"] == 6
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json=base).json()
    assert r["unplanned"] == [] and sum(p["hours"] for p in r["overtime_proposals"]) > 40  # 1. haftada havuz ~92 sa: hedefte 80+92, kalan 28 2. hafta
    assert all(p["weekday_persons"] <= 10 and p["weekend_persons"] <= 10 and p["person_hours"] <= 100 / 4.33 + 1e-6 for p in r["overtime_proposals"])


def test_overtime_only_where_short_in_chain(client, auth):
    """Zincirde yalnız darboğaz merkeze mesai yazılır: A (40 sa) kısa, B (200 sa) bol -> mesai sadece A'da, B'de öneri yok."""
    a = _wc(client, auth, "CH-A", 2)   # 2 kişi × 4 sa × 5 = 40 sa
    b = _wc(client, auth, "CH-B", 10)  # 200 sa
    _upload(client, auth, "items", ITEM_HDR, [["CH-P", "Ürün", "CH"]])
    _upload(client, auth, "routing", RT_HDR, [["CH-P", 10, "Kes", "CH-A", 3600, "CH-P-10"], ["CH-P", 20, "Montaj", "CH-B", 3600, "CH-P-20"]])
    _clear(client, auth)
    client.post("/api/orders", headers=auth, json={"order_no": "CH-1", "due_date": (WEEK + timedelta(days=6)).isoformat(), "item_code": "CH-P", "quantity": 55})
    r = _plan_with_ack(client, "/api/plan/auto", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 2, "work_center_ids": [a, b], "replace_existing": True, "prep_fill": False}).json()
    props = {p["work_center_code"]: p["hours"] for p in r["overtime_proposals"]}
    assert set(props) == {"CH-A"} and props["CH-A"] == 15, props  # yalnız kısa olan merkeze, ihtiyaç kadar
    lines = _lines(client, auth, [a, b])
    assert all(l["week_start"] == WEEK.isoformat() for l in lines) and r["unplanned"] == []
