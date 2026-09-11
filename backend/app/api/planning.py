"""Kapasite, ihtiyac, planlama, ilerleme, analiz ve raporlar."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import require_poweruser, require_user
from app.db.session import get_db
from app.models import PlanLine, User, WorkCenter
from app.schemas import (
    AutoPlanRequest,
    CapacityOut,
    GanttOut,
    ForecastFromLeadTimeIn,
    ForecastSummaryOut,
    LeadTimeOut,
    LeadTimeRequest,
    LoadDetailOut,
    ManualPlanLineIn,
    MergeGroup,
    MergeImpactOut,
    MergeImpactRequest,
    MergeRequest,
    PlanPreflightOut,
    ProductionBatchCreate,
    ProductionBatchOut,
    OrderProgressOut,
    OrderScheduleOut,
    PlanCompareOut,
    PlanCompareRequest,
    JobMovePreviewOut,
    PlanLineOut,
    PlanRevisionChangeIn,
    PlanRevisionChangesBulkIn,
    PlanRevisionCreate,
    PlanRevisionOut,
    ProgressOut,
    RequirementLine,
    RequirementQuery,
    RevenueOut,
    WeeklyOutputOut,
    WorkCenterLoad,
)
from app.services import analysis, capacity, excel, gantt, planning, progress, requirements, revenue
from app.services import merge_impact as merge_impact_svc
from app.services import orders as orders_svc
from app.services import plan_preflight as preflight_svc
from app.services import job_moves as job_moves_svc
from app.services import plan_revisions as revisions_svc
from app.services import production_batches as pbatches

router = APIRouter(prefix="/api", tags=["planning"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _wcs(db: Session, ids: list[int] | None, only_planned: bool = False) -> list[WorkCenter]:
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if ids:
        q = q.filter(WorkCenter.id.in_(ids))
    elif only_planned:
        q = q.filter(WorkCenter.is_planned.is_(True))
    return q.order_by(WorkCenter.code).all()


# ---- Capacity ----
@router.get("/capacity", response_model=list[CapacityOut])
def get_capacity(
    start: date,
    end: date | None = None,
    work_center_ids: list[int] | None = Query(None),
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    start = capacity.week_start(start)
    end = end or (start + timedelta(days=6))
    return [capacity.capacity_for_range(db, w, start, end) for w in _wcs(db, work_center_ids)]


# ---- Requirements ----
@router.post("/requirements", response_model=dict)
def get_requirements(q: RequirementQuery, db: Session = Depends(get_db), _=Depends(require_user)):
    lines: list[RequirementLine] = requirements.requirement_lines(db, q)
    return {"lines": lines, "by_work_center": requirements.summarize_by_work_center(lines), "total_hours": round(sum(l.hours for l in lines), 2)}


@router.get("/requirements/item", response_model=dict)
def item_hours(item_code: str, quantity: float = 1, db: Session = Depends(get_db), _=Depends(require_user)):
    return requirements.item_total_hours(db, item_code, quantity)


# ---- Planning ----
@router.post("/plan/auto/preflight", response_model=PlanPreflightOut)
def auto_plan_preflight(req: AutoPlanRequest, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    return preflight_svc.plan_preflight(db, req)


@router.post("/plan/auto", response_model=dict)
def run_auto_plan(req: AutoPlanRequest, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    check = preflight_svc.plan_preflight(db, req)
    if not check.can_plan:
        codes = ", ".join(r.item_code for r in check.no_routing[:10])
        raise HTTPException(400, f"Acik siparislerde rotasi olmayan stok kodlari var; planlama yapilamaz: {codes}")
    return planning.auto_plan(db, req, user.username)


def _revision_error(exc: ValueError):
    msg = str(exc)
    if "acik taslak" in msg:
        raise HTTPException(409, msg)
    raise HTTPException(400, msg)


@router.get("/plan/revisions", response_model=list[PlanRevisionOut])
def list_plan_revisions(
    status: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    return revisions_svc.list_revisions(db, status=status)


@router.get("/plan/revisions/move-preview", response_model=JobMovePreviewOut)
def preview_job_move(
    order_id: int,
    work_center_ids: list[int] | None = Query(None),
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    try:
        return job_moves_svc.preview_move(db, order_id, work_center_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/plan/revisions", response_model=PlanRevisionOut)
def create_plan_revision(body: PlanRevisionCreate, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        return revisions_svc.create_revision(db, body, user.username)
    except ValueError as e:
        _revision_error(e)


@router.get("/plan/revisions/{revision_id}", response_model=PlanRevisionOut)
def get_plan_revision(revision_id: int, db: Session = Depends(get_db), _=Depends(require_user)):
    try:
        return revisions_svc.to_out(revisions_svc._get(db, revision_id))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/plan/revisions/{revision_id}/changes", response_model=PlanRevisionOut)
def add_plan_revision_change(
    revision_id: int,
    body: PlanRevisionChangeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_poweruser),
):
    try:
        return revisions_svc.add_change(db, revision_id, body, user.username)
    except ValueError as e:
        _revision_error(e)


@router.post("/plan/revisions/{revision_id}/changes/bulk", response_model=PlanRevisionOut)
def add_plan_revision_changes_bulk(
    revision_id: int,
    body: PlanRevisionChangesBulkIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_poweruser),
):
    try:
        return revisions_svc.add_changes_bulk(db, revision_id, body.changes, user.username)
    except ValueError as e:
        _revision_error(e)


@router.delete("/plan/revisions/{revision_id}/changes/{change_id}", response_model=PlanRevisionOut)
def delete_plan_revision_change(
    revision_id: int,
    change_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_poweruser),
):
    try:
        return revisions_svc.delete_change(db, revision_id, change_id, user.username)
    except ValueError as e:
        _revision_error(e)


@router.post("/plan/revisions/{revision_id}/calculate", response_model=PlanRevisionOut)
def calculate_plan_revision(revision_id: int, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        return revisions_svc.calculate(db, revision_id, user.username)
    except ValueError as e:
        _revision_error(e)


@router.post("/plan/revisions/{revision_id}/approve", response_model=PlanRevisionOut)
def approve_plan_revision(revision_id: int, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        return revisions_svc.approve_and_apply(db, revision_id, user.username)
    except ValueError as e:
        _revision_error(e)


@router.post("/plan/revisions/{revision_id}/reject", response_model=PlanRevisionOut)
def reject_plan_revision(
    revision_id: int,
    note: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_poweruser),
):
    try:
        return revisions_svc.reject(db, revision_id, user.username, note)
    except ValueError as e:
        _revision_error(e)


@router.post("/plan/revisions/{revision_id}/cancel", response_model=PlanRevisionOut)
def cancel_plan_revision(revision_id: int, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        return revisions_svc.cancel(db, revision_id, user.username)
    except ValueError as e:
        _revision_error(e)


@router.post("/plan/manual", response_model=PlanLineOut)
def add_manual(line: ManualPlanLineIn, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        pl = planning.add_manual_line(db, line, user.username)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return next(x for x in planning.plan_lines(db, [pl.work_center_id], pl.week_start, pl.week_start) if x.id == pl.id)


@router.get("/plan/lines", response_model=list[PlanLineOut])
def get_plan_lines(
    start: date | None = None,
    end: date | None = None,
    work_center_ids: list[int] | None = Query(None),
    mode: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    return planning.plan_lines(db, work_center_ids, start, end, mode)


@router.patch("/plan/lines/{line_id}", response_model=dict)
def move_plan_line(line_id: int, week_start: date | None = None, planned_hours: float | None = None, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    pl = db.get(PlanLine, line_id)
    if not pl:
        raise HTTPException(404, "Plan satiri bulunamadi")
    if week_start:
        pl.week_start = capacity.week_start(week_start)
    if planned_hours is not None:
        pl.planned_hours = planned_hours
    pl.mode = "manual"
    db.commit()
    return {"ok": True}


@router.delete("/plan/lines/{line_id}", status_code=204)
def delete_plan_line(line_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    pl = db.get(PlanLine, line_id)
    if not pl:
        raise HTTPException(404, "Plan satiri bulunamadi")
    db.delete(pl)
    db.commit()


@router.delete("/plan/lines", status_code=204)
def clear_plan(start: date, mode: str | None = None, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_poweruser)):
    q = db.query(PlanLine).filter(PlanLine.week_start >= capacity.week_start(start))
    if mode:
        q = q.filter(PlanLine.mode == mode)
    if work_center_ids:
        q = q.filter(PlanLine.work_center_id.in_(work_center_ids))
    q.delete(synchronize_session=False)
    db.commit()


@router.get("/plan/load", response_model=list[WorkCenterLoad])
def get_load(start: date, weeks: int = Query(12, ge=1, le=52), work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    return planning.load(db, work_center_ids, start, weeks)


@router.get("/plan/gantt", response_model=GanttOut)
def get_gantt(
    work_center_id: int = Query(...),
    start: date = Query(...),
    end: date = Query(...),
    as_of: date | None = Query(None),
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    try:
        return gantt.plan_gantt(db, work_center_id, start, end, as_of)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/plan/load/detail", response_model=LoadDetailOut)
def get_load_detail(work_center_id: int, week_start: date, db: Session = Depends(get_db), _=Depends(require_user)):
    try:
        return planning.load_detail(db, work_center_id, week_start)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/plan/load/detail.xlsx")
def load_detail_xlsx(work_center_id: int, week_start: date, db: Session = Depends(get_db), _=Depends(require_user)):
    try:
        detail = planning.load_detail(db, work_center_id, week_start)
    except ValueError as e:
        raise HTTPException(400, str(e))
    content = excel.build_load_detail_xlsx(detail)
    return _xlsx(content, f"is_listesi_{detail.work_center_code}_{week_start}.xlsx")


@router.get("/plan/weekly-output", response_model=WeeklyOutputOut)
def get_weekly_output(
    week_start: date,
    work_center_ids: list[int] | None = Query(None),
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    try:
        return planning.weekly_output(db, week_start, work_center_ids or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/plan/weekly-output.xlsx")
def weekly_output_xlsx(
    week_start: date,
    work_center_ids: list[int] | None = Query(None),
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    try:
        out = planning.weekly_output(db, week_start, work_center_ids or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    content = excel.build_weekly_output_xlsx(out)
    return _xlsx(content, f"uretim_plani_{out.week_start}.xlsx")


@router.post("/plan/leadtime/forecast", response_model=dict)
def add_leadtime_forecast(req: ForecastFromLeadTimeIn, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        order_id, n = planning.add_forecast_from_leadtime(db, req, user.username)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "created": n,
        "order_id": order_id,
        "message": f"{n} tahmin plan satiri eklendi; sonraki terminlemelerde doluluk hesaba katilir.",
    }


@router.get("/plan/forecast", response_model=list[ForecastSummaryOut])
def get_forecasts(db: Session = Depends(get_db), _=Depends(require_user)):
    return planning.list_forecasts(db)


@router.delete("/plan/forecast/{order_id}", status_code=204)
def delete_forecast_order(order_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    try:
        planning.delete_forecast(db, order_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/plan/forecast", status_code=204)
def clear_forecast(work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_poweruser)):
    planning.clear_forecast_plans(db, work_center_ids)


@router.post("/plan/leadtime", response_model=LeadTimeOut)
def get_lead_time(req: LeadTimeRequest, db: Session = Depends(get_db), _=Depends(require_user)):
    try:
        return planning.lead_time(db, req)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---- Ciro ve plan karsilastirma ----
@router.get("/plan/revenue", response_model=RevenueOut)
def get_revenue(start: date, weeks: int = Query(12, ge=1, le=52), work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    return revenue.revenue_report(db, work_center_ids, start, weeks)


@router.post("/plan/compare", response_model=PlanCompareOut)
def compare_plans(req: PlanCompareRequest, db: Session = Depends(get_db), _=Depends(require_user)):
    return revenue.compare(db, req)


# ---- Siparis bazli plan sonucu (bitis tarihleri) ----
@router.get("/plan/orders", response_model=list[OrderScheduleOut])
def get_order_schedule(work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    return orders_svc.order_schedule(db, work_center_ids)


# ---- Uretim partisi (eski ad: birlestirme) ----
@router.get("/plan/merge-suggestions", response_model=list[MergeGroup])
def get_merge_suggestions(tolerance_days: int = Query(5, ge=0, le=365), db: Session = Depends(get_db), _=Depends(require_user)):
    return pbatches.batch_suggestions(db, tolerance_days)


@router.get("/plan/production-batches", response_model=list[ProductionBatchOut])
def get_production_batches(status: str = "open", db: Session = Depends(get_db), _=Depends(require_user)):
    return pbatches.list_batches(db, status)


@router.post("/plan/merge/impact", response_model=MergeImpactOut)
def preview_merge_impact(req: MergeImpactRequest, db: Session = Depends(get_db), _=Depends(require_user)):
    try:
        auto = AutoPlanRequest(
            start_week=req.start_week,
            weeks=req.weeks,
            work_center_ids=req.work_center_ids,
            replace_existing=True,
            mode=req.mode,
        )
        return merge_impact_svc.preview_merge_impact(db, req.merge_groups, auto)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/plan/merge", response_model=ProductionBatchOut, status_code=201)
def create_production_batch(req: MergeRequest, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    try:
        batch = pbatches.create_batch(
            db,
            ProductionBatchCreate(order_ids=req.order_ids, batch_no=req.order_no, due_date=req.due_date, note=req.note),
            user.username,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return pbatches.batch_out(batch)


@router.delete("/plan/merge/{batch_id}", response_model=dict)
def dissolve_production_batch(batch_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    try:
        n = pbatches.dissolve_batch(db, batch_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "reopened": n}


# ---- Progress ----
@router.get("/progress", response_model=list[ProgressOut])
def get_progress(week: date, as_of: date | None = None, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    return [progress.week_progress(db, w, week, as_of) for w in _wcs(db, work_center_ids)]


@router.get("/progress/orders", response_model=list[OrderProgressOut])
def get_order_progress(as_of: date | None = None, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    return orders_svc.order_progress(db, work_center_ids, as_of)


@router.get("/progress/orders.xlsx")
def order_progress_xlsx(as_of: date | None = None, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    rows = orders_svc.order_progress(db, work_center_ids, as_of)
    content = excel.build_report(
        {
            "Sipariş İlerleme": (
                ["Sipariş No", "Poz No", "Müşteri", "Stok Kodu", "Miktar", "Termin", "İhtiyaç (saat)", "Kazanılan (saat)", "Çıkan Miktar", "İlerleme %", "Durum", "İlk Üretim", "Son Üretim"],
                [[r.order_no, r.position_no, r.customer, r.item_code, r.quantity, r.due_date, r.required_hours, r.earned_hours, r.produced_qty, r.pct, r.status, r.first_prod_date, r.last_prod_date] for r in rows],
            ),
            "Operasyon Detayı": (
                ["Sipariş No", "Poz No", "Stok Kodu", "Op. Sıra", "Operasyon", "İş Merkezi", "İhtiyaç (saat)", "Planlanan (saat)", "Üretilen Miktar", "Kazanılan (saat)", "İlerleme %"],
                [[r.order_no, r.position_no, r.item_code, o.operation_seq, o.operation_name, o.work_center_code, o.required_hours, o.planned_hours, o.produced_qty, o.earned_hours, o.pct] for r in rows for o in r.ops],
            ),
        }
    )
    return _xlsx(content, f"siparis_ilerleme_{as_of or date.today()}.xlsx")


@router.get("/progress/daily", response_model=list[dict])
def get_progress_daily(week: date, work_center_id: int, db: Session = Depends(get_db), _=Depends(require_user)):
    wc = db.get(WorkCenter, work_center_id)
    if not wc:
        raise HTTPException(404, "Is merkezi bulunamadi")
    return progress.daily_series(db, wc, week)


# ---- Downtime analysis ----
@router.get("/analysis/downtime", response_model=dict)
def get_downtime(start: date, end: date, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    return analysis.downtime_analysis(db, work_center_ids, start, end)


@router.get("/analysis/downtime.xlsx")
def downtime_xlsx(start: date, end: date, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    a = analysis.downtime_analysis(db, work_center_ids, start, end)
    content = excel.build_report(
        {
            "Özet": (["İş Merkezi", "Beklenen Duruş (dk)", "Gerçekleşen Duruş (dk)", "Fazla Duruş (dk)"], [[t["work_center_code"], t["expected_minutes"], t["actual_minutes"], t["excess_minutes"]] for t in a["totals"]]),
            "Günlük": (["İş Merkezi", "Gün", "Beklenen (dk)", "Gerçekleşen (dk)", "Fazla (dk)"], [[d["work_center_code"], d["day"], d["expected_minutes"], d["actual_minutes"], d["excess_minutes"]] for d in a["daily"]]),
            "Sebep Kırılımı": (["İş Merkezi", "Sebep Kodu", "Sebep", "Süre (dk)", "Adet", "Pay %", "Fazla Duruş Payı (dk)"], [[r["work_center_code"], r["reason_code"], r["reason_desc"], r["minutes"], r["count"], r["share_pct"], r["excess_attributed_minutes"]] for r in a["reasons"]]),
        }
    )
    return _xlsx(content, f"durus_raporu_{start}_{end}.xlsx")


# ---- Cycle time suggestions ----
@router.get("/analysis/cycletime", response_model=dict)
def get_cycletime(
    start: date | None = None,
    end: date | None = None,
    work_center_ids: list[int] | None = Query(None),
    min_samples: int = 10,
    threshold_pct: float = 10.0,
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    rows = analysis.cycle_time_suggestions(db, work_center_ids, start, end, min_samples, threshold_pct)
    return {"rows": rows, "groups": analysis.group_suggestions(rows)}


@router.get("/analysis/cycletime.xlsx")
def cycletime_xlsx(
    start: date | None = None,
    end: date | None = None,
    work_center_ids: list[int] | None = Query(None),
    min_samples: int = 10,
    threshold_pct: float = 10.0,
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    rows = analysis.cycle_time_suggestions(db, work_center_ids, start, end, min_samples, threshold_pct)
    groups = analysis.group_suggestions(rows)
    content = excel.build_report(
        {
            "Çevrim Süresi Önerileri": (
                ["Stok Kodu", "Ürün Grubu", "İş Merkezi", "Operasyon", "Tanımlı CT (sn)", "Gözlenen Medyan (sn)", "Min", "Max", "Örnek", "Sapma %", "Önerilen CT (sn)", "Durum"],
                [[r["item_code"], r["product_group"], r["work_center_code"], r["operation_seq"], r["defined_ct_sec"], r["observed_median_ct_sec"], r["observed_min_ct_sec"], r["observed_max_ct_sec"], r["samples"], r["deviation_pct"], r["suggested_ct_sec"], r["status"]] for r in rows],
            ),
            "Ürün Grubu Özeti": (["Ürün Grubu", "İş Merkezi", "Stok Sayısı", "Örnek", "Ort. Sapma %"], [[g["product_group"], g["work_center_code"], g["items"], g["samples"], g["avg_deviation_pct"]] for g in groups]),
        }
    )
    return _xlsx(content, "cevrim_suresi_onerileri.xlsx")


# ---- Plan export ----
@router.get("/plan/export.xlsx")
def plan_xlsx(start: date, weeks: int = 12, work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    loads = planning.load(db, work_center_ids, start, weeks)
    lines = planning.plan_lines(db, work_center_ids, capacity.week_start(start), capacity.week_start(start) + timedelta(weeks=weeks))
    sched = orders_svc.order_schedule(db, work_center_ids)
    rev = revenue.revenue_report(db, work_center_ids, start, weeks, sched=sched)
    content = excel.build_report(
        {
            "Haftalık Yük": (["İş Merkezi", "Hafta", "Kapasite (saat)", "Planlanan (saat)", "Doluluk %", "Kapasite (birim)", "Planlanan (birim)"], [[l.work_center_code, w.week_start, w.capacity_hours, w.planned_hours, round(w.utilization * 100, 1), w.capacity_units, w.planned_units] for l in loads for w in l.weeks]),
            "Sipariş Bitiş Tarihleri": (
                ["Sipariş No", "Müşteri", "Stok Kodu", "Miktar", "Birim Fiyat", "Ciro", "Termin", "İhtiyaç (saat)", "Planlanan (saat)", "Kapsam %", "Plan Başlangıç Haftası", "Tahmini Bitiş", "Son İş Merkezi", "Sapma (gün)", "Durum"],
                [[s.order_no, s.customer, s.item_code, s.quantity, s.unit_price, s.revenue, s.due_date, s.required_hours, s.planned_hours, s.coverage_pct, s.planned_start, s.planned_end, s.last_work_center_code, s.lateness_days, s.plan_status] for s in sched],
            ),
            "Ciro (Haftalık)": (["Hafta", "Tamamlanan Ciro", "Tamamlanan Sipariş", "Oransal Ciro", "Kümülatif Tamamlanan", "Kümülatif Oransal"], [[w.period, w.completed_revenue, w.completed_orders, w.earned_revenue, w.cumulative_completed, w.cumulative_earned] for w in rev.weeks]),
            "Ciro (Aylık)": (["Ay", "Tamamlanan Ciro", "Tamamlanan Sipariş", "Oransal Ciro", "Kümülatif Tamamlanan", "Kümülatif Oransal"], [[m.period, m.completed_revenue, m.completed_orders, m.earned_revenue, m.cumulative_completed, m.cumulative_earned] for m in rev.months]),
            "Plan Satırları": (["Hafta", "İş Merkezi", "Sipariş No", "Müşteri", "Termin", "Stok Kodu", "Op. Sıra", "Planlanan Saat", "Planlanan Miktar", "Mod"], [[p.week_start, p.work_center_code, p.order_no, p.customer, p.due_date, p.item_code, p.operation_seq, p.planned_hours, p.planned_qty, p.mode] for p in lines]),
        }
    )
    return _xlsx(content, f"plan_{capacity.week_start(start)}.xlsx")
