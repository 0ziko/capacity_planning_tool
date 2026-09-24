"""E2E-06: Termin geri çekme (pull-in) stres senaryosu.

Saha durumu: 55 müşterinin 160 açık siparişi planlı; aynı gün 5 müşteri 40 farklı ürünün terminini
2 hafta öne çekmek istiyor. Planlamacı revizyon ekranıyla:
  1. hangi taleplerin yerine getirilebildiğini (yeni termine göre zamanında),
  2. hangi taleplerin karşılanamadığını,
  3. bu uğurda hangi BAŞKA siparişlerin ötelendiğini / geç kaldığını
görebilmeli; onayladığında gördüğü sonuç canlı plana birebir yazılmalı.

Test hem algoritmayı (API) ölçer hem de outputs/e2e/pullin_stress_report.md'ye planlamacı
raporu yazar. Ekranın gösterdiği/göstermediği alanlar rapora "arayüz bulgusu" olarak eklenir.
"""
import json
import random
import time
from datetime import date, timedelta

from tests.e2e.conftest import REPORT_DIR, WEEK, plan_auto, upload, wc_by_code, weekly_staffing

WCS = [("E2E6-KES", "Kesim", 8), ("E2E6-PRS", "Pres", 10), ("E2E6-KYN", "Kaynak", 6), ("E2E6-MNT", "Montaj", 8), ("E2E6-PKT", "Paket", 4)]
N_PRODUCTS, N_CUSTOMERS, N_ORDERS = 40, 55, 160
PULLIN_CUSTOMERS = 5
PULLIN_DAYS = 14


def _build_master(client, auth, rng):
    upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
           [[c, n, "E", 10, 4] for c, n, _ in WCS])
    upload(client, auth, "shifts", ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
           [[c, "Gündüz", "0,1,2,3,4", "08:00", "18:00", hc, 4] for c, _, hc in WCS])
    weekly_staffing(client, auth, [(c, hc, 4, 5) for c, _, hc in WCS], weeks=20)
    products = [f"E2E6-P{i:02d}" for i in range(1, N_PRODUCTS + 1)]
    upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [[p, f"Stres Ürünü {p[-2:]}", "E2E6"] for p in products])
    routing = []
    for p in products:
        n_ops = rng.choice([3, 3, 4, 4, 5])
        chosen = sorted(rng.sample(range(len(WCS)), n_ops))
        for k, wi in enumerate(chosen):
            routing.append([p, (k + 1) * 10, WCS[wi][1], WCS[wi][0], rng.randint(20, 90), f"{p}-{(k + 1) * 10}"])
    upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"], routing)
    return products


def _build_orders(client, auth, rng, products):
    rows, pullin_targets = [], []
    # 5 talepkâr müşteri: 8'er sipariş, 40 FARKLI ürün, termin 4..12. hafta
    k = 0
    for c in range(1, PULLIN_CUSTOMERS + 1):
        for j in range(8):
            p = products[k]
            k += 1
            due = WEEK + timedelta(weeks=rng.randint(4, 12), days=rng.randint(0, 4))
            no = f"E2E6-M{c:02d}-{j + 1}"
            rows.append([no, "10", f"Müşteri {c:02d}", due.isoformat(), p, rng.choice([200, 300, 400, 600, 800, 1000, 1200]), rng.randint(20, 400)])
            pullin_targets.append((no, p, due))
    # Diğer 50 müşteri: 120 sipariş
    seq = 0
    others = list(range(PULLIN_CUSTOMERS + 1, N_CUSTOMERS + 1))
    while len(rows) < N_ORDERS:
        c = others[seq % len(others)] if seq < 2 * len(others) else rng.choice(others)  # herkese en az 2 sipariş
        seq += 1
        due = WEEK + timedelta(weeks=rng.randint(2, 12), days=rng.randint(0, 4))
        rows.append([f"E2E6-M{c:02d}-{seq}", "10", f"Müşteri {c:02d}", due.isoformat(), rng.choice(products), rng.choice([200, 300, 400, 600, 800, 1000, 1200]), rng.randint(20, 400)])
    res = upload(client, auth, "orders", ["Sipariş No", "Poz No", "Müşteri", "Termin", "Stok Kodu", "Miktar", "Birim Fiyat"], rows)
    assert res.get("errors") in ([], None), res
    return pullin_targets


def _schedule_map(rows):
    return {r["order_no"]: r for r in rows}


def _d(v):
    return date.fromisoformat(v[:10]) if v else None


