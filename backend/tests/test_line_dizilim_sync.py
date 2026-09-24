"""Saha tablosu (yıkama makinesi uygunluğu + konveyör dizilimi, tavlama dizilimi) -> istasyon bağları ve dizilim."""
import io
from datetime import date, timedelta

from openpyxl import Workbook

from app.models import Item, Machine, MachineWeek, Order, RoutingOperation, RoutingOperationStation, WorkCenter
from app.schemas import AutoPlanRequest
from app.services import line_dizilim_sync as lds
from app.services.planning import simulate

WK = date(2026, 9, 7)


def _file(wash_rows, anneal_rows):
    wb = Workbook()
    ws = wb.active; ws.title = "YIKAMA ÖNCELİK"
    ws.append(["TAVLAMA & YIKAMA"]); ws.append([]); ws.append([]); ws.append([])
    ws.append(["Stok kodu", "Stok adı", "Ana grup", "Yıkama sayısı", "Yıkama (sn/adet)", "Miktar/yıl (son 12 ay)", "Yıkama yükü (hat-saat/yıl)", "Kümülatif %", "ABC (Yıkama)", "Ciro USD/yıl", "Genel öncelik", "yk2", "Düzeltilmiş yük (hat-saat/yıl)", "Fark (saat)", "yk3", "yk4"])
    for r in wash_rows: ws.append(r)
    ws2 = wb.create_sheet("TAVLAMA ÖNCELİK")
    ws2.append(["x"]); ws2.append([]); ws2.append([]); ws2.append([])
    ws2.append(["Stok kodu", "Stok adı", "Ana grup", "Tavlama sayısı", "Tavlama (sn/adet)", "Miktar/yıl (son 12 ay)", "Tavlama yükü (hat-saat/yıl)", "Kümülatif %", "ABC (Tavlama)", "Ciro USD/yıl", "Genel öncelik", "Tavlama dizilim (adet) → DOLDUR", "Düzeltilmiş yük (hat-saat/yıl)", "Fark (saat)"])
    for r in anneal_rows: ws2.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def test_dizilim_sync_preview_apply_and_placement(db):
    # Paylaşılan test veritabanı: başka testlerin bıraktığı aynı kodlu kayıtlar varsa yeniden kullanılır (kod tekil)
    def _wc(code, name):
        w = db.query(WorkCenter).filter(WorkCenter.code == code).first()
        if w is None:
            w = WorkCenter(code=code, name=name); db.add(w)
        w.planning_mode = "line"; w.is_planned = True; w.is_active = True; w.required_crew_size = 2
        return w
    def _item(code, name):
        it = db.query(Item).filter(Item.code == code).first()
        if it is None:
            it = Item(code=code, name=name); db.add(it)
        return it
    yk = _wc("YIKAMA", "Yıkama"); tv = _wc("TAVLAMA", "Tavlama")
    item = _item("6005657", "1/1-100 GN KUVET"); item2 = _item("6001052", "EVYE")
    db.flush()
    for it in (item, item2):
        db.query(RoutingOperation).filter(RoutingOperation.item_id == it.id).delete(synchronize_session=False)
    ms = {}
    for c in ("YK-02", "YK-03", "YK-04", "YK-05", "TAV-06", "TAV-07"):
        m = db.query(Machine).filter(Machine.code == c).first()
        if m is None:
            m = Machine(code=c); db.add(m)
        m.work_center = yk if c.startswith("YK") else tv; m.is_active = True; m.required_crew_size = 2
        ms[c] = m
    db.flush()
    db.query(MachineWeek).filter(MachineWeek.machine_id.in_([m.id for m in ms.values()]), MachineWeek.week_start == WK).delete(synchronize_session=False)
    for m in ms.values():
        db.add(MachineWeek(machine_id=m.id, week_start=WK, working_hours=42))
    # eski durum: iki yıkama (tekrarlı) YK-05 birincil, dizilim 1; tavlama TAV-06
    ops = [RoutingOperation(item=item, work_center=yk, primary_machine_id=ms["YK-05"].id, seq=10, operation_name="ARA YIKAMA", line_interval_sec=5, cycle_time_sec=60, units_per_cycle=1),
           RoutingOperation(item=item, work_center=tv, primary_machine_id=ms["TAV-06"].id, seq=20, operation_name="TAVLAMA", line_interval_sec=5, cycle_time_sec=60, units_per_cycle=1),
           RoutingOperation(item=item, work_center=yk, primary_machine_id=ms["YK-05"].id, seq=30, operation_name="YIKAMA", line_interval_sec=5, cycle_time_sec=60, units_per_cycle=1),
           RoutingOperation(item=item2, work_center=yk, primary_machine_id=ms["YK-04"].id, seq=10, operation_name="YIKAMA", line_interval_sec=5, cycle_time_sec=60, units_per_cycle=1)]
    db.add_all(ops); db.flush()
    db.add(RoutingOperationStation(operation_id=ops[0].id, machine_id=ms["YK-05"].id, is_primary=True))
    db.add(Order(order_no="DZ-1", item=item, quantity=3600, due_date=WK + timedelta(days=6), material_status="ready"))
    db.commit()
    content = _file(
        [["6005657", "GN", "GN", 2, 10, 45813, 63.6, 0.03, "A", 432631, 1, 2, None, None, 2, 3],   # YK-02:2, YK-03:2 (=> YK-05:2), YK-04:3
         ["6001052", "EVYE", "EVYE", 1, 5, 14990, 20.8, 0.05, "A", 478819, 1, None, None, None, None, 1],  # yalnız YK-04:1
         ["6009999", "YOK", "X", 1, 5, 1, 0, 0, "C", 0, 3, None, None, None, None, 1]],
        [["6005657", "GN", "GN", 1, 5, 45813, 63.6, 0.03, "A", 432631, 1, 4, None, None]],
    )
    p = lds.preview(db, content)
    assert p["missing_sheets"] == [] and p["missing_machines"] == []
    assert p["file"]["washing_items"] == 3 and p["file"]["annealing_items"] == 1 and p["not_found_count"] == 1
    assert p["operations"]["washing"] == 3 and p["operations"]["annealing"] == 1 and p["operations"]["station_links_changed"] == 4 and p["operations"]["units_changed"] == 3 and p["operations"]["primary_changed"] == 2
    # 3600 adet × 2 yıkama: dizilim 1 -> (60 + 3599×5)/3600 ≈ 5,0 sa/yıkama; dizilim 3 -> 1200 grup ≈ 1,68 sa
    assert round(p["open_order_hours"]["before"]["washing"], 1) == 10.0 and round(p["open_order_hours"]["after"]["washing"], 1) == 3.4
    assert round(p["open_order_hours"]["before"]["annealing"], 1) == 5.0 and round(p["open_order_hours"]["after"]["annealing"], 1) == 1.3
    r = lds.apply(db, content, username="t", filename="saha.xlsx")
    assert r["operations"] == 4 and r["washing"] == 3 and r["annealing"] == 1 and r["not_found_count"] == 1
    db.expire_all()
    w1 = db.get(RoutingOperation, ops[0].id)
    st = {s.machine_id: (s.is_primary, s.units_per_cycle) for s in w1.alt_stations}
    assert w1.primary_machine_id == ms["YK-04"].id and w1.units_per_cycle == 3
    assert st == {ms["YK-02"].id: (False, 2), ms["YK-03"].id: (False, 2), ms["YK-05"].id: (False, 2), ms["YK-04"].id: (True, 3)}
    assert db.get(RoutingOperation, ops[2].id).units_per_cycle == 3  # tekrarlı yıkama aynı dizilim
    t1 = db.get(RoutingOperation, ops[1].id)
    assert t1.units_per_cycle == 4 and {s.machine_id for s in t1.alt_stations} == {ms["TAV-06"].id, ms["TAV-07"].id} and t1.primary_machine_id == ms["TAV-06"].id
    e1 = db.get(RoutingOperation, ops[3].id)
    assert [s.machine_id for s in e1.alt_stations] == [ms["YK-04"].id] and e1.units_per_cycle == 1
    # Yerleşim istasyon dizilimini kullanır: YK-04 (dizilim 3) 42 sa/hafta -> 3600 adet tek haftada, ~1,7 sa
    sim = simulate(db, AutoPlanRequest(start_week=WK, weeks=1, work_center_ids=[yk.id, tv.id], use_overtime=False, prep_fill=False))
    wash = [l for l in sim.lines if l.work_center_id == yk.id]
    assert wash and all(l.machine_id == ms["YK-04"].id for l in wash) and round(sum(l.planned_hours for l in wash), 1) == 3.4
