"""Plan revizyonu: taslak girdi, yeniden hesap, onay=uygula, olay kaydi."""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import Order, PlanLine, PlanRevision, PlanRevisionChange, PlanRevisionEvent, PlanRevisionSnapshot, WorkCenterWeek
from app.schemas import (
    AutoPlanRequest,
    PlanRevisionChangeIn,
    PlanRevisionChangeOut,
    PlanRevisionCompareOut,
    PlanRevisionCreate,
    PlanRevisionEventOut,
    PlanRevisionKpis,
    PlanRevisionOut,
    REVISION_REASON_CODES,
)
from app.services import capacity as cap
from app.services import job_moves as job_moves_svc
from app.services import orders as orders_svc
from app.services import planning
from app.services.plan_preflight import plan_preflight

OPEN_STATUSES = ("draft", "calculated")
MUTABLE_STATUSES = ("draft", "calculated")


def horizon_key(start: date, weeks: int, wc_ids: list[int] | None) -> str:
    ids = ",".join(str(i) for i in sorted(wc_ids or []))
    return f"{start.isoformat()}|{weeks}|{ids}"


def _codes(raw: str) -> list[str]:
    return [c for c in (raw or "").split(",") if c]


def _ids(raw: str) -> list[int]:
    try:
        data = json.loads(raw or "[]")
        return [int(x) for x in data]
    except (TypeError, ValueError):
        return []


def _add_event(db: Session, rev: PlanRevision, action: str, username: str, detail: str = "") -> None:
    db.add(PlanRevisionEvent(revision_id=rev.id, action=action, username=username, detail=detail[:512]))


def _next_no(db: Session) -> str:
    year = datetime.now().year
    prefix = f"REV-{year}-"
    last = (
        db.query(func.max(PlanRevision.revision_no))
        .filter(PlanRevision.revision_no.like(f"{prefix}%"))
        .scalar()
    )
    n = 1
    if last:
        try:
            n = int(str(last).split("-")[-1]) + 1
        except ValueError:
            n = 1
    return f"{prefix}{n:04d}"


def _get(db: Session, revision_id: int) -> PlanRevision:
    rev = (
        db.query(PlanRevision)
        .options(
            selectinload(PlanRevision.changes),
            selectinload(PlanRevision.events),
            selectinload(PlanRevision.snapshots),
        )
        .filter(PlanRevision.id == revision_id)
        .first()
    )
    if not rev:
        raise ValueError("Revizyon bulunamadi")
    return rev


def _kpis_from_schedule(rows) -> PlanRevisionKpis:
    return PlanRevisionKpis(
        planned_hours=round(sum(getattr(r, "planned_hours", 0) or 0 for r in rows), 2),
        line_count=0,
        late=sum(1 for r in rows if getattr(r, "plan_status", "") == "late"),
        on_time=sum(1 for r in rows if getattr(r, "plan_status", "") == "on_time"),
        unplanned=sum(1 for r in rows if getattr(r, "plan_status", "") == "unplanned"),
        partial=sum(1 for r in rows if getattr(r, "plan_status", "") == "partial"),
    )


def _snapshot_payload(kpis: PlanRevisionKpis, schedule_rows: list, extra: dict | None = None) -> dict:
    body = {
        "kpis": kpis.model_dump(),
        "schedule": schedule_rows,
    }
    if extra:
        body.update(extra)
    return body


def _apply_changes(db: Session, rev: PlanRevision, *, revert: bool = False) -> None:
    for ch in rev.changes:
        val = ch.old_value if revert else ch.new_value
        if ch.entity_type == "order":
            if ch.field == "job_move":
                continue
            order = db.query(Order).filter(Order.id == ch.entity_id).first()
            if not order or ch.field != "revised_due_date":
                continue
            order.revised_due_date = date.fromisoformat(val) if val else None
        elif ch.entity_type == "wc_week":
            parts = (ch.extra_key or "").split("|")
            if len(parts) != 2:
                continue
            wc_id = int(parts[0])
            week = date.fromisoformat(parts[1])
            row = (
                db.query(WorkCenterWeek)
                .filter(WorkCenterWeek.work_center_id == wc_id, WorkCenterWeek.week_start == week)
                .first()
            )
            if row is None and revert:
                continue
            if row is None:
                row = WorkCenterWeek(work_center_id=wc_id, week_start=week)
                db.add(row)
                db.flush()
            parsed: int | float | None
            if val in ("", None):
                parsed = None
            elif ch.field == "efficient_hours_per_person":
                parsed = float(val)
            else:
                parsed = int(float(val))
            if ch.field in ("headcount", "efficient_hours_per_person", "working_days"):
                setattr(row, ch.field, parsed)
    db.flush()


