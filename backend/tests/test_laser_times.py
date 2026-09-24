"""Lazer sure standardi: carpansiz CT, setup, ERP recete yeniden yuklemede korunma, siparis yukune yansima."""

from datetime import date

from app.core.config import get_settings
from app.models import Item, LaserTimeStandard, Order, RoutingOperation
from app.services.bom_tree import explode_order
from app.services.production_bom import Branch, ParsedFG, ParsedOp, import_parsed_fg
from tests.test_capacity_flow import _upload

import pytest

HEADER = ["Yarımamül Op. Kodu", "Çevrim Süresi (sn)", "Setup (dk)", "Not"]


@pytest.fixture(autouse=True)
def _clean_standards(db):
    """Test DB oturum boyunca paylasilir; standartlar diger testlerin BOM aktarimini etkilemesin."""
    db.query(LaserTimeStandard).delete()
    db.commit()
    yield
    db.rollback()
    db.query(LaserTimeStandard).delete()
    db.commit()


def _op(wip, code, name, sure, wc, sira):
    return ParsedOp(listing_sira=sira, wip=wip, wip_op_code=f"{wip}-{code}", wip_name=f"{wip}-{name}", op_code=code,
                    op_name=name, sure_dk=sure, station="", alt_station="", wc_name=wc, materials=[])


def _ctx():
    return {"cache": {}, "wc_idx": {}, "machines": {}, "counters": {"created": 0, "updated": 0, "routes": 0, "bom": 0}, "warnings": []}


def _import_fg(db, fg, branches, finish, **kw):
    import_parsed_fg(db, ParsedFG(fg=fg, name=f"FG {fg}", branches=branches, finish_wip=finish), **_ctx(), **kw)
    db.flush()


def _two_fgs_sharing_a_part(db, **kw):
    """6900001: ozel tabla (5900001) + ortak parca (5900009); 6900002: sadece ortak parca."""
    shared = Branch(wip="5900009-13", instance=1, ops=[_op("5900009", "12", "LAZER KESME", 1.0, "LAZER", 20),
                                                     _op("5900009", "13", "BUKUM", 0.5, "BUKUM", 21)])
    tabla = Branch(wip="5900001-13", instance=1, ops=[_op("5900001", "12", "LAZER KESME", 2.0, "LAZER", 10),
                                                    _op("5900001", "42", "2.LAZER KESME", 1.0, "LAZER", 11),
                                                    _op("5900001", "13", "BUKUM", 0.5, "BUKUM", 12)])
    fin1 = Branch(wip="5000001-50", instance=1, ops=[_op("5000001", "50", "PAKETLEME", 1.0, "PAKETLEME", 1)])
    fin2 = Branch(wip="5000002-50", instance=1, ops=[_op("5000002", "50", "PAKETLEME", 1.0, "PAKETLEME", 1)])
    _import_fg(db, "6900001", [tabla, shared, fin1], "5000001-50", **kw)
    _import_fg(db, "6900002", [shared, fin2], "5000002-50", **kw)


def _op_by_code(db, code):
    return db.query(RoutingOperation).filter(RoutingOperation.semi_finished_code == code).one()


def _laser_unit_hours(db, fg_code):
    """Bitmis urunun 1 adedi icin lazer is merkezindeki toplam (setup haric) ve setup saatleri."""
    fg = db.query(Item).filter(Item.code == fg_code).one()
    order = Order(order_no=f"T-{fg_code}", item_id=fg.id, quantity=1, due_date=date(2026, 12, 1), status="open")
    order.item = fg
    jobs = explode_order(db, order)
    run = setup = 0.0
    for job in jobs.wip_jobs:
        for op in job.item.operations:
            if op.work_center.name == "LAZER":
                run += op.hours_for(job.quantity) - op.setup_time_min / 60.0
                setup += op.setup_time_min / 60.0
    return run, setup


