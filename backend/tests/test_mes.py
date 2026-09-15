from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from openpyxl import Workbook
from app.models import Item, BomLine, WorkCenter, WorkCenterWeek, Machine, RoutingOperation, Order, PlanLine, StockReceipt
from app.models.mes import MesDetail, MesPlanBaseline
from app.services import mes, mes_progress
from app.services.remaining_work import produced_qty_map

DAY = date(2026, 9, 14)


def workbook(rows):
    wb = Workbook()
    wb.active.append(["Üretim Detay Id", "Tarih", "Malzeme Kodu", "Net Üretilen Miktar", "İş Merkezi Kodu",
                      "Sağlam Adet", "Üretilen Miktar", "Başlangıç", "Müşteri", "Süre"])
    for row in rows:
        wb.active.append([*row, -999, 9999, "IGNORE", "IGNORE", 9999])
        for cell in wb.active[wb.active.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def case(db):
    token = uuid4().hex[:7]
    wc = WorkCenter(code="MES-" + token, name="MES", is_planned=True)
    db.add(wc); db.flush()
    machine = Machine(code="M-" + token, work_center_id=wc.id)
    db.add(machine)
    db.add(WorkCenterWeek(work_center_id=wc.id, week_start=DAY, headcount=10,
                          efficient_hours_per_person=4, working_days=5))
    items, ops, orders = [], [], []
    # Numeric FG/WIP codes, unique across this persistent test database.
    suffix = str(int(token, 16)).zfill(9)
    shared = "5" + suffix + "-01"
    for i in range(2):
        item = Item(code="6" + suffix + str(i), name="Test FG")
        db.add(item); db.flush()
        finish = "5" + suffix + str(i) + "-50"
        db.add(BomLine(item_id=item.id, component_code=shared, source_wip=finish, recipe_seq=10, quantity=1))
        first = RoutingOperation(item_id=item.id, seq=10, operation_name="Kesim", semi_finished_code=shared,
                                 work_center_id=wc.id, cycle_time_sec=3600)
        final = RoutingOperation(item_id=item.id, seq=20, operation_name="Montaj", semi_finished_code=finish,
                                 work_center_id=wc.id, cycle_time_sec=1800)
        db.add_all([first, final]); db.flush()
        order = Order(order_no=f"MES-{token}-{i}", customer="irrelevant", item_id=item.id,
                      due_date=DAY + timedelta(days=7 * (1-i)), quantity=100)
        db.add(order); db.flush()
        items.append(item); ops.append((first, final)); orders.append(order)
    db.commit()
    return dict(wc=wc, machine=machine, items=items, ops=ops, orders=orders, shared=shared)


def import_rows(db, case, rows):
    content = workbook([[id, day, code, qty, case["machine"].code] for id, day, code, qty in rows])
    p = mes.preview(db, content)
    result = mes.apply_import(db, content, p["token"], "test")
    db.commit()
    return result, content


def plan(db, c, i, wk, qty, final=False):
    op = c["ops"][i][1 if final else 0]
    p = PlanLine(order_id=c["orders"][i].id, operation_id=op.id, work_center_id=c["wc"].id,
                 week_start=wk, planned_qty=qty, planned_hours=qty * (0.5 if final else 1), mode="auto")
    db.add(p); db.commit()
    return p


def test_reader_uses_only_net_and_date_and_ignores_broken_styles():
    source = workbook([["X1", DAY, "500001-01", 12, "M1"]])
    out = BytesIO()
    with ZipFile(BytesIO(source)) as z, ZipFile(out, "w", ZIP_DEFLATED) as target:
        for name in z.namelist():
            target.writestr(name, b"<invalid/>" if name == "xl/styles.xml" else z.read(name))
    r = mes.read_mes(out.getvalue())[0]
    assert r["quantity"] == 12 and r["prod_date"] == DAY
    assert "customer" not in r


@pytest.mark.parametrize("qty", [-1, "NaN", "inf", ""])
def test_invalid_quantity_rejected(qty):
    with pytest.raises(ValueError):
        mes.read_mes(workbook([["x", DAY, "500001-01", qty, "M"]]))


def test_duplicate_id_rejects_whole_file():
    with pytest.raises(ValueError, match="tekrarlı"):
        mes.read_mes(workbook([["x", DAY, "500001-01", 1, "M"], ["x", DAY, "500001-01", 2, "M"]]))


def test_idempotent_details_and_amendment_no_daily_collapse(db, case):
    result, content = import_rows(db, case, [("a", DAY, case["shared"], 3), ("b", DAY, case["shared"], 4)])
    assert result["counts"]["new"] == 2 and result["counts"]["unresolved"] == 0
    assert mes.preview(db, content)["counts"]["unchanged"] == 2
    assert db.query(MesDetail).count() == 2
    import_rows(db, case, [("a", DAY, case["shared"], 6)])
    assert mes.balances([mes.detail_dict(d) for d in db.query(MesDetail).all()])[case["shared"]] == 10


def test_finished_receipt_and_consumption_update_once(db, case):
    fg = case["items"][0].code
    import_rows(db, case, [("w", DAY, case["shared"], 10), ("f", DAY, fg, 4)])
    receipt = db.query(StockReceipt).filter_by(source="mes").one()
    assert receipt.quantity == 4
    result, _ = import_rows(db, case, [("f", DAY, fg, 6)])
    assert db.query(StockReceipt).filter_by(source="mes").count() == 1
    assert receipt.quantity == 6
    assert mes.balances([mes.detail_dict(d) for d in db.query(MesDetail).all()])[case["shared"]] == 4
    assert result["counts"]["updated"] == 1


def test_stale_preview_cannot_overwrite_changed_record(db, case):
    import_rows(db, case, [("a", DAY, case["shared"], 1)])
    target = workbook([["a", DAY, case["shared"], 9, case["machine"].code]])
    p = mes.preview(db, target)
    import_rows(db, case, [("a", DAY, case["shared"], 2)])
    with pytest.raises(ValueError, match="Önizlemeyi"):
        mes.apply_import(db, target, p["token"], "test")


def test_ambiguous_times_preserved_unresolved_without_earned_hours(db, case):
    case["ops"][1][0].cycle_time_sec = 7200
    db.commit()
    p, _ = import_rows(db, case, [("a", DAY, case["shared"], 10)])
    assert p["counts"]["unresolved"] == 1
    assert p["standard_hours"] == 0
    assert mes.balances([mes.detail_dict(d) for d in db.query(MesDetail).all()]) == {}


def test_weekly_plan_early_output_and_frozen_baseline(db, case):
    current = plan(db, case, 0, DAY, 5)
    plan(db, case, 1, DAY + timedelta(weeks=1), 10)
    import_rows(db, case, [("a", DAY, case["shared"], 12), ("b", DAY + timedelta(days=1), case["shared"], 1)])
    r = mes_progress.progress(db, DAY)
    assert r["summary"]["actual_hours"] == 12
    assert r["summary"]["matched_hours"] == 5
    assert r["summary"]["early_hours"] == 7
    assert r["summary"]["capacity_usage_pct"] == 30
    assert r["daily"][1]["hours"] == 0
    current.planned_qty = 999
    db.commit()
    assert mes_progress.progress(db, DAY)["operations"][0]["quantity"] == 5
    future = mes_progress.progress(db, DAY + timedelta(weeks=1))
    assert future["operations"][0]["early_qty"] == 8
    assert future["operations"][0]["remaining_qty"] == 2
    assert future["summary"]["actual_hours"] == 0


def test_common_pool_prioritizes_finished_week_not_customer_due_date(db, case):
    # Earlier due order is item 1, but item 0 reaches finished goods this week.
    plan(db, case, 0, DAY, 30, final=True)
    plan(db, case, 1, DAY + timedelta(weeks=1), 40, final=True)
    import_rows(db, case, [("a", DAY, case["shared"], 50)])
    allocations = []
    credit, _ = produced_qty_map(db, as_of=DAY, mes_allocations=allocations)
    assert credit[(case["orders"][0].id, case["ops"][0][0].id)] == 30
    assert credit[(case["orders"][1].id, case["ops"][1][0].id)] == 20
    assert sum(a["quantity"] for a in allocations) == 50
    assert allocations[0]["finish_week"] == DAY.isoformat()


def test_split_finish_plan_cannot_take_all_stock_before_other_fg(db, case):
    plan(db, case, 0, DAY, 10, final=True)
    plan(db, case, 0, DAY + timedelta(weeks=2), 90, final=True)
    plan(db, case, 1, DAY + timedelta(weeks=1), 40, final=True)
    import_rows(db, case, [("a", DAY, case["shared"], 60)])
    allocations = []
    produced_qty_map(db, as_of=DAY, mes_allocations=allocations)
    assert [(a["finished_item_code"], a["quantity"]) for a in allocations] == [
        (case["items"][0].code, 10), (case["items"][1].code, 40), (case["items"][0].code, 10)]


def test_no_finish_plan_leaves_common_pool_unassigned(db, case):
    import_rows(db, case, [("a", DAY, case["shared"], 50)])
    credits, _ = produced_qty_map(db, as_of=DAY)
    assert sum(credits.values()) == 0


def test_api_preview_does_not_write_and_confirmation_required(client, auth, db, case):
    content = workbook([["api1", DAY, case["shared"], 7, case["machine"].code]])
    files = {"file": ("mes.xlsx", content)}
    response = client.post("/api/mes/preview", files=files, headers=auth)
    assert response.status_code == 200, response.text
    assert db.query(MesDetail).count() == 0
    assert client.post("/api/mes/import", files=files, headers=auth).status_code == 422
    result = client.post("/api/mes/import", files=files, params={"token": response.json()["token"]}, headers=auth)
    assert result.status_code == 200, result.text
    assert db.query(MesDetail).count() == 1
    assert client.get("/api/mes/progress", params={"as_of": DAY}, headers=auth).status_code == 200


def test_finished_consumption_cannot_be_credited_to_second_product(db, case):
    plan(db, case, 1, DAY, 100, final=True)
    import_rows(db, case, [("w", DAY, case["shared"], 50), ("f", DAY, case["items"][0].code, 40)])
    allocations = []
    credits, _ = produced_qty_map(db, as_of=DAY, mes_allocations=allocations)
    assert credits[(case["orders"][0].id, case["ops"][0][1].id)] == 40
    assert sum(a["quantity"] for a in allocations) == 10
    assert allocations[0]["finished_item_code"] == case["items"][1].code


def test_batch_credits_whole_batch_not_only_anchor_order(db, case):
    from app.models import ProductionBatch, ProductionBatchOrder
    first = case["orders"][0]
    other = Order(order_no="MES-extra-" + uuid4().hex[:6], item_id=first.item_id, due_date=DAY + timedelta(days=20), quantity=100)
    batch = ProductionBatch(batch_no="MES-batch-" + uuid4().hex[:6], item_id=first.item_id, due_date=DAY, quantity=200)
    db.add_all([other, batch]); db.flush()
    db.add_all([ProductionBatchOrder(batch_id=batch.id, order_id=first.id, quantity=100),
                ProductionBatchOrder(batch_id=batch.id, order_id=other.id, quantity=100)])
    p = plan(db, case, 0, DAY, 200, final=True)
    p.production_batch_id = batch.id
    db.commit()
    import_rows(db, case, [("a", DAY, case["shared"], 150)])
    allocations = []
    credits, _ = produced_qty_map(db, as_of=DAY, mes_allocations=allocations)
    assert credits[(first.id, case["ops"][0][0].id)] == 150
    assert sum(a["quantity"] for a in allocations) == 150


def test_standard_hours_are_linear_for_multi_unit_machine_cycles(db, case):
    for first, _ in case["ops"]:
        first.time_basis = "machine_seconds_per_cycle"
        first.units_per_cycle = 4
        first.crew_size = 2
    db.commit()
    p, _ = import_rows(db, case, [("a", DAY, case["shared"], 3)])
    # 3 units × (3600 sec / 4 units × 2 people) = 1.5 standard hours.
    assert p["standard_hours"] == 1.5


def test_receipt_reduction_cannot_break_reservations(client, auth, db, case):
    from app.models import Reservation
    import_rows(db, case, [("f", DAY, case["items"][0].code, 10)])
    db.add(Reservation(item_id=case["items"][0].id, order_id=case["orders"][0].id, quantity=8))
    db.commit()
    content = workbook([["f", DAY, case["items"][0].code, 5, case["machine"].code]])
    p = mes.preview(db, content)
    response = client.post("/api/mes/import", files={"file": ("mes.xlsx", content)}, params={"token": p["token"]}, headers=auth)
    assert response.status_code == 409
    db.expire_all()
    assert db.get(MesDetail, "f").quantity == 10
    assert db.query(StockReceipt).filter_by(source="mes").one().quantity == 10


def test_export_is_filtered_and_identifiers_are_literal(client, auth, db, case):
    from openpyxl import load_workbook
    import_rows(db, case, [("=1+1", DAY, case["shared"], 7)])
    response = client.get("/api/mes/progress.xlsx", params={"as_of": DAY, "work_center_ids": case["wc"].id}, headers=auth)
    assert response.status_code == 200, response.text
    wb = load_workbook(BytesIO(response.content))
    assert wb["Plan dışı"]["A2"].value == "=1+1"
    assert wb["Plan dışı"]["A2"].data_type == "s"
    assert wb["Günlük"].max_row == 2
    assert wb["Günlük"]["B2"].value == 7


def test_sunday_included_and_missing_capacity_has_no_fake_percentage(db, case):
    sunday = DAY + timedelta(days=6)
    import_rows(db, case, [("a", sunday, case["shared"], 2)])
    r = mes_progress.progress(db, sunday)
    assert r["daily"][6]["hours"] == 2
    assert r["summary"]["output_vs_plan_pct"] is None
