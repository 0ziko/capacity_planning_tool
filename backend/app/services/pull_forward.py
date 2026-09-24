"""Tüm ufuk için tek tık öne çekme: atıl haftalara sığan uygun işleri toplu iş taşıma taslağına dönüştürür.

Hücre hücre seçmek yerine ufuktaki her iş merkezi × hafta atıl kapasitesi haftadan haftaya (erken → geç)
taranır; her sipariş en fazla bir kez, en erken sığdığı haftaya taşınır. Öncül/malzeme uygunluğu
`idle_suggestions` kuralıyla aynıdır. Sonuç bir revizyon taslağıdır: hesapla → önce/sonra tablosu → onayla.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import WorkCenter
from app.schemas import AutoPlanRequest, PullForwardMove, PullForwardPlanOut
from app.services import capacity as cap
from app.services.idle_suggestions import suggest_pull_forward


def plan_pull_forward(db: Session, req: AutoPlanRequest, *, min_idle_hours: float = 1.0) -> PullForwardPlanOut:
    start = cap.week_start(req.start_week)
    weeks = [start + timedelta(weeks=i) for i in range(req.weeks)]
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    q = q.filter(WorkCenter.id.in_(req.work_center_ids)) if req.work_center_ids else q.filter(WorkCenter.is_planned.is_(True))
    wcs = q.order_by(WorkCenter.code).all()
    moves: list[PullForwardMove] = []
    taken: set[int] = set()
    idle_total = 0.0
    cells = 0
    for wk in weeks:
        for wc in wcs:
            if wc.planning_mode == "line":
                continue
            s = suggest_pull_forward(db, wc.id, wk, min_idle_hours=min_idle_hours, limit=500)
            idle_total += s.idle_hours
            if s.idle_hours < min_idle_hours:
                continue
            cells += 1
            budget = s.idle_hours
            for r in s.rows:
                if not r.eligible or r.order_id in taken or r.hours > budget + 1e-6:
                    continue
                taken.add(r.order_id)
                budget -= r.hours
                moves.append(PullForwardMove(
                    order_id=r.order_id, order_no=r.order_no, position_no=r.position_no, customer=r.customer, item_code=r.item_code,
                    operation_seq=r.operation_seq, work_center_code=wc.code, from_week=r.from_week, to_week=wk, hours=r.hours, due_date=r.due_date,
                ))
    return PullForwardPlanOut(start_week=start, weeks=req.weeks, idle_hours_total=round(idle_total, 1), idle_cells=cells,
                              moves=moves, moved_hours=round(sum(m.hours for m in moves), 1))


def create_pull_forward_draft(db: Session, req: AutoPlanRequest, username: str, *, min_idle_hours: float = 1.0):
    from app.schemas import PlanRevisionChangeIn, PlanRevisionCreate
    from app.services import plan_revisions as revs

    plan = plan_pull_forward(db, req, min_idle_hours=min_idle_hours)
    if not plan.moves:
        raise ValueError("Atıl kapasiteye öne çekilebilecek uygun iş bulunamadı")
    rev = revs.create_revision(db, PlanRevisionCreate(
        reason_codes=["other"], note=f"Atıl kapasite: tüm ufukta {len(plan.moves)} iş öne çekme ({plan.moved_hours} sa)",
        start_week=req.start_week, weeks=req.weeks, mode=req.mode, material_policy=req.material_policy,
        placement=req.placement, jit_buffer_days=req.jit_buffer_days, work_center_ids=req.work_center_ids, replace_manual=False,
    ), username)
    changes = [PlanRevisionChangeIn(
        entity_type="order", entity_id=m.order_id, extra_key=m.item_code, field="job_move",
        new_value='{"item_code":"%s","start_date":"%s","qty_mode":"remaining","quantity":null}' % (m.item_code, m.to_week.isoformat()),
    ) for m in plan.moves]
    return revs.add_changes_bulk(db, rev.id, changes, username), plan
