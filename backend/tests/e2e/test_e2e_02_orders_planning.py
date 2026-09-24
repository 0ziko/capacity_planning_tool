"""E2E-02: Sipariş → iş gücü ihtiyacı → ön kontrol → otomatik plan → yük/çıktı/ciro/karşılaştırma/değerlendirme."""
from datetime import timedelta

from tests.e2e.conftest import WEEK, is_xlsx, plan_auto, upload, xlsx

ORDER_HDR = ["Sipariş No", "Poz No", "Müşteri", "Termin", "Stok Kodu", "Miktar", "Birim Fiyat"]


def _orders(client, auth, rows):
    return upload(client, auth, "orders", ORDER_HDR, rows)


def _load(client, auth, wc_ids, weeks=8):
    return client.get("/api/plan/load", headers=auth, params={"start": WEEK.isoformat(), "weeks": weeks, "work_center_ids": wc_ids}).json()


def test_orders_import_preview_and_requirements(client, auth, master, sc):
    """Sipariş Excel önizleme + import, API ile sipariş CRUD; ihtiyaç 18.000 adet × 120 sn = 600 saat."""
    content = xlsx(ORDER_HDR, [["E2E-S1", "10", "ABC Otel", (WEEK + timedelta(weeks=3)).isoformat(), "E2E-MAM", 9000, 100],
                               ["E2E-S2", "10", "XYZ Restoran", (WEEK + timedelta(weeks=5)).isoformat(), "E2E-MAM", 9000, 120]])
    pv = client.post("/api/imports/orders/preview", headers=auth, files={"file": ("orders.xlsx", content, "application/octet-stream")})
    sc.step("Sipariş önizleme (yazmadan)", pv.status_code == 200, keys=list(pv.json().keys())[:8] if isinstance(pv.json(), dict) else len(pv.json()))
    sc.step("Önizleme Excel raporu", is_xlsx(client.post("/api/imports/orders/preview.xlsx", headers=auth, files={"file": ("orders.xlsx", content, "application/octet-stream")})))
    sc.step("Önizleme veri yazmadı", all(o["order_no"] not in ("E2E-S1", "E2E-S2") for o in client.get("/api/orders", headers=auth).json()))

    res = client.post("/api/imports/orders", headers=auth, files={"file": ("orders.xlsx", content, "application/octet-stream")}).json()
    sc.step("Sipariş import", res.get("errors") in ([], None), created=res.get("created"), updated=res.get("updated"))
    orders = {o["order_no"]: o for o in client.get("/api/orders", headers=auth).json()}
    sc.step("İki sipariş açık", {"E2E-S1", "E2E-S2"} <= set(orders), count=len(orders))

    req = client.post("/api/requirements", headers=auth, json={}).json()
    sc.step("Toplam ihtiyaç 600 saat", round(req["total_hours"], 1) == 600.0, total=req["total_hours"])
    by_wc = {r.get("work_center_code"): r for r in req.get("work_centers", req.get("by_work_center", []))}
    if by_wc:
        sc.step("E2E-PRS ihtiyacı 400 saat (50+30 sn)", round(by_wc["E2E-PRS"].get("hours", by_wc["E2E-PRS"].get("required_hours", 0)), 1) == 400.0)
        sc.step("E2E-MNT ihtiyacı 200 saat (40 sn)", round(by_wc["E2E-MNT"].get("hours", by_wc["E2E-MNT"].get("required_hours", 0)), 1) == 200.0)
    else:
        sc.info("İhtiyaç yanıt anahtarları", keys=list(req.keys()))
    sc.step("Sipariş analizi", client.get("/api/orders/analysis", headers=auth).status_code == 200)

    # API CRUD + doğrulama
    bad = client.post("/api/orders", headers=auth, json={"order_no": "E2E-BAD", "due_date": WEEK.isoformat(), "item_code": "E2E-MAM", "quantity": 0})
    sc.step("Miktar 0 reddedilir (422)", bad.status_code == 422)
    unknown = client.post("/api/orders", headers=auth, json={"order_no": "E2E-BAD", "due_date": WEEK.isoformat(), "item_code": "YOK-123", "quantity": 5})
    sc.step("Tanımsız stok kodu reddedilir", unknown.status_code in (400, 404, 422), status=unknown.status_code)
    o = client.post("/api/orders", headers=auth, json={"order_no": "E2E-S3", "customer": "API", "due_date": (WEEK + timedelta(weeks=2)).isoformat(), "item_code": "E2E-MAM", "quantity": 360, "unit_price": 50}).json()
    sc.step("API ile sipariş oluşturuldu", o.get("id") is not None, id=o.get("id"))
    u = client.put(f"/api/orders/{o['id']}", headers=auth, json={"order_no": "E2E-S3", "customer": "API", "due_date": (WEEK + timedelta(weeks=2)).isoformat(), "item_code": "E2E-MAM", "quantity": 720, "unit_price": 50})
    sc.step("Sipariş güncellendi (720)", u.status_code == 200 and u.json()["quantity"] == 720)
    st = client.patch(f"/api/orders/{o['id']}/status", headers=auth, params={"status": "closed"})
    sc.step("Sipariş kapatıldı", st.status_code == 200, status=st.status_code)
    sc.step("Kapalı sipariş açık listede değil", all(x["order_no"] != "E2E-S3" for x in client.get("/api/orders", headers=auth).json()))
    sc.step("Sipariş silindi", client.delete(f"/api/orders/{o['id']}", headers=auth).status_code in (200, 204))


