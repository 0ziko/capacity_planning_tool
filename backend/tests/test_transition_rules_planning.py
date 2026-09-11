"""B2/B3: oncul miktarlari ve gecis kurallari haftalik planda."""

from datetime import date, timedelta

from app.db.session import SessionLocal
from app.models import Item, OpTransitionRule, RoutingOperation, WorkCenter, WorkCenterShift
from app.schemas import AutoPlanRequest
from app.services import planning
from app.services import operation_constraints as opcon
from app.services import scenarios as scen
from datetime import time

from tests.test_capacity_flow import _upload
from tests.test_revenue_modes import _setup

START = date(2026, 9, 7)


def _simulate(client, auth, db, **params):
    session = SessionLocal()
    try:
        return planning.simulate(session, AutoPlanRequest(start_week=START.isoformat(), weeks=3, replace_existing=True, **params))
    finally:
        session.close()


def _two_wc_setup(client, auth, db):
    for b in client.get("/api/plan/production-batches", headers=auth).json():
        client.delete(f"/api/plan/merge/{b['id']}", headers=auth)
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})
    _upload(
        client,
        auth,
        "workcenters",
        ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"],
        [["OP1-WC", "Op1", "E", 10, 4], ["OP2-WC", "Op2", "E", 10, 4]],
    )
    _upload(
        client,
        auth,
        "shifts",
        ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
        [["OP1-WC", "G", "0,1,2,3,4", "08:00", "18:00", 10, 4], ["OP2-WC", "G", "0,1,2,3,4", "08:00", "18:00", 10, 4]],
    )
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["TR-ITEM", "Test", "G"]])
    _upload(
        client,
        auth,
        "routing",
        ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"],
        [["TR-ITEM", 10, "Op1", "OP1-WC", 3600], ["TR-ITEM", 20, "Op2", "OP2-WC", 3600]],
    )
    wc1 = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "OP1-WC")
    wc2 = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "OP2-WC")
    return wc1["id"], wc2["id"]


def test_predecessor_zero_capacity_blocks_successor(client, auth, db):
    """Oncul kapasite 0 -> ardil plan miktarı 0 (B2)."""
    wc1, wc2 = _two_wc_setup(client, auth, db)
    wc1_row = db.query(WorkCenter).filter(WorkCenter.id == wc1).one()
    db.query(WorkCenterShift).filter(WorkCenterShift.work_center_id == wc1).delete()
    db.add(
        WorkCenterShift(
            work_center_id=wc1,
            name="G",
            weekdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(18, 0),
            headcount=0,
            efficient_hours_per_person=4,
        )
    )
    db.commit()

    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "TR-B2", "due_date": "2026-09-20", "item_code": "TR-ITEM", "quantity": 10},
    )
    sim = _simulate(client, auth, db, work_center_ids=[wc1, wc2])
    ops = db.query(RoutingOperation).join(Item).filter(Item.code == "TR-ITEM").order_by(RoutingOperation.seq).all()
    op2_lines = [l for l in sim.lines if l.operation_id == ops[1].id]
    assert sum(l.planned_qty for l in op2_lines) == 0.0
    assert any(u.get("reason") == "oncul_eksik" for u in sim.unplanned)


def test_finish_wait_14_days_not_same_week(client, auth, db):
    """finish + 14 gun bekleme -> iki op ayni haftada baslayamaz (B3)."""
    wc1, wc2 = _two_wc_setup(client, auth, db)
    client.put(
        "/api/scenarios/rules",
        headers=auth,
        json={
            "scope": "group",
            "product_group": "G",
            "from_op": "Op1",
            "to_op": "Op2",
            "rule": "finish",
            "wait_minutes": 20160,
        },
    )
    client.post(
        "/api/orders",
        headers=auth,
        json={"order_no": "TR-B3", "due_date": "2026-09-20", "item_code": "TR-ITEM", "quantity": 10},
    )
    sim = _simulate(client, auth, db, work_center_ids=[wc1, wc2])
    ops = db.query(RoutingOperation).join(Item).filter(Item.code == "TR-ITEM").order_by(RoutingOperation.seq).all()
    op1_weeks = {l.week_start for l in sim.lines if l.operation_id == ops[0].id}
    op2_weeks = {l.week_start for l in sim.lines if l.operation_id == ops[1].id}
    assert op1_weeks
    assert not op2_weeks.intersection(op1_weeks)


def test_cycles_trigger_weeks():
    """Hafta1 20 + hafta2 80 oncul -> lag 5 ve 50 farkli tetik haftasi."""
    rule5 = scen.Rule(rule="cycles", lag_cycles=5)
    rule50 = scen.Rule(rule="cycles", lag_cycles=50)
    weeks = [START + timedelta(weeks=i) for i in range(3)]
    pred_cum = {0: 20.0, 1: 80.0}
    assert opcon.trigger_week_index(rule5, 100, pred_cum, weeks) == 0
    assert opcon.trigger_week_index(rule50, 100, pred_cum, weeks) == 1


def test_finish_partial_predecessor_blocks_successor(client, auth, db):
    """finish: 20/100 oncul -> ardil baslamaz."""
    rule = scen.Rule(rule="finish")
    assert opcon.max_successor_qty(rule, 100, 20, 100, 0) == 0.0
    assert opcon.max_successor_qty(rule, 100, 100, 100, 0) == 100.0


def test_wait_minutes_weekly_policy_documented():
    """Pazar 23:59:59 + bekleme -> sonraki hafta indeksi."""
    weeks = [START + __import__("datetime").timedelta(weeks=i) for i in range(4)]
    assert opcon.earliest_week_index_after_wait(0, 20160, weeks) == 2
    assert opcon.earliest_week_index_after_wait(0, 0, weeks) == 0


def test_route_cycle_error():
    from app.models import RoutingOperation as RO

    class _Op:
        def __init__(self, i, s):
            self.id = i
            self.seq = s
            self.operation_name = f"O{i}"

    ops = [_Op(1, 10), _Op(2, 20)]
    deps = {2: [opcon.OpDependency(pred_op_id=1, pred_required_qty=1)], 1: [opcon.OpDependency(pred_op_id=2, pred_required_qty=1)]}
    errs = opcon.detect_cycle([], deps)
    assert errs and "rota_dongusu" in errs[0]