def _current_field_value(db: Session, body: PlanRevisionChangeIn) -> str:
    if body.entity_type == "order":
        order = db.query(Order).filter(Order.id == body.entity_id).first()
        if not order:
            raise ValueError("Siparis bulunamadi")
        if body.field == "job_move":
            payload, old = job_moves_svc.validate_and_payload(db, body.entity_id, body.new_value, body.extra_key)
            body.new_value = payload
            body.extra_key = json.loads(payload).get("item_code") or body.extra_key
            return old
        if body.field != "revised_due_date":
            raise ValueError("Sipariste yalnizca revised_due_date veya job_move degistirilebilir")
        return order.revised_due_date.isoformat() if order.revised_due_date else ""
    if body.entity_type == "wc_week":
        if body.field not in ("headcount", "efficient_hours_per_person", "working_days"):
            raise ValueError("Haftalik is gucunde gecersiz alan")
        parts = (body.extra_key or "").split("|")
        if len(parts) != 2:
            raise ValueError("wc_week extra_key wc_id|hafta (Pazartesi) olmali")
        wc_id = int(parts[0])
        week = cap.week_start(date.fromisoformat(parts[1]))
        body.extra_key = f"{wc_id}|{week.isoformat()}"
        row = (
            db.query(WorkCenterWeek)
            .filter(WorkCenterWeek.work_center_id == wc_id, WorkCenterWeek.week_start == week)
            .first()
        )
        if not row:
            return ""
        raw = getattr(row, body.field)
        return "" if raw is None else str(raw)
    raise ValueError("entity_type order veya wc_week olmali")


def _req(rev: PlanRevision) -> AutoPlanRequest:
    return AutoPlanRequest(
        start_week=rev.start_week,
        weeks=rev.weeks,
        work_center_ids=_ids(rev.wc_ids_json) or None,
        replace_existing=True,
        mode=rev.mode,  # type: ignore[arg-type]
    )


def _compare_from_snapshots(rev: PlanRevision) -> PlanRevisionCompareOut | None:
    by_kind = {s.kind: s for s in rev.snapshots}
    if "baseline" not in by_kind or "proposed" not in by_kind:
        return None
    base = json.loads(by_kind["baseline"].payload_json or "{}")
    prop = json.loads(by_kind["proposed"].payload_json or "{}")
    return PlanRevisionCompareOut(
        baseline=PlanRevisionKpis(**(base.get("kpis") or {})),
        proposed=PlanRevisionKpis(**(prop.get("kpis") or {})),
        schedule_rows=prop.get("schedule") or [],
        bumped_orders=prop.get("bumped_orders") or [],
        insert_notes=prop.get("insert_notes") or [],
        unplanned=prop.get("unplanned") or [],
    )


def to_out(rev: PlanRevision, *, apply_message: str = "") -> PlanRevisionOut:
    return PlanRevisionOut(
        id=rev.id,
        revision_no=rev.revision_no,
        status=rev.status,
        reason_codes=_codes(rev.reason_codes),
        note=rev.note or "",
        start_week=rev.start_week,
        weeks=rev.weeks,
        mode=rev.mode,
        work_center_ids=_ids(rev.wc_ids_json),
        replace_manual=bool(rev.replace_manual),
        created_by=rev.created_by,
        created_at=rev.created_at,
        calculated_at=rev.calculated_at,
        approved_by=rev.approved_by or "",
        approved_at=rev.approved_at,
        applied_at=rev.applied_at,
        rejected_by=rev.rejected_by or "",
        reject_note=rev.reject_note or "",
        changes=[
            PlanRevisionChangeOut(
                id=c.id,
                entity_type=c.entity_type,
                entity_id=c.entity_id,
                extra_key=c.extra_key,
                field=c.field,
                old_value=c.old_value,
                new_value=c.new_value,
            )
            for c in rev.changes
        ],
        events=[
            PlanRevisionEventOut(
                id=e.id,
                action=e.action,
                username=e.username,
                detail=e.detail,
                created_at=e.created_at,
            )
            for e in sorted(rev.events, key=lambda x: x.id)
        ],
        compare=_compare_from_snapshots(rev),
        apply_message=apply_message,
    )


def list_revisions(db: Session, status: str | None = None, limit: int = 80) -> list[PlanRevisionOut]:
    q = db.query(PlanRevision).options(
        selectinload(PlanRevision.changes),
        selectinload(PlanRevision.events),
        selectinload(PlanRevision.snapshots),
    )
    if status:
        q = q.filter(PlanRevision.status == status)
    rows = q.order_by(PlanRevision.id.desc()).limit(limit).all()
    return [to_out(r) for r in rows]