def test_preflight_and_auto_plan_due_date(client, auth, master, sc):
    """Ön kontrol → termine göre otomatik plan; PRS ilk hafta %100 dolu, toplam 600 saat yerleşir; yük/çıktı/sipariş takvimi/Gantt/Excel."""
    _orders(client, auth, [["E2E-S1", "10", "ABC", (WEEK + timedelta(weeks=3)).isoformat(), "E2E-MAM", 9000, 100],
                           ["E2E-S2", "10", "XYZ", (WEEK + timedelta(weeks=5)).isoformat(), "E2E-MAM", 9000, 120]])
    req = {"start_week": WEEK.isoformat(), "weeks": 8, "work_center_ids": master["wc_ids"], "replace_existing": True}
    pre = client.post("/api/plan/auto/preflight", headers=auth, json=req)
    sc.step("Ön kontrol yanıtı", pre.status_code == 200, keys=list(pre.json().keys())[:12])
    sc.info("Ön kontrol özeti", missing_headcount=bool(pre.json().get("missing_headcount_token")), no_routing=pre.json().get("no_routing_count", pre.json().get("missing_routing_count")))

    out = plan_auto(client, auth, **req)
    sc.step("Otomatik plan çalıştı", out.get("created", 0) > 0 and out.get("unplanned") == [], created=out.get("created"), unplanned=out.get("unplanned"))

    load = _load(client, auth, master["wc_ids"])
    prs = next(l for l in load if l["work_center_code"] == "E2E-PRS")
    w0 = prs["weeks"][0]
    sc.step("PRS 1. hafta 200 saat / %100", w0["planned_hours"] == 200 and w0["utilization"] == 1.0, planned=w0["planned_hours"], util=w0["utilization"])
    total = round(sum(w["planned_hours"] for l in load for w in l["weeks"]), 1)
    sc.step("Toplam planlanan 600 saat", total == 600.0, total=total)
    sc.step("Hiçbir hafta kapasiteyi aşmıyor", all(w["planned_hours"] <= w["capacity_hours"] + 1e-6 for l in load for w in l["weeks"]))

    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).json()
    sc.step("Plan satırları", len(lines) > 0, count=len(lines), modes=sorted({l.get("mode") for l in lines}))
    det = client.get("/api/plan/load/detail", headers=auth, params={"work_center_id": master["prs_id"], "week_start": WEEK.isoformat()})
    sc.step("Yük detayı", det.status_code == 200, keys=list(det.json().keys())[:8])
    sc.step("Yük detayı Excel", is_xlsx(client.get("/api/plan/load/detail.xlsx", headers=auth, params={"work_center_id": master["prs_id"], "week_start": WEEK.isoformat()})))
    wo = client.get("/api/plan/weekly-output", headers=auth, params={"week_start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]})
    sc.step("Haftalık çıktı", wo.status_code == 200, keys=list(wo.json().keys())[:8])
    sc.step("Haftalık çıktı Excel", is_xlsx(client.get("/api/plan/weekly-output.xlsx", headers=auth, params={"week_start": WEEK.isoformat()})))
    sched = client.get("/api/plan/orders", headers=auth).json()
    row = next((s for s in sched if s.get("order_no") == "E2E-S1"), None)
    sc.step("Sipariş takvimi E2E-S1", row is not None, keys=list(row.keys())[:14] if row else None)
    sc.step("Sipariş takvimi Excel", is_xlsx(client.get("/api/plan/orders.xlsx", headers=auth)))
    sc.step("Plan Excel", is_xlsx(client.get("/api/plan/export.xlsx", headers=auth, params={"start": WEEK.isoformat()})))
    g = client.get("/api/plan/gantt", headers=auth, params={"work_center_id": master["prs_id"], "start": WEEK.isoformat(), "end": (WEEK + timedelta(days=27)).isoformat()}).json()
    sc.step("Gantt çubukları", len(g.get("bars", [])) >= 1, bars=len(g.get("bars", [])), wc=g.get("work_center_code"))
    sc.step("Ciro görünümü", client.get("/api/plan/revenue", headers=auth, params={"start": WEEK.isoformat(), "weeks": 8}).status_code == 200)
    # Plan temizleme
    sc.step("Plan satırları temizlendi", client.delete("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).status_code in (200, 204))
    sc.step("Temizlik sonrası satır yok", client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).json() == [])


def test_revenue_mode_compare_evaluation(client, auth, master, sc):
    """Ciro modu 1 haftalık dar ufukta pahalı siparişi seçer; termin modu ile karşılaştırma ve plan kalite değerlendirmesi."""
    # PRS kapasitesi 200 saat/hafta; ucuz 7200 adet = 160 saat (PRS) / pahalı 3600 adet = 80 saat (PRS)
    _orders(client, auth, [["E2E-UCUZ", "10", "A", (WEEK + timedelta(weeks=1)).isoformat(), "E2E-MAM", 7200, 1],
                           ["E2E-PAHALI", "10", "B", (WEEK + timedelta(weeks=1)).isoformat(), "E2E-MAM", 3600, 1000]])
    out = plan_auto(client, auth, start_week=WEEK.isoformat(), weeks=1, work_center_ids=master["wc_ids"], replace_existing=True, mode="revenue")
    sc.step("Ciro modu plan", out.get("created", 0) > 0, created=out.get("created"), unplanned=[u.get("order_no") if isinstance(u, dict) else u for u in out.get("unplanned", [])])
    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).json()
    by_order = {}
    for l in lines:
        by_order[l.get("order_no")] = by_order.get(l.get("order_no"), 0) + l["planned_hours"]
    sc.step("Pahalı sipariş ufka alındı", by_order.get("E2E-PAHALI", 0) > 0, hours=by_order)
    rev = client.get("/api/plan/revenue", headers=auth, params={"start": WEEK.isoformat(), "weeks": 1})
    sc.step("Ciro raporu", rev.status_code == 200, keys=list(rev.json().keys())[:8] if isinstance(rev.json(), dict) else len(rev.json()))

    cmp_ = client.post("/api/plan/compare", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": master["wc_ids"]})
    sc.step("Termin/ciro karşılaştırma", cmp_.status_code == 200, keys=list(cmp_.json().keys())[:8])
    ev = client.post("/api/plan/evaluation", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": master["wc_ids"], "benchmark_kind": "live"})
    sc.step("Plan değerlendirme (FAZ 12)", ev.status_code == 200 and ev.json().get("input_fingerprint"), fingerprint=str(ev.json().get("input_fingerprint"))[:16])
    ev2 = client.post("/api/plan/evaluation", headers=auth, json={"start_week": WEEK.isoformat(), "weeks": 1, "work_center_ids": master["wc_ids"], "benchmark_kind": "live"})
    sc.step("Değerlendirme deterministik", ev2.json().get("input_fingerprint") == ev.json().get("input_fingerprint"))


def test_manual_line_forecast_and_cleanup(client, auth, master, sc):
    """Manuel plan satırı ekle/güncelle/sil; terminleme → tahmin (forecast) kaydı → listele → sil."""
    _orders(client, auth, [["E2E-MAN", "10", "M", (WEEK + timedelta(weeks=4)).isoformat(), "E2E-MAM", 720, 10]])
    order = next(o for o in client.get("/api/orders", headers=auth).json() if o["order_no"] == "E2E-MAN")
    plan_auto(client, auth, start_week=WEEK.isoformat(), weeks=2, work_center_ids=master["wc_ids"], replace_existing=True)
    auto_lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).json()
    op_id = next(l["operation_id"] for l in auto_lines if l["work_center_id"] == master["prs_id"])
    client.delete("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]})

    m = client.post("/api/plan/manual", headers=auth, json={"order_id": order["id"], "operation_id": op_id, "week_start": (WEEK + timedelta(weeks=1)).isoformat(), "planned_hours": 5})
    sc.step("Manuel satır eklendi", m.status_code in (200, 201), status=m.status_code, body=m.text[:200] if m.status_code >= 400 else None)
    line = m.json()
    p = client.patch(f"/api/plan/lines/{line['id']}", headers=auth, params={"planned_hours": 6})
    sc.step("Manuel satır güncellendi (6 saat)", p.status_code == 200, status=p.status_code)
    load = _load(client, auth, [master["prs_id"]], weeks=2)
    sc.step("Yük tablosunda 2. hafta 6 saat", round(load[0]["weeks"][1]["planned_hours"], 1) == 6.0, weeks=[w["planned_hours"] for w in load[0]["weeks"]])
    sc.step("Manuel satır silindi", client.delete(f"/api/plan/lines/{line['id']}", headers=auth).status_code in (200, 204))

    lt = client.post("/api/plan/leadtime", headers=auth, json={"item_code": "E2E-MAM", "quantity": 720, "start": (WEEK + timedelta(weeks=3)).isoformat()}).json()
    # 720 adet × (50+30+40) sn = 24 saat
    sc.step("Terminleme 3 adım / 24 saat", len(lt["steps"]) == 3 and round(lt["total_hours"], 1) == 24.0, total=lt["total_hours"], end=lt.get("end"))
    f = client.post("/api/plan/leadtime/forecast", headers=auth, json={"item_code": "E2E-MAM", "quantity": 720, "label": "E2E tahmin", "steps": lt["steps"]})
    sc.step("Tahmin kaydı oluşturuldu", f.status_code == 200, status=f.status_code, body=f.text[:200] if f.status_code >= 400 else None)
    fl = client.get("/api/plan/forecast", headers=auth).json()
    sc.step("Tahmin listede", any("E2E" in str(x) for x in fl), count=len(fl))
    sc.step("Tahminler temizlendi", client.delete("/api/plan/forecast", headers=auth).status_code in (200, 204))
    sc.step("Tahmin listesi boş", client.get("/api/plan/forecast", headers=auth).json() == [])


def test_no_routing_diagnostics(client, auth, master, sc, db):
    """Rotasız ürün: sipariş girişi/aktarımı kapıda reddedilir; eski (kapı öncesi) rotasız sipariş varsa ön kontrol uyarır, eksik rota Excel'i iner, plan açık hata ile durur; sipariş kapatılınca plan çalışır."""
    from app.models import Item, Order

    due = (WEEK + timedelta(weeks=2)).isoformat()
    # 1) Excel aktarımı: rotasız ürün satırı hata ile reddedilir, rotalı satır aktarılır; önizleme de aynı kodu listeler
    content = xlsx(ORDER_HDR, [["E2E-NR", "10", "N", due, "E2E-MAM2", 100, 10], ["E2E-OK", "10", "N", due, "E2E-MAM", 360, 10]])
    pv = client.post("/api/imports/orders/preview", headers=auth, files={"file": ("orders.xlsx", content, "application/octet-stream")}).json()
    sc.step("Önizleme rotasız ürünü ayrı listeler", pv.get("no_routing_item_codes") == ["E2E-MAM2"] and any("Rota tanimi eksik" in e for e in pv["parse_errors"]), no_routing=pv.get("no_routing_item_codes"))
    res = client.post("/api/imports/orders", headers=auth, files={"file": ("orders.xlsx", content, "application/octet-stream")}).json()
    sc.step("Aktarım: rotasız satır reddedildi, rotalı satır aktarıldı", res["inserted"] == 1 and len(res["errors"]) == 1 and "Rota tanimi eksik" in res["errors"][0], result={k: res[k] for k in ("inserted", "updated", "errors")})
    nos = {o["order_no"] for o in client.get("/api/orders", headers=auth).json()}
    sc.step("Rotasız sipariş sisteme girmedi", "E2E-NR" not in nos and "E2E-OK" in nos)
    # 2) API girişi de aynı kapıdan geçer; tanımsız stok kodu artık aktarımda otomatik açılmaz
    api_r = client.post("/api/orders", headers=auth, json={"order_no": "E2E-NR2", "due_date": due, "item_code": "E2E-MAM2", "quantity": 5})
    sc.step("API sipariş girişi rotasız üründe 400", api_r.status_code == 400 and "Rota tan" in api_r.text, detail=api_r.json().get("detail"))
    unk = client.post("/api/imports/orders", headers=auth, files={"file": ("o.xlsx", xlsx(ORDER_HDR, [["E2E-UNK", "10", "N", due, "E2E-YOK-999", 5, 1]]), "application/octet-stream")}).json()
    sc.step("Tanımsız stok kodu aktarımda otomatik açılmıyor", unk["inserted"] == 0 and unk["errors"], errors=unk["errors"][:1])
    sc.step("Stok kartı yaratılmadı", db.query(Item).filter(Item.code == "E2E-YOK-999").first() is None)
    # 3) Eski veri: kapıdan önce girilmiş rotasız sipariş (doğrudan yazılır) — ön kontrol ve plan davranışı
    mam2 = db.query(Item).filter(Item.code == "E2E-MAM2").one()
    db.add(Order(order_no="E2E-NR", item_id=mam2.id, quantity=100, unit_price=10, due_date=WEEK + timedelta(weeks=2), status="open"))
    db.commit()
    req = {"start_week": WEEK.isoformat(), "weeks": 4, "work_center_ids": master["wc_ids"], "replace_existing": True}
    pre = client.post("/api/plan/auto/preflight", headers=auth, json=req)
    sc.step("Ön kontrol yanıtı", pre.status_code == 200, keys=list(pre.json().keys()))
    sc.step("Ön kontrol rotasız siparişi görüyor", "E2E-NR" in pre.text or "E2E-MAM2" in pre.text)
    sc.step("Eksik rota Excel'i", is_xlsx(client.post("/api/plan/auto/preflight/no-routing.xlsx", headers=auth, json=req)))
    blocked = client.post("/api/plan/auto", headers=auth, json=req)
    sc.step("Rotasız açık sipariş varken plan 400 ile durur", blocked.status_code == 400 and "E2E-MAM2" in blocked.text, detail=blocked.json().get("detail") if blocked.status_code == 400 else blocked.status_code)
    sc.step("Engellenen plan satır yazmadı", client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).json() == [])
    nr = next(o for o in client.get("/api/orders", headers=auth).json() if o["order_no"] == "E2E-NR")
    client.patch(f"/api/orders/{nr['id']}/status", headers=auth, params={"status": "closed"})
    out = plan_auto(client, auth, **req)
    sc.step("Rotasız sipariş kapatılınca plan çalışır", out.get("created", 0) > 0 and out.get("unplanned") == [], created=out.get("created"))
    lines = client.get("/api/plan/lines", headers=auth, params={"start": WEEK.isoformat(), "work_center_ids": master["wc_ids"]}).json()
    sc.step("Rotalı sipariş planlandı", any(l.get("order_no") == "E2E-OK" for l in lines), count=len(lines))
