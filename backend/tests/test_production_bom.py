"""Production BOM import ve WIP planlama."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from app.models import Item, RoutingOperation, WorkCenter
from app.services.bom_tree import is_raw_material, is_wip_step
from app.services.production_bom import GOLDEN_FGS, run_production_bom_import
from tests.test_capacity_flow import _upload

BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")
STATIONS_PATH = Path(r"C:\Users\ozan.deniz\Desktop\İstasyonlar.xlsx")


def _monday(offset_weeks: int = 0) -> str:
    d = date.today()
    return (d - timedelta(days=d.weekday()) + timedelta(weeks=offset_weeks)).isoformat()


@pytest.mark.skipif(not BOM_PATH.is_file(), reason="BOM.xlsx masaustunde yok")
def test_golden_fg_import(db):
    from app.services.stations import import_stations, load_stations_xlsx

    if STATIONS_PATH.is_file():
        rows = load_stations_xlsx(STATIONS_PATH)
        import_stations(db, rows, replace_missing=False)
        db.commit()

    r = run_production_bom_import(db, path=BOM_PATH, fg_filter=set(GOLDEN_FGS))
    db.commit()
    assert r.fg_count == len(GOLDEN_FGS), r.errors
    for fg in GOLDEN_FGS:
        it = db.query(Item).filter(Item.code == fg).first()
        assert it is not None, fg
        assert it.operations, f"{fg} rota yok"
    fg6 = db.query(Item).filter(Item.code == "6000006").first()
    assert fg6 and fg6.bom_lines
    assert any(is_raw_material(bl.component_code) for bl in fg6.bom_lines), "6000006 hammadde yok"
    assert any(bl.component_code.startswith("5001848-") for bl in fg6.bom_lines if is_wip_step(bl.component_code)), "5001848 adimlari yok"


def test_wip_planning_parallel(client, auth, db):
    """Yari mamul + bitis rotasi: bitis dal onceki dallar bitmeden baslamaz."""
    from app.models import WorkCenterShift
    from datetime import time

    wc = WorkCenter(code="MNT", name="MONTAJ", is_planned=True, is_active=True, default_efficient_hours=8)
    db.add(wc)
    db.flush()
    db.add(WorkCenterShift(work_center_id=wc.id, name="Gunduz", weekdays="0,1,2,3,4", start_time=time(8, 0), end_time=time(18, 0), headcount=10, efficient_hours_per_person=8))
    db.flush()
    fg = Item(code="6999999", name="Test FG", product_group="T")
    wip_a = Item(code="5999991", name="WIP A", product_group="T")
    wip_b = Item(code="5999992", name="WIP B", product_group="T")
    db.add_all([fg, wip_a, wip_b])
    db.flush()
    db.add(RoutingOperation(item_id=wip_a.id, seq=10, operation_name="OP-A", work_center_id=wc.id, cycle_time_sec=3600))
    db.add(RoutingOperation(item_id=wip_b.id, seq=10, operation_name="OP-B", work_center_id=wc.id, cycle_time_sec=3600))
    db.add(RoutingOperation(item_id=fg.id, seq=10, operation_name="MONTAJ", work_center_id=wc.id, cycle_time_sec=1800))
    from app.models import BomLine

    db.add(BomLine(item_id=fg.id, component_code="5999991", quantity=1, source_wip="5999991"))
    db.add(BomLine(item_id=fg.id, component_code="5999992", quantity=1, source_wip="5999992"))
    db.commit()

    _upload(client, auth, "orders", ["Sipariş No", "Termin", "Stok Kodu", "Miktar"], [["S-WIP-1", "2026-12-01", "6999999", 1]])
    wk = _monday()
    plan = client.post(
        "/api/plan/auto",
        headers=auth,
        json={"start_week": wk, "weeks": 4, "mode": "due_date", "replace_existing": True},
    ).json()
    assert plan["created"] >= 3, plan
    lines = client.get("/api/plan/lines", headers=auth, params={"start": wk, "mode": "auto"}).json()
    finish = [l for l in lines if l.get("semi_finished_code") in ("", None) and l["item_code"] == "6999999"]
    wip_lines = [l for l in lines if l.get("semi_finished_code")]
    assert len(wip_lines) >= 2
    if finish and wip_lines:
        finish_week = min(l["week_start"] for l in finish)
        wip_last = max(l["week_start"] for l in wip_lines)
        assert finish_week >= wip_last