def create_revision(db: Session, body: PlanRevisionCreate, username: str) -> PlanRevisionOut:
    reasons = [c for c in body.reason_codes if c in REVISION_REASON_CODES]
    if not reasons:
        raise ValueError("En az bir gecerli neden secin")
    start = cap.week_start(body.start_week)
    wc_ids = list(body.work_center_ids or [])
    key = horizon_key(start, body.weeks, wc_ids or None)
    open_rev = (
        db.query(PlanRevision)
        .filter(PlanRevision.horizon_key == key, PlanRevision.status.in_(OPEN_STATUSES))
        .first()
    )
    if open_rev:
        raise ValueError(f"Bu ufuk icin acik taslak var: {open_rev.revision_no}")
    rev = PlanRevision(
        revision_no=_next_no(db),
        status="draft",
        reason_codes=",".join(reasons),
        note=body.note or "",
        start_week=start,
        weeks=body.weeks,
        mode=body.mode,
        wc_ids_json=json.dumps(wc_ids),
        horizon_key=key,
        replace_manual=body.replace_manual,
        created_by=username,
    )
    db.add(rev)
    db.flush()
    _add_event(db, rev, "create", username, ",".join(reasons))
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def _remove_existing_change(db: Session, rev: PlanRevision, body: PlanRevisionChangeIn) -> None:
    q = db.query(PlanRevisionChange).filter(
        PlanRevisionChange.revision_id == rev.id,
        PlanRevisionChange.entity_type == body.entity_type,
        PlanRevisionChange.field == body.field,
    )
    if body.entity_type == "order":
        q = q.filter(PlanRevisionChange.entity_id == body.entity_id)
        if body.field == "job_move":
            q = q.filter(PlanRevisionChange.extra_key == (body.extra_key or ""))
    elif body.entity_type == "wc_week":
        q = q.filter(PlanRevisionChange.extra_key == (body.extra_key or ""))
    q.delete()


def _append_change(db: Session, rev: PlanRevision, body: PlanRevisionChangeIn) -> None:
    old = _current_field_value(db, body)
    _remove_existing_change(db, rev, body)
    row = PlanRevisionChange(
        revision_id=rev.id,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        extra_key=body.extra_key or "",
        field=body.field,
        old_value=old,
        new_value=body.new_value or "",
    )
    db.add(row)
    rev.changes.append(row)


def add_change(db: Session, revision_id: int, body: PlanRevisionChangeIn, username: str) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyona girdi eklenemez")
    _append_change(db, rev, body)
    rev.status = "draft"
    rev.calculated_at = None
    db.query(PlanRevisionSnapshot).filter(PlanRevisionSnapshot.revision_id == rev.id).delete()
    _add_event(db, rev, "change", username, f"{body.entity_type}.{body.field}")
    db.commit()
    db.expire_all()
    return to_out(_get(db, revision_id))


def add_changes_bulk(db: Session, revision_id: int, bodies: list[PlanRevisionChangeIn], username: str) -> PlanRevisionOut:
    if not bodies:
        raise ValueError("En az bir girdi gerekli")
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyona girdi eklenemez")
    for body in bodies:
        _append_change(db, rev, body)
    rev.status = "draft"
    rev.calculated_at = None
    db.query(PlanRevisionSnapshot).filter(PlanRevisionSnapshot.revision_id == rev.id).delete()
    detail = f"toplu {len(bodies)} girdi"
    kinds = sorted({f"{b.entity_type}.{b.field}" for b in bodies})
    if len(kinds) <= 3:
        detail = f"{detail} ({', '.join(kinds)})"
    _add_event(db, rev, "change", username, detail)
    db.commit()
    db.expire_all()
    return to_out(_get(db, revision_id))