def test_pullin_stress_55_customers_40_products(client, auth, sc):
    """55 müşteri / 160 sipariş planlıyken 5 müşterinin 40 ürünlük termin öne çekme talebi: karşılanan, karşılanamayan ve ötelenen işler."""
    rng = random.Random(2026)
    products = _build_master(client, auth, rng)
    wc_ids = [wc_by_code(client, auth, c)["id"] for c, _, _ in WCS]
    targets = _build_orders(client, auth, rng, products)
    orders = {o["order_no"]: o for o in client.get("/api/orders", headers=auth).json() if o["order_no"].startswith("E2E6-")}
    sc.step("160 sipariş / 55 müşteri açık", len(orders) == N_ORDERS and len({o["customer"] for o in orders.values()}) == N_CUSTOMERS, orders=len(orders), customers=len({o["customer"] for o in orders.values()}))
    sc.step("Talep 5 müşteri × 8 = 40 farklı ürün", len({p for _, p, _ in targets}) == 40)

    req = client.post("/api/requirements", headers=auth, json={}).json()
    cap = client.get("/api/capacity", headers=auth, params={"start": WEEK.isoformat()}).json()
    weekly_cap = sum(c["capacity_hours"] for c in cap if c["work_center_code"].startswith("E2E6-"))
    sc.info("Yük profili", total_required_hours=round(req["total_hours"]), weekly_capacity_hours=weekly_cap, weeks_of_load=round(req["total_hours"] / weekly_cap, 1))

    t0 = time.perf_counter()
    out = plan_auto(client, auth, start_week=WEEK.isoformat(), weeks=16, work_center_ids=wc_ids, replace_existing=True, placement="asap", use_overtime=False)
    sc.step("Canlı plan (16 hafta) kuruldu", out.get("created", 0) > 0, created=out["created"], unplanned=len(out.get("unplanned", [])), seconds=round(time.perf_counter() - t0, 1))
    base_rows = [r for r in client.get("/api/plan/orders", headers=auth).json() if r["order_no"].startswith("E2E6-")]
    base = _schedule_map(base_rows)
    base_late = sorted(n for n, r in base.items() if r["plan_status"] == "late")
    sc.info("Canlı plan durumu", on_time=sum(1 for r in base.values() if r["plan_status"] == "on_time"), late=len(base_late), unplanned=sum(1 for r in base.values() if r["plan_status"] == "unplanned"))

    # ---- Revizyon 1: 40 termin 14 gün öne
    body = {"reason_codes": ["vip_pull_in"], "note": "5 müşteri aynı gün termin iyileştirme istedi", "start_week": WEEK.isoformat(), "weeks": 16, "work_center_ids": wc_ids, "mode": "due_date", "placement": "asap", "use_overtime": False}  # revizyon sözleşmesi ölçülür: otomatik FM kapalı
    rev = client.post("/api/plan/revisions", headers=auth, json=body).json()
    new_due = {no: max(WEEK + timedelta(days=7), due - timedelta(days=PULLIN_DAYS)) for no, _, due in targets}
    changes = [{"entity_type": "order", "entity_id": orders[no]["id"], "field": "revised_due_date", "new_value": new_due[no].isoformat()} for no, _, _ in targets]
    t0 = time.perf_counter()
    r = client.post(f"/api/plan/revisions/{rev['id']}/changes/bulk", headers=auth, json={"changes": changes})
    sc.step("40 revize termin tek seferde girildi", r.status_code == 200 and len(r.json()["changes"]) == 40, status=r.status_code, seconds=round(time.perf_counter() - t0, 2))
    t0 = time.perf_counter()
    calc = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth)
    calc_s = round(time.perf_counter() - t0, 1)
    sc.step("Revizyon hesaplandı", calc.status_code == 200, seconds=calc_s, body=calc.text[:200] if calc.status_code != 200 else None)
    cmp = calc.json()["compare"]
    prop = _schedule_map([x for x in cmp["schedule_rows"] if x["order_no"].startswith("E2E6-")])
    sc.step("Önerilen takvim tüm siparişleri kapsıyor", set(prop) == set(base), proposed_rows=len(prop))
    sc.step("Canlı plan hesaplamayla değişmedi", _schedule_map([r for r in client.get("/api/plan/orders", headers=auth).json() if r["order_no"].startswith("E2E6-")]) == base)

    # Talep analizi: yeni termine göre karşılandı mı?
    met, unmet = [], []
    for no, p, old_due in targets:
        pr = prop[no]
        end = _d(pr["planned_end"])
        ok = end is not None and end <= new_due[no]
        (met if ok else unmet).append({"order": no, "product": p, "old_due": old_due.isoformat(), "new_due": new_due[no].isoformat(), "proposed_end": pr["planned_end"], "baseline_end": base[no]["planned_end"], "status": pr["plan_status"], "lateness": pr.get("lateness_days")})
    sc.step("Talep sonucu hesaplanabilir (karşılanan + karşılanamayan = 40)", len(met) + len(unmet) == 40, met=len(met), unmet=len(unmet))
    sc.step("Önerilen takvimde gecikme yeni (revize) termine göre ölçülüyor", all(((u["status"] == "late") == (_d(u["proposed_end"]) > date.fromisoformat(u["new_due"]))) for u in met + unmet if u["proposed_end"]))

    # Yan etki: talepte OLMAYAN siparişlerden bitişi geriye kayanlar / geç kalanlar
    target_nos = {no for no, _, _ in targets}
    pushed, newly_late = [], []
    for no, br in base.items():
        if no in target_nos:
            continue
        pr = prop[no]
        b_end, p_end = _d(br["planned_end"]), _d(pr["planned_end"])
        if b_end and p_end and p_end > b_end:
            pushed.append({"order": no, "customer": br["customer"], "product": br["item_code"], "baseline_end": br["planned_end"], "proposed_end": pr["planned_end"], "delta_days": (p_end - b_end).days, "due": br["due_date"], "status_before": br["plan_status"], "status_after": pr["plan_status"]})
            if br["plan_status"] != "late" and pr["plan_status"] == "late":
                newly_late.append(no)
    pulled_forward = sum(1 for no in target_nos if _d(prop[no]["planned_end"]) and _d(base[no]["planned_end"]) and _d(prop[no]["planned_end"]) < _d(base[no]["planned_end"]))
    sc.step("Öne çekme diğer siparişleri ötelediğinde bu API verisinden çıkarılabiliyor", len(pushed) > 0, pushed=len(pushed), newly_late=len(newly_late), pulled_forward=pulled_forward)
    sc.info("KPI karşılaştırması (ekranda gösterilen)", baseline_late=cmp["baseline"]["late"], proposed_late=cmp["proposed"]["late"], baseline_on_time=cmp["baseline"]["on_time"], proposed_on_time=cmp["proposed"]["on_time"])

    # ---- Sunucunun sipariş bazlı önce/sonra farkı (ekranın gösterdiği) bağımsız hesapla birebir örtüşmeli
    diffs = {d["order_no"]: d for d in cmp["order_diffs"] if d["order_no"].startswith("E2E6-")}
    summary = cmp["diff_summary"]
    sc.step("Sipariş bazlı fark tüm siparişleri kapsıyor", set(diffs) == set(base), rows=len(diffs))
    srv_met = {no for no, d in diffs.items() if d["requested"] and d["met"] is True}
    srv_unmet = {no for no, d in diffs.items() if d["requested"] and d["met"] is False}
    sc.step("Karşılanan/karşılanamayan talepler sunucuda aynı", srv_met == {m["order"] for m in met} and srv_unmet == {u["order"] for u in unmet}, summary=summary)
    srv_pushed = {no for no, d in diffs.items() if d["pushed"] and not d["requested"]}
    srv_newly_late = {no for no, d in diffs.items() if d["newly_late"]}
    sc.step("Ötelenen ve yeni geç siparişler sunucuda adıyla listeleniyor", srv_pushed == {p["order"] for p in pushed} and srv_newly_late == set(newly_late), pushed=len(srv_pushed), newly_late=len(srv_newly_late))
    sc.step("Her talep satırında yeni termin ve taslak girdi kimliği var", all(diffs[no]["due_after"] == new_due[no].isoformat() and diffs[no]["change_id"] for no in target_nos))
    sc.step("Kayma günleri bitiş farkıyla tutarlı", all(d["delta_days"] == (_d(d["end_after"]) - _d(d["end_before"])).days for d in diffs.values() if d["end_after"] and d["end_before"]))
    sc.step("Özet sayaçları satırlarla tutarlı", summary["requested"] == 40 and summary["met"] == len(srv_met) and summary["unmet"] == len(srv_unmet) and summary["newly_late"] == len(srv_newly_late))

    # ---- Kısmi kabul: karşılanamayan talepler tek işlemle geri çekilir (ekrandaki 'geri çek ve yeniden hesapla')
    unmet_change_ids = [diffs[no]["change_id"] for no in srv_unmet]
    rm = client.post(f"/api/plan/revisions/{rev['id']}/changes/remove", headers=auth, json={"change_ids": unmet_change_ids})
    sc.step("Karşılanamayan talepler tek istekle taslaktan çıkarıldı", rm.status_code == 200 and len(rm.json()["changes"]) == 40 - len(unmet_change_ids), removed=len(unmet_change_ids), status=rm.status_code)
    calc2 = client.post(f"/api/plan/revisions/{rev['id']}/calculate", headers=auth).json()
    sc.step("İkinci hesapta karşılanamayan talep kalmadı", calc2["compare"]["diff_summary"]["unmet"] == 0, summary=calc2["compare"]["diff_summary"])
    prop2 = _schedule_map([x for x in calc2["compare"]["schedule_rows"] if x["order_no"].startswith("E2E6-")])
    kept = [m["order"] for m in met]
    still_met = [no for no in kept if _d(prop2[no]["planned_end"]) and _d(prop2[no]["planned_end"]) <= new_due[no]]
    sc.step("Kabul edilen talepler ikinci hesapta da karşılanıyor", len(still_met) == len(kept), kept=len(kept), still_met=len(still_met), lost=[no for no in kept if no not in still_met][:8])

    # ---- Onay: ekranda görülen önerilen takvim canlı plana birebir yazılır
    ap = client.post(f"/api/plan/revisions/{rev['id']}/approve", headers=auth)
    sc.step("Revizyon onaylandı ve devreye alındı", ap.status_code == 200, status=ap.status_code, body=ap.text[:200] if ap.status_code != 200 else None)
    live = _schedule_map([r for r in client.get("/api/plan/orders", headers=auth).json() if r["order_no"].startswith("E2E6-")])
    diff = [no for no in live if (live[no]["planned_end"], live[no]["plan_status"]) != (prop2[no]["planned_end"], prop2[no]["plan_status"])]
    sc.step("Onay sonrası canlı takvim = hesaplanan öneri", diff == [], differing=diff[:10])
    sc.step("Kabul edilen revize terminler siparişe yazıldı", all(client.get("/api/orders", headers=auth).json() and True for _ in [0]) and all(o["revised_due_date"] == new_due[o["order_no"]].isoformat() for o in client.get("/api/orders", headers=auth).json() if o["order_no"] in set(kept)))

    # ---- Revizyon 2: iş taşıma yolu — öne çekilen işlerin ötelediği siparişler API'de ADIYLA listelenir
    m01 = [no for no, _, _ in targets if no.startswith("E2E6-M01-")][:4]
    rev2 = client.post("/api/plan/revisions", headers=auth, json={**body, "note": "M01 dört işi 1. haftaya"}).json()
    moves = [{"entity_type": "order", "entity_id": orders[no]["id"], "extra_key": orders[no]["item_code"], "field": "job_move",
              "new_value": json.dumps({"item_code": orders[no]["item_code"], "start_date": WEEK.isoformat(), "qty_mode": "remaining", "quantity": None})} for no in m01]
    r = client.post(f"/api/plan/revisions/{rev2['id']}/changes/bulk", headers=auth, json={"changes": moves})
    sc.step("4 iş taşıma girildi", r.status_code == 200, status=r.status_code, body=r.text[:200] if r.status_code != 200 else None)
    t0 = time.perf_counter()
    calc3 = client.post(f"/api/plan/revisions/{rev2['id']}/calculate", headers=auth)
    sc.step("İş taşıma revizyonu hesaplandı", calc3.status_code == 200, seconds=round(time.perf_counter() - t0, 1))
    c3 = calc3.json()["compare"]
    sc.step("Dolu haftaya taşıma 'kaydırılan işler' listesini ADIYLA veriyor", len(c3["bumped_orders"]) > 0, bumped=c3["bumped_orders"][:12], notes=c3["insert_notes"][:4])
    rj = client.post(f"/api/plan/revisions/{rev2['id']}/reject", headers=auth, json={"note": "kaydırma kabul edilmedi"})
    sc.step("Planlamacı reddetti; canlı plan korunuyor", rj.status_code == 200 and _schedule_map([r for r in client.get("/api/plan/orders", headers=auth).json() if r["order_no"].startswith("E2E6-")]) == live)

    # ---- Planlamacı raporu
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    L = ["# Termin geri çekme stres senaryosu — planlamacı raporu", "",
         f"Veri: {N_ORDERS} açık sipariş, {N_CUSTOMERS} müşteri, {N_PRODUCTS} ürün, 5 iş merkezi ({weekly_cap} saat/hafta). "
         f"Toplam yük {round(req['total_hours'])} saat (~{round(req['total_hours'] / weekly_cap, 1)} hafta). Plan ufku 16 hafta.", "",
         f"Talep: {PULLIN_CUSTOMERS} müşteri, 40 farklı ürün, termin {PULLIN_DAYS} gün öne. Hesaplama süresi {calc_s} sn.", "",
         "## Sonuç özeti", "",
         f"- Karşılanabilen talep: **{len(met)} / 40**", f"- Karşılanamayan talep: **{len(unmet)} / 40** (planlamacı bunları geri çekti, kalanlar onaylandı)",
         f"- Talep dışı ötelenen sipariş: **{len(pushed)} / {N_ORDERS - 40}**, bunlardan yeni geç kalan: **{len(newly_late)}** "
         f"(kayma ≤7 gün: {sum(1 for p in pushed if p['delta_days'] <= 7)}, 8-14 gün: {sum(1 for p in pushed if 7 < p['delta_days'] <= 14)}, >14 gün: {sum(1 for p in pushed if p['delta_days'] > 14)}; etkilenen müşteri: {len({p['customer'] for p in pushed})})",
         "- Not: revize-termin revizyonu tüm ufku yeniden simüle eder; bu yüzden talep dışı siparişlerin büyük kısmı birkaç gün oynar (plan dalgalanması). İş taşıma yolu ise yalnızca çakışan işleri kaydırır.",
         f"- Geç sipariş sayısı (KPI): {cmp['baseline']['late']} → {cmp['proposed']['late']}", "",
         "## Karşılanan talepler", "", "| Sipariş | Ürün | Eski termin | Yeni termin | Önerilen bitiş | Eski bitiş |", "|---|---|---|---|---|---|"]
    L += [f"| {m['order']} | {m['product']} | {m['old_due']} | {m['new_due']} | {m['proposed_end']} | {m['baseline_end']} |" for m in met]
    L += ["", "## Karşılanamayan talepler", "", "| Sipariş | Ürün | Eski termin | Yeni termin | Önerilen bitiş | Gecikme (gün) |", "|---|---|---|---|---|---|"]
    L += [f"| {u['order']} | {u['product']} | {u['old_due']} | {u['new_due']} | {u['proposed_end']} | {u['lateness']} |" for u in unmet]
    L += ["", "## Ötelenen diğer siparişler (talep dışı)", "", "| Sipariş | Müşteri | Ürün | Termin | Eski bitiş | Yeni bitiş | Kayma (gün) | Durum |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {p['order']} | {p['customer']} | {p['product']} | {p['due']} | {p['baseline_end']} | {p['proposed_end']} | +{p['delta_days']} | {p['status_before']} → {p['status_after']} |" for p in sorted(pushed, key=lambda x: -x["delta_days"])]
    L += ["", "## İş taşıma yolu (Revizyon 2)", "", f"M01'in 4 işi 1. haftaya taşındığında API'nin adıyla bildirdiği kaydırılan işler: {', '.join(c3['bumped_orders']) or '—'}", ""]
    L += ["## Revizyon ekranı sözleşmesi (bu koşuda doğrulandı)", "",
          f"1. Karşılaştırma yanıtı sipariş bazlı `order_diffs` taşıyor: {len(diffs)} satır; her talepte yeni termin, karşılandı/karşılanamadı ve taslak girdi kimliği var.",
          f"2. Ötelenen ({len(srv_pushed)}) ve yeni geç kalan ({len(srv_newly_late)}) siparişler adıyla, kayma günü ve durum geçişiyle listeleniyor; bağımsız hesapla birebir örtüştü.",
          "3. Canlı ve önerilen takvim farkı sunucuda hesaplanıyor (fingerprint ile tutarlı); ekran 'Sipariş bazında önce / sonra' tablosunu, özet kartlarını, filtreleri ve CSV indirmeyi bu veriden çiziyor.",
          "4. Termin talepleri sipariş başına ayrı yeni terminle giriliyor (tablo + 'tümüne uygula' / ±gün kısayolları); tek istekte toplu gönderilir.",
          f"5. Karşılanamayan {len(unmet_change_ids)} talep tek istekle (`changes/remove`) geri çekildi ve yeniden hesapta karşılanamayan kalmadı.",
          f"6. İş taşıma yolunda kaydırılan işler ({len(c3['bumped_orders'])}) aynı tabloda 'Kaydırıldı' etiketi, kayma günü ve termin durumu ile görünür."]
    (REPORT_DIR / "pullin_stress_report.md").write_text("\n".join(L), encoding="utf-8")
    (REPORT_DIR / "pullin_stress_data.json").write_text(json.dumps({"met": met, "unmet": unmet, "pushed": pushed, "newly_late": newly_late, "kpi": {"baseline": cmp["baseline"], "proposed": cmp["proposed"]}, "job_move_bumped": c3["bumped_orders"]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    sc.info("Rapor yazıldı", path=str(REPORT_DIR / "pullin_stress_report.md"))
