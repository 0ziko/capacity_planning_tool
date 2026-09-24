"""Plan raporu: otomatik plan veya revizyon onayı sonrası canlı plandan üretilen özet.

İçerik: özet KPI'lar, iş merkezi tablosu (kapasite/plan/atıl/doluluk/plansız), plansız nedenleri,
sipariş durumu (hedef termin − 2 gün), haftalık doluluk, ardışık operasyon geçişleri ve
eşik tabanlı deterministik yorumlar. Serbest metin üretmez; her satır bir kuralın çıktısıdır.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Order, PlanLine, RoutingOperation, WorkCenter, WorkCenterWeek
from app.models.plan_report import PlanReport
from app.services import capacity as cap
from app.services import orders as orders_svc
from app.services.orders import DELIVERY_BUFFER_DAYS

BOTTLENECK_UTIL = 0.95


def fmt_h(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")

REASON_LABELS = {"kapasite_yetersiz": "Kapasite yetersiz", "oncul_eksik": "Önceki adım yetişmedi",
                 "yarimamul_eksik": "Montaj parçası (yarımamül) yetişmedi", "operasyon_suresi_eksik": "Operasyon süresi eksik",
                 "bekleme_ufuk_disinda": "Bekleme süresi ufuk dışında", "termin_kaydi": "Hedef tarihe sığmadı (plana yazılmadı)",
                 "darbogaz_bekliyor": "Darboğaz nedeniyle dengeli kısıldı"}
REASON_HELP = {"kapasite_yetersiz": "İş merkezinde izin verilen haftalarda yeterli saat kalmadı.",
               "oncul_eksik": "Aynı ürünün rotasındaki bir önceki operasyon yeterli miktarı üretemedi; bu adım beslenemedi.",
               "yarimamul_eksik": "Montaj için gereken farklı bir parçanın (reçetedeki yarımamülün) kendi üretimi yetişmedi; montaj adımı bekledi.",
               "operasyon_suresi_eksik": "Rotada süre sıfır/negatif.", "bekleme_ufuk_disinda": "Geçiş kuralındaki bekleme süresi plan ufkunu aşıyor.",
               "termin_kaydi": "Kayan adet 'komple kaydır' ayarıyla plana yazılmadı; tahmini bitiş ve darboğaz notta.",
               "darbogaz_bekliyor": "Bu adımın kendi kapasitesi var; aynı siparişin darboğaz adımı yetişmediği için yetim iş üretmemek adına aynı adette kısıldı. Kök neden darboğazın satırında."}


def build_plan_report(db: Session, *, start_week: date, weeks: int, work_center_ids: list[int] | None,
                      unplanned: list[dict] | None = None, placement_notes: list[dict] | None = None,
                      kind: str = "auto", username: str = "", revision_id: int | None = None, plan_result: dict | None = None) -> dict:
    wk0 = cap.week_start(start_week)
    wks = [wk0 + timedelta(weeks=i) for i in range(weeks)]
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    q = q.filter(WorkCenter.id.in_(work_center_ids)) if work_center_ids else q.filter(WorkCenter.is_planned.is_(True))
    wcs = q.order_by(WorkCenter.code).all()
    wc_ids = [w.id for w in wcs]
    lines = db.query(PlanLine).filter(PlanLine.work_center_id.in_(wc_ids), PlanLine.week_start >= wk0,
                                      PlanLine.week_start <= wks[-1], PlanLine.mode.in_(["auto", "manual"])).all() if wc_ids else []
    load: dict[tuple[int, date], float] = defaultdict(float)
    for l in lines:
        load[(l.work_center_id, l.week_start)] += l.planned_hours or 0.0
    capm = {(w.id, wk): cap.planning_capacity_hours(db, w, wk) for w in wcs for wk in wks}

    unplanned = unplanned or []
    unp_by_wc: dict[str, float] = defaultdict(float)
    unp_by_reason: dict[str, float] = defaultdict(float)
    unp_orders: dict[str, float] = defaultdict(float)
    unp_wc_reason: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for u in unplanned:
        h = float(u.get("hours") or 0)
        unp_by_wc[u.get("work_center_code") or "?"] += h
        unp_by_reason[u.get("reason") or "?"] += h
        unp_orders[u.get("order_no") or "?"] += h
        unp_wc_reason[u.get("work_center_code") or "?"][u.get("reason") or "?"] += h

    wc_rows = []
    for w in wcs:
        c = sum(capm[(w.id, wk)] for wk in wks)
        u = sum(load.get((w.id, wk), 0.0) for wk in wks)
        util = (u / c) if c > 0 else 0.0
        wc_rows.append({"code": w.code, "name": w.name, "planning_mode": w.planning_mode or "labor", "capacity_hours": round(c, 1),
                        "planned_hours": round(u, 1), "idle_hours": round(max(c - u, 0), 1), "utilization": round(util, 3),
                        "unplanned_hours": round(unp_by_wc.get(w.code, 0.0), 1),
                        "unplanned_reasons": {REASON_LABELS.get(k, k): round(v, 1) for k, v in unp_wc_reason.get(w.code, {}).items()},
                        "bottleneck": c > 0 and util >= BOTTLENECK_UTIL, "zero_capacity": c <= 0})
    # Hat merkezlerinde istasyon bazlı doluluk (tavlama fırınları, yıkama makineleri)
    from app.services.planning import machine_loads
    machine_rows = []
    for w in wcs:
        if (w.planning_mode or "labor") != "line":
            continue
        for ml in machine_loads(db, w, wks):
            c = sum(x.capacity_hours for x in ml.weeks); u = sum(x.planned_hours for x in ml.weeks)
            machine_rows.append({"work_center": w.code, "machine_code": ml.machine_code, "machine_name": ml.machine_name, "capacity_hours": round(c, 1), "planned_hours": round(u, 1),
                                 "idle_hours": round(max(c - u, 0), 1), "utilization": round(u / c, 3) if c > 0 else 0.0,
                                 "weekly": [{"week_start": x.week_start.isoformat(), "capacity_hours": x.capacity_hours, "planned_hours": x.planned_hours, "utilization": x.utilization} for x in ml.weeks]})
    total_c = sum(r["capacity_hours"] for r in wc_rows)
    total_u = sum(r["planned_hours"] for r in wc_rows)

    weekly = []
    for wk in wks:
        c = sum(capm[(w.id, wk)] for w in wcs)
        u = sum(load.get((w.id, wk), 0.0) for w in wcs)
        weekly.append({"week_start": wk.isoformat(), "capacity_hours": round(c, 1), "planned_hours": round(u, 1),
                       "idle_hours": round(max(c - u, 0), 1), "utilization": round(u / c, 3) if c > 0 else 0.0})

    sched = orders_svc.order_schedule(db, work_center_ids)
    status = defaultdict(int)
    for r in sched:
        status[r.plan_status] += 1
    slack = [r.slack_days for r in sched if r.slack_days is not None]
    overdue = sum(1 for r in sched if r.due_date < wk0)
    buffer_ok = sum(1 for r in sched if r.buffer_ok)
    order_block = {"open_orders": len(sched), "status": dict(status), "with_finish_estimate": len(slack),
                   "target_met": buffer_ok, "overdue_before_horizon": overdue,
                   "avg_slack_days": round(sum(slack) / len(slack), 1) if slack else None,
                   "delivery_buffer_days": DELIVERY_BUFFER_DAYS}

    tag_hours: dict[str, float] = defaultdict(float)
    for l in lines:
        tag_hours[(l.tag or "normal")] += l.planned_hours or 0.0
    ops = {o.id: o for o in db.query(RoutingOperation).filter(RoutingOperation.id.in_({l.operation_id for l in lines})).all()} if lines else {}
    # Hazırlık satırları bilinçli ara üretimdir; yetim ölçümüne girmez (ayrı gösterge: prep_line_hours).
    orphan_total, orphan_by_wc = _orphan_hours(db, [l for l in lines if (l.tag or "") != "prep"], ops, {w.id: w.code for w in wcs})
    chains: dict[tuple, dict[int, list[date]]] = defaultdict(lambda: defaultdict(list))
    for l in lines:
        op = ops.get(l.operation_id)
        if op is None:
            continue
        chains[(l.order_id, l.production_batch_id, op.item_id)][op.seq].append(l.week_start)
    waits: list[float] = []
    for seqs in chains.values():
        s = sorted(seqs)
        for a, b in zip(s, s[1:]):
            waits.append((min(seqs[b]) - max(seqs[a])).days / 7.0)
    transitions = {"count": len(waits), "no_wait": sum(1 for w in waits if w <= 0),
                   "avg_wait_weeks": round(sum(waits) / len(waits), 2) if waits else 0.0}

    notes = placement_notes or []
    note_kinds = defaultdict(int)
    for n in notes:
        note_kinds[n.get("kind") or "?"] += 1

    findings: list[dict] = []
    for r in sorted(wc_rows, key=lambda x: -x["unplanned_hours"]):
        if r["bottleneck"] and r["unplanned_hours"] > 0:
            findings.append({"level": "critical", "code": "bottleneck", "title": f"Darboğaz: {r['code']}",
                             "text": f"Kapasitesi %{round(r['utilization'] * 100)} dolu, {fmt_h(r['unplanned_hours'])} sa iş bu merkeze sığmadı. Fazla mesai veya ek kişi/vardiya gerekir."})
        elif r["bottleneck"]:
            findings.append({"level": "warn", "code": "full", "title": f"Tam dolu: {r['code']}", "text": f"Kapasitesi %{round(r['utilization'] * 100)} dolu; yeni iş alamaz."})
    for r in wc_rows:
        if r["zero_capacity"]:
            findings.append({"level": "warn", "code": "zero_capacity", "title": f"Kapasite girilmemiş: {r['code']}", "text": "Bu ufukta haftalık kişi sayısı veya istasyon saati yok; buradan geçen tüm işler plansız kalır."})
    if unp_by_reason.get("oncul_eksik", 0) > 0:
        findings.append({"level": "info", "code": "chain", "title": "Önceki adım yetişmedi", "text": f"{fmt_h(unp_by_reason['oncul_eksik'])} sa iş, aynı ürünün bir önceki operasyonu üretemediği için planlanamadı. Darboğazın arkasındaki merkezler bu yüzden boş kalıyor; darboğaz açılınca bu işler kendiliğinden yerleşir."})
    if unp_by_reason.get("yarimamul_eksik", 0) > 0:
        findings.append({"level": "info", "code": "wip_missing", "title": "Montaj parçası yetişmedi", "text": f"{fmt_h(unp_by_reason['yarimamul_eksik'])} sa montaj işi, reçetedeki başka bir parçanın (yarımamülün) üretimi yetişmediği için planlanamadı. Parçanın kendi rotasındaki darboğaza bakın."})
    if overdue:
        findings.append({"level": "warn", "code": "overdue", "title": "Termini geçmiş siparişler", "text": f"{overdue} açık siparişin termini plan başlangıcından önce. Bunlar ne yapılırsa yapılsın 'zamanında' sayılamaz; müşteriyle yeni termin belirleyip revize termin girin."})
    idle_wcs = [r for r in wc_rows if r["capacity_hours"] > 0 and r["utilization"] < 0.25]
    if idle_wcs:
        findings.append({"level": "info", "code": "idle", "title": "Boş kalan merkezler (doluluk < %25)", "text": ", ".join(f"{r['code']} %{round(r['utilization'] * 100)}" for r in idle_wcs) + ". Bu merkezlerde iş var ama önceki adımlar beslemiyor; darboğaz çözülmeden burada mesai açmak fayda sağlamaz."})
    if note_kinds.get("wip_cap_violation"):
        findings.append({"level": "warn", "code": "wip_cap", "title": "Ara stok sınırı aşıldı", "text": f"{note_kinds['wip_cap_violation']} üründe tanımlı ara stok sınırı tutturulamadı; kapasite başka yerleşime izin vermedi."})
    if transitions["count"] and transitions["no_wait"] < transitions["count"]:
        findings.append({"level": "info", "code": "waits", "title": "Operasyonlar arası bekleme", "text": f"{transitions['count']} geçişin {transitions['no_wait']} tanesi beklemesiz; {transitions['count'] - transitions['no_wait']} geçişte ara stok en az bir hafta bekliyor (geçiş kuralı veya sonraki merkezde yer yok)."})

    slips = _slip_rows(db, notes, sched, wk0)
    ot_rows = _overtime_rows(db, (plan_result or {}).get("overtime_proposals") or [])
    ot_total = round(sum(r["hours"] for r in ot_rows), 1)
    n_slip = sum(1 for r in slips if r["slipped_qty"] > 0 and not r["overdue"])
    n_slip_overdue = sum(1 for r in slips if r["overdue"])
    n_ot = sum(1 for r in slips if r["overtime_qty"] > 0)
    if ot_total > 0:
        top = sorted(ot_rows, key=lambda r: -r["hours"])[:3]
        findings.insert(0, {"level": "warn", "code": "overtime", "title": "Fazla mesai ihtiyacı (onay bekliyor)",
                            "text": f"{fmt_h(ot_total)} sa fazla mesai {len(ot_rows)} merkez/haftada termini kurtarıyor; {n_ot} sipariş buna bağlı. En büyük: " + ", ".join(f"{r['work_center_code']} {r['week_start'][5:]} {fmt_h(r['hours'])} sa" for r in top) + ". Haftalık iş gücünde onaylayın; onaylanmazsa bir sonraki planda bu işler kayar."})
    if n_slip:
        bn = defaultdict(int)
        for r in slips:
            if r["slipped_qty"] > 0 and not r["overdue"] and r["bottleneck"]:
                bn[r["bottleneck"]] += 1
        findings.insert(1 if ot_total > 0 else 0, {"level": "critical", "code": "slip", "title": "Hedef tarihe sığmayan siparişler",
                            "text": f"{n_slip} siparişin bir kısmı hedef tarihe (termin − {DELIVERY_BUFFER_DAYS} gün) sığmadı" + (" ve fazla mesaiyle de kapanmadı" if ot_total > 0 else "") + ". Darboğaz dağılımı: " + ", ".join(f"{k} ({v})" for k, v in sorted(bn.items(), key=lambda x: -x[1])[:5]) + ". Kayan siparişler tablosunda tahmini bitiş ve adet var; müşteriyle termin veya ek kapasite kararı verin."})
    streaks = _overtime_streaks(db, wcs, wks)
    if streaks:
        findings.append({"level": "warn", "code": "overtime_streak", "title": "Sürekli fazla mesai",
                         "text": "Aynı ekibe art arda 4+ hafta fazla mesai yazılı: " + ", ".join(f"{c} ({n} hafta)" for c, n in streaks) + ". Sürdürülebilirlik ve yasal sınır (270 sa/yıl) açısından kadro/vardiya kararı gerekir."})
    if orphan_total > 0 and total_u > 0 and orphan_total / total_u > 0.05:
        findings.append({"level": "warn", "code": "orphan", "title": "Bitmiş ürüne dönüşmeyen işçilik", "text": f"{fmt_h(orphan_total)} sa (%{round(100 * orphan_total / total_u)}) ara üretim ufukta bitmiş ürüne dönüşmüyor (üretim partileri / tamamlanmış bitiş adımı). En çok: " + ", ".join(f"{k} {fmt_h(v)} sa" for k, v in sorted(orphan_by_wc.items(), key=lambda x: -x[1])[:4]) + "."})
    top_unplanned_orders = sorted(unp_orders.items(), key=lambda x: -x[1])[:15]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind, "username": username, "revision_id": revision_id,
        "horizon": {"start_week": wk0.isoformat(), "weeks": weeks, "work_center_count": len(wcs)},
        "summary": {"planned_hours": round(total_u, 1), "capacity_hours": round(total_c, 1), "idle_hours": round(max(total_c - total_u, 0), 1),
                    "utilization": round(total_u / total_c, 3) if total_c > 0 else 0.0, "unplanned_hours": round(sum(unp_by_wc.values()), 1),
                    "unplanned_orders": len([k for k in unp_orders if k != "?"]), "line_count": len(lines),
                    "created": (plan_result or {}).get("created"), "placement_notes": dict(note_kinds),
                    "overtime_hours": ot_total, "overtime_cells": len(ot_rows), "overtime_line_hours": round(tag_hours.get("overtime", 0.0), 1),
                    "slip_line_hours": round(tag_hours.get("slip", 0.0), 1), "prep_line_hours": round(tag_hours.get("prep", 0.0), 1),
                    "orphan_hours": round(orphan_total, 1), "orphan_share": round(orphan_total / total_u, 3) if total_u > 0 else 0.0,
                    "slip_orders": n_slip, "slip_overdue_orders": n_slip_overdue, "overtime_orders": n_ot, "slip_mode": (plan_result or {}).get("slip_mode")},
        "work_centers": wc_rows,
        "unplanned_reasons": [{"reason": k, "label": REASON_LABELS.get(k, k), "help": REASON_HELP.get(k, ""), "hours": round(v, 1)} for k, v in sorted(unp_by_reason.items(), key=lambda x: -x[1])],
        "top_unplanned_orders": [{"order_no": k, "hours": round(v, 1)} for k, v in top_unplanned_orders],
        "orders": order_block, "weekly": weekly, "transitions": transitions, "findings": findings,
        "machines": machine_rows, "slips": slips, "overtime": ot_rows, "orphan_by_wc": {k: round(v, 1) for k, v in sorted(orphan_by_wc.items(), key=lambda x: -x[1])},
    }
    return report


def _orphan_hours(db: Session, lines, ops: dict, wc_code: dict[int, str]) -> tuple[float, dict[str, float]]:
    """Bitmiş ürüne dönüşmeyen işçilik: zincirde (sipariş, parti, ürün) son operasyon çıktısını aşan adedin saati."""
    if not lines:
        return 0.0, {}
    order_item = {oid: iid for oid, iid in db.query(Order.id, Order.item_id).filter(Order.id.in_({l.order_id for l in lines})).all()}
    chains: dict[tuple, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for l in lines:
        op = ops.get(l.operation_id)
        if op:
            chains[(l.order_id, l.production_batch_id, op.item_id)][op.seq].append(l)
    fg_out: dict[tuple, float] = {}
    for (oid, bid, iid), seqs in chains.items():
        if order_item.get(oid) == iid:
            fg_out[(oid, bid)] = sum(x.planned_qty or 0.0 for x in seqs[max(seqs)])
    total = 0.0
    by_wc: dict[str, float] = defaultdict(float)
    for (oid, bid, iid), seqs in chains.items():
        usable = fg_out.get((oid, bid), 0.0)
        for ls in seqs.values():
            q = sum(x.planned_qty or 0.0 for x in ls)
            h = sum(x.planned_hours or 0.0 for x in ls)
            if q <= 1e-6 or h <= 0:
                continue
            oh = h * max(q - usable, 0.0) / q
            total += oh
            for x in ls:
                by_wc[wc_code.get(x.work_center_id, "?")] += oh * (x.planned_hours or 0.0) / h
    return total, dict(by_wc)


def _overtime_streaks(db: Session, wcs, wks: list[date]) -> list[tuple[str, int]]:
    """Ufukta art arda ≥4 hafta fazla mesai (onaylı veya öneri) olan merkezler."""
    out = []
    for w in wcs:
        rows = {r.week_start: r for r in db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == w.id, WorkCenterWeek.week_start.in_(wks)).all()}
        best = run = 0
        for wk in wks:
            r = rows.get(wk)
            run = run + 1 if (r and (r.overtime_headcount or r.weekend_overtime_headcount)) else 0
            best = max(best, run)
        if best >= 4:
            out.append((w.code, best))
    return out


def _slip_rows(db: Session, notes: list[dict], sched, wk0: date) -> list[dict]:
    """Kayan sipariş tablosu: yerleşim notlarından (kind=slip) + sipariş takviminden gecikme günü."""
    by_order = {r.order_id: r for r in sched}
    rows = []
    for n in notes:
        if n.get("kind") != "slip":
            continue
        r = by_order.get(n.get("order_id"))
        rows.append({"order_no": n.get("label"), "order_id": n.get("order_id"), "customer": r.customer if r else "", "item_code": r.item_code if r else "",
                     "due_date": r.due_date.isoformat() if r else None, "target_date": r.target_date.isoformat() if r and r.target_date else None,
                     "remaining_qty": float(n.get("remaining_qty") or 0), "target_qty": float(n.get("target_qty") or 0), "overtime_qty": float(n.get("overtime_qty") or 0),
                     "slipped_qty": float(n.get("qty") or 0), "slipped_hours": float(n.get("hours") or 0), "bottleneck": n.get("bottleneck"),
                     "est_finish_week": n.get("est_finish_week"), "planned_end": r.planned_end.isoformat() if r and r.planned_end else None,
                     "days_late": (-r.slack_days if r and r.slack_days is not None and r.slack_days < 0 else 0), "plan_status": r.plan_status if r else "",
                     "overdue": bool(n.get("overdue")), "slip_mode": n.get("slip_mode"), "detail": n.get("detail"),
                     "overtime_saved_weeks": int(n.get("overtime_saved_weeks") or 0), "overtime_extra_qty": float(n.get("overtime_extra_qty") or 0)})
    rows.sort(key=lambda x: (x["overdue"], -x["slipped_qty"], x["order_no"] or ""))
    return rows


def _overtime_rows(db: Session, proposals: list[dict]) -> list[dict]:
    """FM ihtiyacı tablosu: öneri + haftalık iş gücündeki onay durumu + kişi başı yıl içi birikim."""
    from app.services import capacity as _cap
    rows = []
    for p in proposals:
        wk = date.fromisoformat(p["week_start"])
        ov = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == p["work_center_id"], WorkCenterWeek.week_start == wk).first()
        wc = db.get(WorkCenter, p["work_center_id"])
        ytd = _cap.overtime_person_hours_period(db, wc, date(wk.year, 1, 1), wk) if wc else 0.0
        rows.append({**p, "approved": bool(ov and not ov.overtime_proposed and (ov.overtime_headcount or ov.weekend_overtime_headcount)),
                     "pending": bool(ov and ov.overtime_proposed), "person_hours_ytd": ytd, "legal_yearly_hours": _cap.OVERTIME_LEGAL_YEARLY_HOURS})
    return rows


def store_report(db: Session, report: dict) -> PlanReport:
    s = report["summary"]
    row = PlanReport(kind=report["kind"], revision_id=report.get("revision_id"), username=report.get("username") or "",
                     start_week=report["horizon"]["start_week"], weeks=report["horizon"]["weeks"],
                     summary=f"plan {s['planned_hours']} sa · plansız {s['unplanned_hours']} sa · doluluk %{round(s['utilization'] * 100)} · FM {s.get('overtime_hours', 0)} sa · kayan {s.get('slip_orders', 0)} sipariş",
                     payload=json.loads(json.dumps(report, default=str)))
    db.add(row)
    db.flush()
    return row


def report_xlsx(report: dict) -> bytes:
    from openpyxl import Workbook

    from app.services.excel import _ws_from_rows, workbook_bytes

    wb = Workbook()
    s = report["summary"]; o = report["orders"]; t = report["transitions"]
    _ws_from_rows(wb, "Özet", ["Alan", "Değer"], [
        ["Oluşturma", report["generated_at"]], ["Tür", report["kind"]], ["Ufuk", f"{report['horizon']['start_week']} +{report['horizon']['weeks']} hafta"],
        ["Planlanan saat", s["planned_hours"]], ["Kapasite saat", s["capacity_hours"]], ["Atıl saat", s["idle_hours"]], ["Doluluk", s["utilization"]],
        ["Plansız saat", s["unplanned_hours"]], ["Plansız sipariş", s["unplanned_orders"]], ["Plan satırı", s["line_count"]],
        ["Açık sipariş", o["open_orders"]], ["Hedef (termin − 2 gün) tutan", o["target_met"]], ["Termini ufuktan önce", o["overdue_before_horizon"]],
        ["Ort. termine kalan gün", o["avg_slack_days"]], ["Ardışık geçiş", t["count"]], ["Beklemesiz geçiş", t["no_wait"]], ["Ort. bekleme (hafta)", t["avg_wait_weeks"]],
    ] + [[f"Durum: {k}", v] for k, v in o["status"].items()])
    _ws_from_rows(wb, "İş Merkezleri", ["Kod", "Ad", "Mod", "Kapasite", "Plan", "Atıl", "Doluluk", "Plansız", "Darboğaz", "Kapasite 0"],
                  [[r["code"], r["name"], r["planning_mode"], r["capacity_hours"], r["planned_hours"], r["idle_hours"], r["utilization"], r["unplanned_hours"], "E" if r["bottleneck"] else "", "E" if r["zero_capacity"] else ""] for r in report["work_centers"]])
    _ws_from_rows(wb, "İstasyonlar", ["İş merkezi", "İstasyon", "Ad", "Kapasite", "Plan", "Atıl", "Doluluk"] + [w["week_start"] for w in report["weekly"]],
                  [[r["work_center"], r["machine_code"], r["machine_name"], r["capacity_hours"], r["planned_hours"], r["idle_hours"], r["utilization"]] + [x["utilization"] for x in r["weekly"]] for r in report.get("machines", [])])
    _ws_from_rows(wb, "Plansız Nedenler", ["Neden", "Saat", "Açıklama"], [[r["label"], r["hours"], r.get("help", "")] for r in report["unplanned_reasons"]])
    _ws_from_rows(wb, "Plansız Siparişler", ["Sipariş", "Saat"], [[r["order_no"], r["hours"]] for r in report["top_unplanned_orders"]])
    _ws_from_rows(wb, "Haftalık", ["Hafta", "Kapasite", "Plan", "Atıl", "Doluluk"], [[w["week_start"], w["capacity_hours"], w["planned_hours"], w["idle_hours"], w["utilization"]] for w in report["weekly"]])
    _ws_from_rows(wb, "Bulgular", ["Seviye", "Konu", "Açıklama"], [[f["level"], f.get("title", f["code"]), f["text"]] for f in report["findings"]])
    _ws_from_rows(wb, "Kayan Siparişler", ["Sipariş", "Müşteri", "Ürün", "Termin", "Hedef", "Kalan adet", "Hedefte", "Fazla mesaiyle", "Kayan adet", "Darboğaz", "Tahmini bitiş haftası", "Plan bitiş", "Gecikme (gün)", "Durum"],
                  [[r["order_no"], r["customer"], r["item_code"], r["due_date"], r["target_date"], r["remaining_qty"], r["target_qty"], r["overtime_qty"], r["slipped_qty"], r["bottleneck"], r["est_finish_week"], r["planned_end"], r["days_late"], "Termini geçmiş" if r["overdue"] else ("Plana yazılmadı" if r["slip_mode"] == "defer" else "Sonraya yerleşti")] for r in report.get("slips", [])])
    _ws_from_rows(wb, "Fazla Mesai", ["İş merkezi", "Hafta", "Saat", "Hafta içi kişi", "Hafta içi gün", "Hafta sonu kişi", "Hafta sonu gün", "Kişi başı saat", "Yıl içi birikim", "Durum", "Hesap"],
                  [[r["work_center_code"], r["week_start"], r["hours"], r["weekday_persons"], r["weekday_days"], r["weekend_persons"], r["weekend_days"], r["person_hours"], r.get("person_hours_ytd"), "Onaylı" if r.get("approved") else ("Onay bekliyor" if r.get("pending") else "—"), r.get("explanation", "")] for r in report.get("overtime", [])])
    return workbook_bytes(wb)


def generate_and_store(db: Session, req, plan_result: dict | None, *, kind: str, username: str, revision_id: int | None = None) -> PlanReport:
    """Plan (auto/revizyon) sonrasi rapor uret ve kaydet. Hata olursa plan sonucunu bozmaz (None doner)."""
    pr = plan_result or {}
    report = build_plan_report(db, start_week=req.start_week, weeks=req.weeks, work_center_ids=req.work_center_ids,
                               unplanned=pr.get("unplanned"), placement_notes=pr.get("placement_notes"),
                               kind=kind, username=username, revision_id=revision_id, plan_result=pr)
    row = store_report(db, report)
    prune(db, keep=MAX_REPORTS)
    return row


MAX_REPORTS = 30


def prune(db: Session, keep: int = 30) -> None:
    ids = [r.id for r in db.query(PlanReport.id).order_by(PlanReport.created_at.desc(), PlanReport.id.desc()).offset(keep).all()]
    if ids:
        db.query(PlanReport).filter(PlanReport.id.in_(ids)).delete(synchronize_session=False)


def list_reports(db: Session, limit: int = 10) -> list[dict]:
    rows = db.query(PlanReport).order_by(PlanReport.created_at.desc(), PlanReport.id.desc()).limit(limit).all()
    out = []
    for r in rows:
        s = (r.payload or {}).get("summary") or {}
        o = (r.payload or {}).get("orders") or {}
        out.append({"id": r.id, "kind": r.kind, "revision_id": r.revision_id, "username": r.username, "start_week": r.start_week,
                    "weeks": r.weeks, "summary": r.summary, "created_at": r.created_at.isoformat() if r.created_at else None,
                    "planned_hours": s.get("planned_hours"), "unplanned_hours": s.get("unplanned_hours"), "idle_hours": s.get("idle_hours"),
                    "utilization": s.get("utilization"), "target_met": o.get("target_met"), "open_orders": o.get("open_orders"),
                    "overtime_hours": s.get("overtime_hours"), "slip_orders": s.get("slip_orders"), "orphan_hours": s.get("orphan_hours"),
                    "findings": len((r.payload or {}).get("findings") or [])})
    return out


def get_report(db: Session, report_id: int) -> PlanReport | None:
    return db.get(PlanReport, report_id)