def delete_change(db: Session, revision_id: int, change_id: int, username: str) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyondan girdi silinemez")
    ch = db.query(PlanRevisionChange).filter(PlanRevisionChange.id == change_id, PlanRevisionChange.revision_id == rev.id).first()
    if not ch:
        raise ValueError("Girdi satiri yok")
    db.delete(ch)
    rev.status = "draft"
    rev.calculated_at = None
    db.query(PlanRevisionSnapshot).filter(PlanRevisionSnapshot.revision_id == rev.id).delete()
    _add_event(db, rev, "change", username, f"silindi {change_id}")
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def calculate(db: Session, revision_id: int, username: str) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyon hesaplanamaz")
    req = _req(rev)
    wc_ids = req.work_center_ids
    baseline_sched = orders_svc.order_schedule(db, wc_ids)
    baseline_kpis = _kpis_from_schedule(baseline_sched)
    live_lines = (
        db.query(PlanLine)
        .filter(PlanLine.week_start >= rev.start_week, PlanLine.mode.in_(["auto", "manual"]))
        .all()
    )
    if wc_ids:
        live_lines = [p for p in live_lines if p.work_center_id in wc_ids]
    baseline_kpis.line_count = len(live_lines)
    baseline_kpis.planned_hours = round(sum(p.planned_hours or 0 for p in live_lines), 2)

    _apply_changes(db, rev, revert=False)
    check = plan_preflight(db, req)
    if not check.can_plan:
        _apply_changes(db, rev, revert=True)
        db.commit()
        codes = ", ".join(r.item_code for r in check.no_routing[:8])
        raise ValueError(f"Rotasi olmayan stok var; hesaplanamaz: {codes}")

    moves = job_moves_svc.job_moves_from_revision(rev)
    if moves:
        sim = job_moves_svc.simulate_priority_insert(db, req, moves, replace_manual=bool(rev.replace_manual))
        extra = job_moves_svc.extra_from_sim(sim)
    else:
        sim = planning.simulate(db, req)
        extra = {"unplanned": sim.unplanned, "skipped": sim.skipped}
    proposed_sched = orders_svc.order_schedule(db, wc_ids, lines=sim.lines, orders=sim.orders)
    proposed_kpis = _kpis_from_schedule(proposed_sched)
    proposed_kpis.line_count = len(sim.lines)
    proposed_kpis.planned_hours = round(sim.planned_hours, 2)

    _apply_changes(db, rev, revert=True)

    db.query(PlanRevisionSnapshot).filter(PlanRevisionSnapshot.revision_id == rev.id).delete()
    db.add(
        PlanRevisionSnapshot(
            revision_id=rev.id,
            kind="baseline",
            payload_json=json.dumps(
                _snapshot_payload(baseline_kpis, [s.model_dump(mode="json") for s in baseline_sched]),
                default=str,
            ),
        )
    )
    db.add(
        PlanRevisionSnapshot(
            revision_id=rev.id,
            kind="proposed",
            payload_json=json.dumps(
                _snapshot_payload(
                    proposed_kpis,
                    [s.model_dump(mode="json") for s in proposed_sched],
                    extra=extra,
                ),
                default=str,
            ),
        )
    )
    rev.status = "calculated"
    rev.calculated_at = datetime.now()
    _add_event(db, rev, "calculate", username, f"{proposed_kpis.line_count} satir")
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def approve_and_apply(db: Session, revision_id: int, username: str) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status != "calculated":
        raise ValueError("Once yeniden hesaplayin")
    req = _req(rev)
    _apply_changes(db, rev, revert=False)
    check = plan_preflight(db, req)
    if not check.can_plan:
        _apply_changes(db, rev, revert=True)
        db.commit()
        raise ValueError("Rotasi olmayan stok var; uygulanamaz")

    others = (
        db.query(PlanRevision)
        .filter(
            PlanRevision.horizon_key == rev.horizon_key,
            PlanRevision.status == "applied",
            PlanRevision.id != rev.id,
        )
        .all()
    )
    for other in others:
        other.status = "superseded"

    moves = job_moves_svc.job_moves_from_revision(rev)
    if moves:
        sim = job_moves_svc.simulate_priority_insert(db, req, moves, replace_manual=bool(rev.replace_manual))
        if not rev.replace_manual:
            sim.lines = [l for l in sim.lines if l.mode != "manual"]
        result = planning.write_simulation(
            db,
            req,
            sim,
            username,
            revision_id=rev.id,
            commit=False,
            replace_manual=bool(rev.replace_manual),
            message_tag="revizyon, is tasima",
            keep_line_mode=True,
        )
        bumped = getattr(sim, "bumped_orders", None) or []
        if bumped:
            result["message"] = f"{result.get('message') or ''} Kaydirilan: {', '.join(bumped)}.".strip()
    else:
        result = planning.auto_plan(
            db,
            req,
            username,
            revision_id=rev.id,
            commit=False,
            replace_manual=bool(rev.replace_manual),
        )
    now = datetime.now()
    rev.status = "applied"
    rev.approved_by = username
    rev.approved_at = now
    rev.applied_at = now
    _add_event(db, rev, "approve", username, "onayla ve devreye al")
    _add_event(db, rev, "apply", username, result.get("message") or "")
    db.commit()
    return to_out(_get(db, rev.id), apply_message=result.get("message") or "")


def reject(db: Session, revision_id: int, username: str, note: str) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyon reddedilemez")
    rev.status = "rejected"
    rev.rejected_by = username
    rev.reject_note = (note or "")[:512]
    _add_event(db, rev, "reject", username, rev.reject_note)
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def cancel(db: Session, revision_id: int, username: str) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyon iptal edilemez")
    rev.status = "cancelled"
    _add_event(db, rev, "cancel", username, "")
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))
