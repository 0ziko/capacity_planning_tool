"""Plan revizyonu: taslak girdi, yeniden hesap, onay=uygula, olay kaydi."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import Order, PlanLine, PlanRevision, PlanRevisionChange, PlanRevisionEvent, PlanRevisionSnapshot, WorkCenterWeek
from app.schemas import (
    AutoPlanRequest,
    PlanRevisionChangeIn,
    PlanRevisionChangeOut,
    PlanRevisionCompareOut,
    PlanRevisionCreate,
    PlanRevisionDiffSummary,
    PlanRevisionEventOut,
    PlanRevisionKpis,
    PlanRevisionOrderDiff,
    PlanRevisionOut,
    REVISION_REASON_CODES,
)
from app.services import capacity as cap
from app.services import job_moves as job_moves_svc
from app.services import orders as orders_svc
from app.services import planning
from app.services.plan_input_fingerprint import compute_plan_input_fingerprint
from app.services.plan_input_lock import plan_input_write_lock
from app.services.plan_preflight import plan_preflight

OPEN_STATUSES = ("draft", "calculated")
# Haftalik is gucu taslak alanlari: normal vardiya + fazla mesai (18:00-21:00, kisi basi <= 2.5 sa)
WC_WEEK_FIELDS = ("headcount", "efficient_hours_per_person", "working_days",
                  "overtime_headcount", "overtime_days", "overtime_hours_per_person",
                  "weekend_overtime_headcount", "weekend_overtime_days", "weekend_overtime_hours_per_person")


class RevisionConflictError(Exception):
    """HTTP 409 — veri surumu veya durum uyumsuz."""
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
            elif ch.field in ("efficient_hours_per_person", "overtime_hours_per_person", "weekend_overtime_hours_per_person"):
                parsed = float(val)
            else:
                parsed = int(float(val))
            if ch.field in WC_WEEK_FIELDS:
                setattr(row, ch.field, parsed)
            if ch.field == "overtime_headcount" and not parsed:
                row.overtime_headcount = None
            if ch.field == "weekend_overtime_headcount" and not parsed:
                row.weekend_overtime_headcount = None
            if not revert and "overtime_" in ch.field:
                problem = cap.overtime_violation(
                    row.work_center, row, overtime_headcount=row.overtime_headcount, overtime_days=row.overtime_days,
                    overtime_hours=row.overtime_hours_per_person, emp_count=cap.employee_count(db, row.work_center),
                    weekend_headcount=row.weekend_overtime_headcount, weekend_days=row.weekend_overtime_days,
                    weekend_hours=row.weekend_overtime_hours_per_person, db=db,
                )
                if problem:
                    raise ValueError(f"{row.work_center.code} {week.isoformat()}: {problem}")
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
        if body.field not in WC_WEEK_FIELDS:
            raise ValueError("Haftalik is gucunde gecersiz alan")
        if body.field == "overtime_hours_per_person" and body.new_value not in ("", None) and float(body.new_value) > cap.OVERTIME_MAX_HOURS + 1e-9:
            raise ValueError(f"Kisi basi fazla mesai en fazla {cap.OVERTIME_MAX_HOURS} saat olabilir (18:00-21:00)")
        if body.field == "weekend_overtime_hours_per_person" and body.new_value not in ("", None) and float(body.new_value) > cap.WEEKEND_OVERTIME_MAX_HOURS + 1e-9:
            raise ValueError(f"Kisi basi hafta sonu fazla mesai en fazla {cap.WEEKEND_OVERTIME_MAX_HOURS} saat olabilir (08:00-18:00)")
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
        material_policy=rev.material_policy or "conditional",
        placement=rev.placement or "flow",  # type: ignore[arg-type]
        jit_buffer_days=int(rev.jit_buffer_days if rev.jit_buffer_days is not None else 2),
        slip_mode=(rev.slip_mode or "chain"),  # type: ignore[arg-type]
        use_overtime=bool(rev.use_overtime if rev.use_overtime is not None else True),
        prep_fill=bool(rev.prep_fill if rev.prep_fill is not None else True),
        mode=rev.mode,  # type: ignore[arg-type]
    )


def _row_date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _row_effective_due(row: dict) -> date | None:
    """order_schedule satırı etkin termini taşımaz; bitiş − gecikme farkından geri türetilir."""
    end = _row_date(row.get("planned_end"))
    late = row.get("lateness_days")
    if end is not None and late is not None:
        return end - timedelta(days=int(late))
    return _row_date(row.get("due_date"))


def _job_move_start(value: str) -> date | None:
    try:
        return _row_date((json.loads(value or "{}") or {}).get("start_date"))
    except (ValueError, TypeError):
        return None


def build_order_diffs(
    base_rows: list[dict],
    prop_rows: list[dict],
    changes,
    bumped_orders: list[str],
) -> tuple[list[PlanRevisionOrderDiff], PlanRevisionDiffSummary]:
    """Canlı ve önerilen sipariş takvimini sipariş bazında karşılaştırır.

    Tüm alanlar iki takvimin kesişim/birleşiminden türetilir; ek sorgu yapılmaz, bu yüzden
    hesaplama anındaki anlık görüntüyle (fingerprint) tutarlı kalır.
    """
    base = {int(r["order_id"]): r for r in base_rows if r.get("order_id") is not None}
    prop = {int(r["order_id"]): r for r in prop_rows if r.get("order_id") is not None}
    requested: dict[int, object] = {}
    for ch in changes or []:
        if getattr(ch, "entity_type", "") == "order" and getattr(ch, "field", "") in ("revised_due_date", "job_move"):
            requested[int(ch.entity_id)] = ch
    bumped = set(bumped_orders or [])
    diffs: list[PlanRevisionOrderDiff] = []
    summary = PlanRevisionDiffSummary()
    for oid in sorted(set(base) | set(prop)):
        b, p = base.get(oid, {}), prop.get(oid, {})
        src = p or b
        end_b, end_a = _row_date(b.get("planned_end")), _row_date(p.get("planned_end"))
        st_b, st_a = str(b.get("plan_status") or ""), str(p.get("plan_status") or "")
        ch = requested.get(oid)
        kind = getattr(ch, "field", "") if ch else ""
        due_before = _row_effective_due(b) if b else _row_date(src.get("due_date"))
        if kind == "revised_due_date":
            due_after = _row_date(ch.new_value) or _row_date(src.get("due_date"))
        else:
            due_after = _row_effective_due(p) if p else due_before
        delta = (end_a - end_b).days if end_a and end_b else None
        pushed = bool(delta is not None and delta > 0) or (end_b is not None and end_a is None and st_a in ("unplanned", "partial"))
        pulled = bool(delta is not None and delta < 0)
        # Talep edilen siparişin yeni termine yetişememesi "karşılanamayan" olarak sayılır;
        # "yeni geç" yalnızca talep dışı (yan etki) siparişleri işaretler.
        newly_late = ch is None and st_b in ("on_time", "no_ops", "") and st_a == "late"
        met: bool | None = None
        if ch is not None:
            met = bool(end_a is not None and due_after is not None and end_a <= due_after and st_a not in ("unplanned", "partial"))
        row = PlanRevisionOrderDiff(
            order_id=oid,
            order_no=str(src.get("order_no") or ""),
            position_no=str(src.get("position_no") or ""),
            customer=str(src.get("customer") or ""),
            item_code=str(src.get("item_code") or ""),
            quantity=float(src.get("quantity") or 0),
            due_date=_row_date(src.get("due_date")),
            due_before=due_before,
            due_after=due_after,
            end_before=end_b,
            end_after=end_a,
            delta_days=delta,
            status_before=st_b,
            status_after=st_a,
            lateness_before=b.get("lateness_days") if b else None,
            lateness_after=p.get("lateness_days") if p else None,
            change_kind=kind,
            change_id=getattr(ch, "id", None) if ch else None,
            requested=ch is not None,
            met=met,
            pushed=pushed,
            pulled_forward=pulled,
            newly_late=newly_late,
            bumped=str(src.get("order_no") or "") in bumped,
        )
        diffs.append(row)
        if row.requested:
            summary.requested += 1
            if row.met:
                summary.met += 1
            else:
                summary.unmet += 1
        if row.pushed:
            summary.pushed += 1
        if row.newly_late:
            summary.newly_late += 1
        if row.pulled_forward:
            summary.pulled_forward += 1
        if row.bumped:
            summary.bumped += 1
        if not row.requested and delta in (0, None) and st_a == st_b:
            summary.unchanged += 1

    def _rank(r: PlanRevisionOrderDiff):
        # Önce karşılanamayan talepler, sonra karşılananlar, sonra yeni geç kalanlar, sonra en çok kayanlar.
        if r.requested:
            return (0 if not r.met else 1, -(r.delta_days or 0), r.order_no)
        if r.newly_late:
            return (2, -(r.delta_days or 0), r.order_no)
        if r.pushed or r.bumped:
            return (3, -(r.delta_days or 0), r.order_no)
        if r.pulled_forward:
            return (4, r.delta_days or 0, r.order_no)
        return (5, 0, r.order_no)

    diffs.sort(key=_rank)
    return diffs, summary


def _compare_from_snapshots(rev: PlanRevision) -> PlanRevisionCompareOut | None:
    by_kind = {s.kind: s for s in rev.snapshots}
    if "baseline" not in by_kind or "proposed" not in by_kind:
        return None
    base = json.loads(by_kind["baseline"].payload_json or "{}")
    prop = json.loads(by_kind["proposed"].payload_json or "{}")
    bumped = prop.get("bumped_orders") or []
    diffs, summary = build_order_diffs(base.get("schedule") or [], prop.get("schedule") or [], rev.changes, bumped)
    return PlanRevisionCompareOut(
        baseline=PlanRevisionKpis(**(base.get("kpis") or {})),
        proposed=PlanRevisionKpis(**(prop.get("kpis") or {})),
        schedule_rows=prop.get("schedule") or [],
        bumped_orders=bumped,
        insert_notes=prop.get("insert_notes") or [],
        unplanned=prop.get("unplanned") or [],
        order_diffs=diffs,
        diff_summary=summary,
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
        material_policy=rev.material_policy or "conditional",
        placement=rev.placement or "flow",
        jit_buffer_days=int(rev.jit_buffer_days if rev.jit_buffer_days is not None else 2),
        slip_mode=rev.slip_mode or "chain",
        use_overtime=bool(rev.use_overtime if rev.use_overtime is not None else True),
        prep_fill=bool(rev.prep_fill if rev.prep_fill is not None else True),
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
        input_fingerprint=rev.input_fingerprint or "",
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
        material_policy=body.material_policy,
        placement=body.placement,
        jit_buffer_days=body.jit_buffer_days,
        slip_mode=body.slip_mode,
        use_overtime=body.use_overtime,
        prep_fill=body.prep_fill,
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
    rev.input_fingerprint = ""
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
    rev.input_fingerprint = ""
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
    rev.input_fingerprint = ""
    db.query(PlanRevisionSnapshot).filter(PlanRevisionSnapshot.revision_id == rev.id).delete()
    _add_event(db, rev, "change", username, f"silindi {change_id}")
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def _simulate_revision_preview(db: Session, rev: PlanRevision, req: AutoPlanRequest):
    """Taslak etkisini savepoint icinde uygular; canli veri degismez."""
    nested = db.begin_nested()
    try:
        _apply_changes(db, rev, revert=False)
        check = plan_preflight(db, req)
        if not check.can_plan:
            codes = ", ".join(r.item_code for r in check.no_routing[:8])
            raise ValueError(f"Rotasi olmayan stok var; hesaplanamaz: {codes}")
        moves = job_moves_svc.job_moves_from_revision(rev)
        if moves:
            sim = job_moves_svc.simulate_priority_insert(db, req, moves, replace_manual=bool(rev.replace_manual))
            extra = job_moves_svc.extra_from_sim(sim)
            apply_path = "job_moves"
            keep_line_mode = True
        else:
            sim = planning.simulate(db, req)
            extra = {"unplanned": sim.unplanned, "skipped": sim.skipped,
                     "insert_notes": [f"{n['label']}: {n['detail']}" for n in (sim.placement_notes or [])],
                     "placement_notes": sim.placement_notes or [], "overtime_proposals": sim.overtime_proposals or []}
            apply_path = "auto"
            keep_line_mode = False
        return sim, extra, apply_path, keep_line_mode
    finally:
        nested.rollback()


def delete_changes(db: Session, revision_id: int, change_ids: list[int], username: str) -> PlanRevisionOut:
    """Birden fazla taslak girdisini tek işlemde çıkarır (ör. karşılanamayan talepleri geri çekme)."""
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyondan girdi silinemez")
    wanted = {int(i) for i in change_ids}
    rows = db.query(PlanRevisionChange).filter(PlanRevisionChange.revision_id == rev.id, PlanRevisionChange.id.in_(wanted)).all()
    if len(rows) != len(wanted):
        missing = sorted(wanted - {r.id for r in rows})
        raise ValueError(f"Girdi satiri yok: {', '.join(str(m) for m in missing)}")
    labels = sorted({f"{r.entity_type}.{r.field}" for r in rows})
    for r in rows:
        db.delete(r)
    rev.status = "draft"
    rev.calculated_at = None
    rev.input_fingerprint = ""
    db.query(PlanRevisionSnapshot).filter(PlanRevisionSnapshot.revision_id == rev.id).delete()
    _add_event(db, rev, "change", username, f"toplu silindi {len(rows)} girdi ({', '.join(labels)})")
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def calculate(db: Session, revision_id: int, username: str, *, commit: bool = True) -> PlanRevisionOut:
    """commit=False: arka plan isi kendi islemi icinde sonucu ve is kaydini birlikte kalici yapar."""
    rev = _get(db, revision_id)
    if rev.status not in MUTABLE_STATUSES:
        raise ValueError("Bu revizyon hesaplanamaz")
    req = _req(rev)
    wc_ids = req.work_center_ids
    # Ucuz on kontrol en basta: rotasiz acik siparis varsa dakikalar suren taban takvimi/fingerprint
    # hesabina girmeden ayni hatayla hemen doner (rota eksigi taslak girdilerinden bagimsizdir).
    early = plan_preflight(db, req)
    if not early.can_plan and early.no_routing:
        codes = ", ".join(r.item_code for r in early.no_routing[:8])
        raise ValueError(f"Rotasi olmayan stok var; hesaplanamaz: {codes}")
    input_fp = compute_plan_input_fingerprint(db, req)

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

    sim, extra, apply_path, keep_line_mode = _simulate_revision_preview(db, rev, req)
    proposed_sched = orders_svc.order_schedule(db, wc_ids, lines=sim.lines, orders=sim.orders)
    proposed_kpis = _kpis_from_schedule(proposed_sched)
    proposed_kpis.line_count = len(sim.lines)
    proposed_kpis.planned_hours = round(sim.planned_hours, 2)

    plan_lines = [planning.draft_line_to_dict(l) for l in sim.lines]
    apply_payload = {
        "plan_lines": plan_lines,
        "apply_path": apply_path,
        "keep_line_mode": keep_line_mode,
        "replace_manual": bool(rev.replace_manual),
        "mode": req.mode,
        "start_week": req.start_week.isoformat(),
        "weeks": req.weeks,
        "work_center_ids": wc_ids or [],
        "input_fingerprint": input_fp,
        "unplanned": extra.get("unplanned") or [],
        "placement_notes": extra.get("placement_notes") or [],
        "overtime_proposals": extra.get("overtime_proposals") or [],
    }

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
    db.add(
        PlanRevisionSnapshot(
            revision_id=rev.id,
            kind="apply",
            payload_json=json.dumps(apply_payload, default=str),
        )
    )
    rev.status = "calculated"
    rev.calculated_at = datetime.now()
    rev.input_fingerprint = input_fp
    _add_event(db, rev, "calculate", username, f"{proposed_kpis.line_count} satir, fp={input_fp[:12]}")
    if not commit:
        db.flush()
        db.expire_all()  # silinen/yeni snapshot koleksiyonu ayni islem icinde taze okunsun
        return to_out(_get(db, rev.id))
    db.commit()
    db.expire_all()
    return to_out(_get(db, rev.id))


def revision_preflight(db: Session, revision_id: int):
    rev = _get(db, revision_id)
    req = _req(rev)
    nested = db.begin_nested()
    try:
        _apply_changes(db, rev, revert=False)
        return plan_preflight(db, req)
    finally:
        nested.rollback()
        db.expire_all()


def approve_and_apply(db: Session, revision_id: int, username: str, missing_headcount_ack: str | None = None) -> PlanRevisionOut:
    rev = _get(db, revision_id)
    if rev.status == "applied":
        raise RevisionConflictError("Revizyon zaten uygulandi")
    if rev.status != "calculated":
        raise ValueError("Once yeniden hesaplayin")

    apply_snap = next((s for s in rev.snapshots if s.kind == "apply"), None)
    if not apply_snap or not rev.input_fingerprint:
        raise ValueError("Once yeniden hesaplayin")
    apply_payload = json.loads(apply_snap.payload_json or "{}")
    plan_lines = apply_payload.get("plan_lines") or []
    req = _req(rev)

    with plan_input_write_lock(db, rev.horizon_key):
        current_fp = compute_plan_input_fingerprint(db, req)
        if current_fp != rev.input_fingerprint:
            raise RevisionConflictError("Veri degisti; yeniden hesaplayin")

        check = revision_preflight(db, revision_id)
        if check.missing_headcount_token and missing_headcount_ack != check.missing_headcount_token:
            raise RevisionConflictError("Haftalık kişi sayısı eksik; girişleri düzeltin veya ön kontrolde sıfır kapasiteyi onaylayın.")
        if not check.can_plan:
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

        _apply_changes(db, rev, revert=False)
        result = planning.apply_plan_snapshot(
            db,
            req,
            plan_lines,
            username,
            revision_id=rev.id,
            replace_manual=bool(rev.replace_manual),
            keep_line_mode=bool(apply_payload.get("keep_line_mode")),
            message_tag="revizyon onay snapshot",
            overtime_proposals=apply_payload.get("overtime_proposals") or [],
        )
        now = datetime.now()
        rev.status = "applied"
        rev.approved_by = username
        rev.approved_at = now
        rev.applied_at = now
        _add_event(db, rev, "approve", username, "onayla ve devreye al")
        _add_event(db, rev, "apply", username, result.get("message") or "")
        db.commit()
        try:
            from app.services import plan_report as report_svc
            report_svc.generate_and_store(db, req, {"unplanned": apply_payload.get("unplanned") or [],
                                                     "placement_notes": apply_payload.get("placement_notes") or [],
                                                     "overtime_proposals": apply_payload.get("overtime_proposals") or [],
                                                     "slip_mode": rev.slip_mode or "chain", "created": len(plan_lines)}, kind="revision", username=username, revision_id=rev.id)
            db.commit()
        except Exception:  # rapor onayi bozmaz
            db.rollback()
    db.expire_all()
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