def test_bom_import_uses_factor_without_standard(db):
    _two_fgs_sharing_a_part(db)
    factor = get_settings().bom_cycle_factor
    assert _op_by_code(db, "5900001-12").cycle_time_sec == round(2.0 * 60 * factor, 2)
    assert _op_by_code(db, "5900001-12").setup_time_min == 0


def test_laser_import_writes_ct_and_setup_without_factor(client, auth, db):
    _two_fgs_sharing_a_part(db)
    db.commit()
    body = _upload(client, auth, "laser_times", HEADER, [
        ["5900001-12", 150, 30, "Olculmus"],
        ["5900001-42", 50, 10, ""],
        ["5900009-12", 60, 15, ""],
    ])
    assert body["inserted"] == 3
    db.expire_all()
    op = _op_by_code(db, "5900001-12")
    assert op.cycle_time_sec == 150 and op.setup_time_min == 30
    # Diger operasyonlar etkilenmez (ERP SURE x 1.6 kalir)
    assert _op_by_code(db, "5900001-13").cycle_time_sec == round(0.5 * 60 * get_settings().bom_cycle_factor, 2)
    # Planlama yuku setup'i bir kez icerir: 10 adet -> 10 x 150 sn + 30 dk
    assert abs(op.hours_for(10) - (10 * 150 / 3600 + 0.5)) < 1e-9


def test_unit_laser_time_per_finished_product(client, auth, db):
    """Birim adet icin bitmis urun lazer toplami = urun ozel + ortak operasyonlar; setup toplami da tutar."""
    _two_fgs_sharing_a_part(db)
    db.commit()
    _upload(client, auth, "laser_times", HEADER, [["5900001-12", 150, 30, ""], ["5900001-42", 50, 10, ""], ["5900009-12", 60, 15, ""]])
    db.expire_all()
    run1, setup1 = _laser_unit_hours(db, "6900001")
    run2, setup2 = _laser_unit_hours(db, "6900002")
    assert abs(run1 * 3600 - 260) < 1e-6 and abs(setup1 * 60 - 55) < 1e-6
    assert abs(run2 * 3600 - 60) < 1e-6 and abs(setup2 * 60 - 15) < 1e-6


def test_standard_survives_bom_reimport(client, auth, db):
    _two_fgs_sharing_a_part(db)
    db.commit()
    _upload(client, auth, "laser_times", HEADER, [["5900001-12", 150, 30, ""], ["5900009-12", 60, 15, ""]])
    from app.services.laser_times import standards_by_code

    _two_fgs_sharing_a_part(db, laser_standards=standards_by_code(db))
    db.commit()
    db.expire_all()
    assert (_op_by_code(db, "5900001-12").cycle_time_sec, _op_by_code(db, "5900001-12").setup_time_min) == (150, 30)
    assert (_op_by_code(db, "5900009-12").cycle_time_sec, _op_by_code(db, "5900009-12").setup_time_min) == (60, 15)
    # Standardi olmayan lazer operasyonu ERP degerinde kalir
    assert _op_by_code(db, "5900001-42").cycle_time_sec == round(1.0 * 60 * get_settings().bom_cycle_factor, 2)


def test_unknown_code_is_kept_and_reported(client, auth, db):
    r = client.post("/api/imports/laser_times", headers=auth,
                    files={"file": ("l.xlsx", __import__("tests.test_capacity_flow", fromlist=["_xlsx"])._xlsx(HEADER, [["5999999-12", 100, 20, ""]]), "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 1
    assert any("henuz yok" in e for e in body["errors"])
    assert db.query(LaserTimeStandard).filter(LaserTimeStandard.semi_finished_code == "5999999-12").one().cycle_time_sec == 100


def test_invalid_rows_rejected(client, auth, db):
    from tests.test_capacity_flow import _xlsx

    r = client.post("/api/imports/laser_times", headers=auth,
                    files={"file": ("l.xlsx", _xlsx(HEADER, [["5999998-12", 0, 20, ""], ["5999997-12", 50, -5, ""]]), "application/octet-stream")})
    body = r.json()
    assert body["inserted"] == 0
    assert len([e for e in body["errors"] if e.startswith("Satir")]) == 2
