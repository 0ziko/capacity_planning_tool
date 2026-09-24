"""Atıl kapasite önerisi: atıl haftaya öne çekilebilecek işler (salt hesap, yazmaz).

Bir iş merkezi × hafta hücresinde atıl saat varsa, aynı iş merkezinde **daha sonraki haftalarda**
planlı (auto/manual) satırlar arasından öne çekilebilir olanlar listelenir:
  * öncül operasyonu yok ya da öncülün tüm plan satırları bu haftadan önce/aynı haftada,
  * malzeme durumu 'expected' ise hazır tarihi hedef haftanın sonunu geçmiyor,
  * makine (dizilim) kapasitesine bağlı satırlar hariç (haftalık iş gücü kapasitesi geçerli),
  * sipariş termini sırasıyla; atıl saate sığan miktar 'fits' ile işaretlenir.
Planlamacı seçtiği siparişler için tek tıkla iş taşıma taslağı (revizyon) oluşturur; canlı plan onaya kadar değişmez.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models import Order, PlanLine, RoutingOperation, WorkCenter
from app.schemas import IdleSuggestionOut, IdleSuggestionRow
from app.services import capacity as cap
from app.services.orders import effective_due


def suggest_pull_forward(db: Session, wc_id: int, week: date, *, min_idle_hours: float = 1.0, limit: int = 50) -> IdleSuggestionOut:
    wc = db.get(WorkCenter, wc_id)
    if not wc:
        raise ValueError("Is merkezi bulunamadi")
    wk = cap.week_start(week)
    plan_cap = cap.planning_capacity_hours(db, wc, wk)
    used = sum(
        p.planned_hours or 0.0
        for p in db.query(PlanLine).filter(PlanLine.work_center_id == wc.id, PlanLine.week_start == wk, PlanLine.mode.in_(["auto", "manual"])).all()
    )
    idle = max(plan_cap - used, 0.0)
    out = IdleSuggestionOut(work_center_id=wc.id, work_center_code=wc.code, week_start=wk, capacity_hours=round(plan_cap, 2),
                            planned_hours=round(used, 2), idle_hours=round(idle, 2), rows=[], notes=[])
    if wc.planning_mode == "line":
        out.notes.append("Dizilim (hat) modunda öne çekme istasyon takvimiyle değerlendirilir; öneri üretilmedi.")
        return out
    if idle < min_idle_hours:
        return out

    later = (
        db.query(PlanLine)
        .options(joinedload(PlanLine.order).joinedload(Order.item), joinedload(PlanLine.operation))
        .filter(PlanLine.work_center_id == wc.id, PlanLine.week_start > wk, PlanLine.mode.in_(["auto", "manual"]), PlanLine.machine_id.is_(None))
        .all()
    )
    later = [p for p in later if p.order is not None and p.order.status == "open" and p.operation is not None and p.production_batch_id is None]
    if not later:
        return out
    order_ids = {p.order_id for p in later}
    all_lines = db.query(PlanLine).filter(PlanLine.order_id.in_(order_ids), PlanLine.mode.in_(["auto", "manual", "forecast"])).all()
    lines_by_order: dict[int, list[PlanLine]] = defaultdict(list)
    for p in all_lines:
        lines_by_order[p.order_id].append(p)
    op_ids = {p.operation_id for p in all_lines}
    ops = {o.id: o for o in db.query(RoutingOperation).filter(RoutingOperation.id.in_(op_ids)).all()}
    week_end = wk + timedelta(days=6)

    # Ayni siparis+operasyon icin en erken sonraki hafta satirini aday al (tekrarlari birlestir)
    best: dict[tuple[int, int], PlanLine] = {}
    for p in later:
        key = (p.order_id, p.operation_id)
        if key not in best or p.week_start < best[key].week_start:
            best[key] = p

    rows: list[IdleSuggestionRow] = []
    for (oid, opid), p in best.items():
        o = p.order
        op = ops.get(opid) or p.operation
        # Oncul: ayni rota kartinda daha kucuk seq'li operasyonlar
        preds = [q for q in ops.values() if q.item_id == op.item_id and q.seq < op.seq]
        pred_ready = True
        blocker = ""
        for q in preds:
            q_lines = [l for l in lines_by_order[oid] if l.operation_id == q.id]
            if q_lines and max(l.week_start for l in q_lines) > wk:
                pred_ready = False
                blocker = f"öncül {q.seq} {q.operation_name or ''} {max(l.week_start for l in q_lines).isoformat()} haftasında"
                break
        material_ok = True
        if (o.material_status or "unknown") == "expected" and o.material_ready_date and o.material_ready_date > week_end:
            material_ok = False
            blocker = f"malzeme {o.material_ready_date.isoformat()}"
        rows.append(IdleSuggestionRow(
            order_id=o.id, order_no=o.order_no, position_no=o.position_no or "", customer=o.customer or "",
            item_code=o.item.code if o.item else "", operation_seq=op.seq, operation_name=op.operation_name or "",
            semi_finished_code=op.semi_finished_code or "", from_week=p.week_start, hours=round(float(p.planned_hours or 0), 2),
            qty=round(float(p.planned_qty or 0), 2), due_date=effective_due(o), plan_line_id=p.id,
            eligible=pred_ready and material_ok, blocker=blocker,
        ))
    rows.sort(key=lambda r: (not r.eligible, r.due_date, r.from_week, r.order_no))
    budget = idle
    for r in rows:
        if r.eligible:
            r.fits = r.hours <= budget + 1e-6
            if r.fits:
                budget -= r.hours
    out.rows = rows[:limit]
    out.eligible_count = sum(1 for r in rows if r.eligible)
    out.fits_hours = round(idle - budget, 2)
    return out
