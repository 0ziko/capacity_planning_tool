"""FAZ 08 — M5 olcum birimleri kabul testleri."""

import io
import uuid
from datetime import date, time

from openpyxl import Workbook

from app.models import Downtime, Item, Order, PlanLine, ProductionActual, RoutingOperation, WorkCenter, WorkCenterShift
from app.services import analysis, excel
from app.services.kpi_units import downtime_labor_minutes, week_plan_and_output_kpis

WK = date(2026, 9, 7)


def _wc(db, code: str | None = None) -> WorkCenter:
    code = code or f"WC-{uuid.uuid4().hex[:8].upper()}"
    wc = WorkCenter(code=code, name=code, is_planned=True, is_active=True, default_efficient_hours=4)
    db.add(wc)
    db.flush()
    db.add(
        WorkCenterShift(
            work_center_id=wc.id,
            name="G",
            weekdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(18, 0),
            headcount=10,
            efficient_hours_per_person=4,
        )
    )
    return wc


def _item_op(db, wc: WorkCenter, code: str) -> RoutingOperation:
    it = Item(code=code, name=code, product_group="T")
    db.add(it)
    db.flush()
    op = RoutingOperation(item_id=it.id, seq=10, operation_name="Op", work_center_id=wc.id, cycle_time_sec=360.0)
    db.add(op)
    db.flush()
    return op


def test_plan_a_unplanned_b_output_does_not_consume_plan(db):
    """Planli A 8 saat; yalniz plansiz B 8 saat uretim -> A kalan 8, merkez ciktisi 8."""
    wc = _wc(db)
    op_a = _item_op(db, wc, "ITEM-A")
    op_b = _item_op(db, wc, "ITEM-B")
    oa = Order(order_no="OA", customer="X", due_date=date(2026, 10, 1), item_id=op_a.item_id, quantity=10, status="open")
    ob = Order(order_no="OB", customer="Y", due_date=date(2026, 10, 1), item_id=op_b.item_id, quantity=10, status="open")
    db.add_all([oa, ob])
    db.flush()
    db.add(
        PlanLine(
            order_id=oa.id,
            operation_id=op_a.id,
            work_center_id=wc.id,
            week_start=WK,
            planned_hours=8.0,
            planned_qty=10,
            mode="auto",
        )
    )
    db.add(
        ProductionActual(
            prod_date=WK,
            work_center_id=wc.id,
            item_id=op_b.item_id,
            operation_seq=op_b.seq,
            order_no="OB",
            quantity=80,
            earned_hours=8.0,
        )
    )
    db.commit()
    k = week_plan_and_output_kpis(db, wc.id, WK, as_of=WK)
    assert k["plan_adherence_remaining_hours"] == 8.0
    assert k["standard_hour_equivalent_output"] == 8.0


def test_downtime_labor_minutes_elapsed_and_direct(db):
    row_el = Downtime(dt_date=WK, work_center_id=1, minutes=30, duration_minutes=30, time_basis="elapsed_minutes", affected_headcount=10)
    row_lb = Downtime(dt_date=WK, work_center_id=1, minutes=30, duration_minutes=30, time_basis="labor_minutes")
    assert downtime_labor_minutes(row_el) == (300.0, "elapsed_minutes")
    assert downtime_labor_minutes(row_lb) == (30.0, "labor_minutes")


def test_legacy_downtime_not_measured_in_analysis(db):
    wc = _wc(db)
    db.add(Downtime(dt_date=WK, work_center_id=wc.id, reason_code="X", minutes=120, time_basis="legacy_unspecified"))
    db.commit()
    a = analysis.downtime_analysis(db, [wc.id], WK, WK)
    assert a["totals"][0]["actual_minutes"] == 0.0


def test_ct_no_suggestion_without_reported_hours(db):
    wc = _wc(db)
    op = _item_op(db, wc, "CT-1")
    db.add(
        ProductionActual(
            prod_date=WK,
            work_center_id=wc.id,
            item_id=op.item_id,
            operation_seq=op.seq,
            quantity=100,
            earned_hours=5.0,
            reported_hours=None,
        )
    )
    db.commit()
    rows = analysis.cycle_time_suggestions(db, [wc.id], WK, WK, min_samples=1, threshold_pct=1.0)
    assert rows == []


def _xlsx(header, rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_export_quality_and_legacy_downtime(db):
    wc = _wc(db)
    op = _item_op(db, wc, "IMP-1")
    db.commit()
    old_prod = [
        {
            "_row": 2,
            "prod_date": WK.isoformat(),
            "semi_finished_code": "",
            "quantity": 5,
            "wc_code": wc.code,
            "item_code": "IMP-1",
            "operation_seq": 10,
            "reported_hours": 1.5,
            "order_no": "",
        }
    ]
    ins, upd, errs = excel.import_production(db, old_prod)
    db.commit()
    assert errs == []
    assert ins + upd >= 1
    pa = db.query(ProductionActual).filter(ProductionActual.work_center_id == wc.id).one()
    assert pa.quality_status == "legacy_unspecified"

    new_prod = [
        {
            "_row": 2,
            "prod_date": WK.isoformat(),
            "semi_finished_code": "",
            "quantity": 5,
            "wc_code": wc.code,
            "item_code": "IMP-1",
            "operation_seq": 10,
            "reported_hours": 2,
            "reported_time_basis": "labor_hours",
            "good_qty": 4,
            "scrap_qty": 1,
            "quality_status": "verified",
            "order_no": "",
        }
    ]
    excel.import_production(db, new_prod)
    db.commit()
    pa = db.query(ProductionActual).filter(ProductionActual.work_center_id == wc.id).one()
    assert pa.good_qty == 4
    assert pa.scrap_qty == 1
    assert pa.reported_time_basis == "labor_hours"

    old_dt = [{"_row": 2, "dt_date": WK.isoformat(), "wc_code": wc.code, "reason_code": "A", "reason_desc": "x", "minutes": 15}]
    ins, _, errs = excel.import_downtime(db, old_dt)
    db.commit()
    assert errs == []
    dt = db.query(Downtime).filter(Downtime.work_center_id == wc.id).one()
    assert dt.time_basis == "legacy_unspecified"
