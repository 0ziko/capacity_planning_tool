"""Read-only delivery impact scenario. Never writes production/order/reservation links."""
from collections import defaultdict
from datetime import date, timedelta
import math
from types import SimpleNamespace
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.models import Item, Order, PlanLine, Reservation, Shipment, StockReceipt, RoutingOperation, WorkCenter
from app.models.mes import MesDetail
from app.models import ProductionBatchOrder
from app.services.capacity import LaborCapacityCalendar, apply_planning_reserve
from app.services.bom_tree import is_wip_asm_link
from app.services.mes import balances, detail_dict, standard_unit_hours, monday
from app.services.mes_allocation import allocate_pool
from app.services.scenarios import RuleLookup


def _place(hours, cursor, calendar, backwards=False):
    """Fractional days share each day's labor pool. No wall-clock precision implied."""
    if hours <= 1e-8:
        return cursor, cursor, 0.
    remaining, first, last = hours, None, None
    for day in sorted(calendar, reverse=backwards):
        entry = calendar[day]
        rate, slots = entry if isinstance(entry, tuple) else (entry, [(float(day), float(day + 1))])
        if rate <= 1e-9:
            continue
        for left, right in sorted(list(slots), reverse=backwards):
            low, high = (left, min(right, cursor)) if backwards else (max(left, cursor), right)
            if high <= low:
                continue
            take = min(remaining, (high - low) * rate)
            begin, finish = (high - take / rate, high) if backwards else (low, low + take / rate)
            slots.remove((left, right))
            if begin > left + 1e-9:
                slots.append((left, begin))
            if finish < right - 1e-9:
                slots.append((finish, right))
            calendar[day] = (rate, slots)
            first = begin if first is None else min(first, begin)
            last = finish if last is None else max(last, finish)
            remaining -= take
            if remaining <= 1e-8:
                return first, last, 0.
    return first, last, max(remaining, 0.)


def _finish_day(t):
    return date.fromordinal(max(1, math.ceil(t - 1e-9) - 1)) if t is not None else None


def analyze(db, as_of, horizon=8, work_center_ids=None):
    start = as_of + timedelta(days=1)
    horizon_end = monday(as_of) + timedelta(weeks=horizon, days=6)
    schedule_end = max(horizon_end, start) + timedelta(weeks=26)
    past = start - timedelta(weeks=26)
    orders = db.query(Order).filter(Order.status == "open").order_by(Order.due_date, Order.id).all()
    # Stock competition still includes every open order; unused catalogue BOMs are irrelevant.
    items = {i.id: i for i in db.query(Item).options(selectinload(Item.operations), selectinload(Item.bom_lines))
             .filter(Item.id.in_({o.item_id for o in orders})).all()}
    wip_codes = {b.component_code.upper() for item in items.values() for b in item.bom_lines
                 if is_wip_asm_link(b.component_code, b.source_wip, b.recipe_seq)}
    if wip_codes:
        items.update({i.id: i for i in db.query(Item).options(selectinload(Item.operations), selectinload(Item.bom_lines))
                      .filter(func.upper(Item.code).in_(wip_codes)).all()})
    by_code = {i.code.upper(): i for i in items.values()}
    orders.sort(key=lambda o: (o.revised_due_date or o.due_date, o.order_no, o.position_no, o.id))
    # All orders compete for stock; only demand due within the requested horizon is scheduled.
    shipped, shipped_item, reserved, reserved_item, receipts = (defaultdict(float) for _ in range(5))
    for s in db.query(Shipment).filter(Shipment.ship_date <= as_of).all():
        shipped[s.order_id] += s.quantity
        shipped_item[s.item_id] += s.quantity
    for r in db.query(Reservation).all():
        reserved[r.order_id] += r.quantity
        reserved_item[r.item_id] += r.quantity
    for r in db.query(StockReceipt).filter(StockReceipt.receipt_date <= as_of).all():
        receipts[r.item_id] += r.quantity
    on_hand = {iid: max(receipts[iid] - shipped_item[iid], 0.) for iid in items}
    free = {iid: max(on_hand[iid] - reserved_item[iid], 0.) for iid in items}
    reservation_room = dict(on_hand)
    coverage = {}
    for o in orders:
        demand = max(o.quantity - shipped[o.id], 0)
        owned = min(demand, reserved[o.id], reservation_room.get(o.item_id, 0))
        reservation_room[o.item_id] = max(reservation_room.get(o.item_id, 0) - owned, 0)
        take = min(max(demand - owned, 0), free.get(o.item_id, 0))
        free[o.item_id] = max(free.get(o.item_id, 0) - take, 0)
        coverage[o.id] = (demand, owned, take, max(demand - owned - take, 0))
    order_items = {o.id: o.item_id for o in orders}
    orders = [o for o in orders if (o.revised_due_date or o.due_date) <= horizon_end and coverage[o.id][0] > 0]
    rules = RuleLookup(db)
    plans = db.query(PlanLine).filter(PlanLine.mode.in_(["auto", "manual"])).all()
    # Split existing batch completion slots in memory, retaining their total quantity.
    # No batch/order link or plan line is created or changed.
    links = defaultdict(list)
    rank = {o.id: i for i, o in enumerate(orders)}
    for link in db.query(ProductionBatchOrder).all():
        if link.order_id in rank:
            links[link.batch_id].append(link)
    slot_room = {link.order_id: min(link.quantity, coverage[link.order_id][3])
                 for group in links.values() for link in group}
    expanded = []
    for p in sorted(plans, key=lambda p: (p.week_start, p.id)):
        item = items.get(order_items.get(p.order_id))
        final = max(item.operations, key=lambda op: op.seq).id if item and item.operations else None
        if not p.production_batch_id or p.operation_id != final:
            expanded.append(p)
            continue
        left = max(p.planned_qty, 0)
        for link in sorted(links[p.production_batch_id], key=lambda l: rank[l.order_id]):
            take = min(left, slot_room[link.order_id])
            if take > 0:
                expanded.append(SimpleNamespace(id=p.id, order_id=link.order_id, operation_id=p.operation_id,
                                                week_start=p.week_start, planned_qty=take))
                slot_room[link.order_id] -= take
                left -= take
    plans = expanded
    finish_plans = defaultdict(list)
    for p in plans:
        item = items.get(order_items.get(p.order_id))
        if item and item.operations and p.operation_id == max(item.operations, key=lambda op: op.seq).id:
            finish_plans[p.order_id].append(p.week_start)
    graphs, required, credits = {}, {}, {}
    for o in orders:
        item = items.get(o.item_id)
        qty = coverage[o.id][3]
        nodes, warnings = [], []
        if qty <= 1e-9:
            graphs[o.id] = (nodes, warnings)
            required[o.id] = {}
            continue
        if not item or not item.operations:
            warnings.append("Bitmiş ürün rotası eksik")
        if item and not item.bom_lines:
            warnings.append("BOM tanımı eksik")
        branch_ends = []

        def branch(route_item, coefficient, name):
            previous = None
            for op in sorted(route_item.operations, key=lambda x: x.seq):
                n = {"op": op, "coefficient": coefficient, "deps": [] if previous is None else [previous],
                     "branch": name, "qty": qty * coefficient, "wait": 0}
                if previous is not None:
                    rule = rules.get(item, nodes[previous]["op"], op)
                    n["wait"] = math.ceil(rule.wait_minutes / 1440)
                    if rule.rule == "cycles":
                        warnings.append("Çevrimle örtüşen geçiş var; tarih hesabı ihtiyatlı olarak aşama bitişini bekliyor")
                nodes.append(n)
                previous = len(nodes) - 1
            return previous

        if item:
            components = defaultdict(float)
            for b in item.bom_lines:
                if is_wip_asm_link(b.component_code, b.source_wip, b.recipe_seq):
                    components[b.component_code.upper()] += b.quantity
            for code, coefficient in components.items():
                child = by_code.get(code)
                if not child or not child.operations or coefficient <= 0:
                    warnings.append(f"{code}: yarımamül rotası veya BOM katsayısı eksik")
                else:
                    last = branch(child, coefficient, child.code)
                    if last is not None:
                        branch_ends.append(last)
            own_start = len(nodes)
            last = branch(item, 1., item.code)
            if last is not None:
                # Standalone WIP branches feed the FG finishing route, including
                # any operations before packaging/the final operation.
                nodes[own_start]["deps"].extend(branch_ends)
                for end in branch_ends:
                    rule = rules.get(item, nodes[end]["op"], nodes[own_start]["op"])
                    nodes[own_start]["wait"] = max(nodes[own_start]["wait"], math.ceil(rule.wait_minutes / 1440))
                    if rule.rule == "cycles":
                        warnings.append("Montaja geçişte çevrim örtüşmesi var; tam parti bitişi esas alındı")
        required[o.id] = {n["op"].id: n["qty"] for n in nodes}
        graphs[o.id] = (nodes, warnings)
    # Reuse common-pool rules, only in local dictionaries. No ORM relationship is assigned.
    records = [detail_dict(r) for r in db.query(MesDetail).filter(MesDetail.prod_date <= as_of).all()]
    from app.services.mes_inventory import planning_pool
    pool = planning_pool(records)
    allocations = allocate_pool(db, pool, as_of, orders, required, credits, items.values(), plan_lines=plans) if records else []
    from app.services.remaining_work import produced_qty_map
    from app.core.config import get_settings
    legacy, legacy_warnings = ({}, []) if get_settings().production_source == "mes" else produced_qty_map(db, as_of=as_of, include_mes=False)
    work_centers = {w.id: w for w in db.query(WorkCenter).all()}
    used_wc = {n["op"].work_center_id for nodes, _ in graphs.values() for n in nodes}
    full, forward = {}, {}
    for wid in used_wc:
        wc = work_centers.get(wid)
        if not wc:
            full[wid], forward[wid] = {}, {}
            continue
        cap = LaborCapacityCalendar(db, wc, past, schedule_end).capacity(past, schedule_end)
        full[wid] = {d.day.toordinal(): apply_planning_reserve(wc, d.hours) for d in cap.days}
        forward[wid] = {d: h for d, h in full[wid].items() if d >= start.toordinal()}
    initial = {wid: dict(cal) for wid, cal in forward.items()}
    rows, wk_load = [], defaultdict(float)
    uncertain_wc = set()
    selected = set(work_center_ids or [])
    for o in orders:
        item = items.get(o.item_id)
        nodes, warnings = graphs[o.id]
        due = o.revised_due_date or o.due_date
        demand, owned, stock, qty = coverage[o.id]
        last_op = max(item.operations, key=lambda op: op.seq).id if item and item.operations else None
        fg_legacy = legacy.get((o.id, last_op), 0) if last_op else 0
        for n in nodes:
            op = n["op"]
            old_wip = max(legacy.get((o.id, op.id), 0) - fg_legacy * n["coefficient"], 0)
            n["qty"] = max(n["qty"] - old_wip - credits.get((o.id, op.id), 0), 0)
            unit = standard_unit_hours(op)
            n["invalid"] = n["qty"] > 0 and (not math.isfinite(unit) or unit <= 0)
            setup = op.setup_labor_minutes if op.setup_labor_minutes is not None else op.setup_time_min
            n["hours"] = n["qty"] * (unit if math.isfinite(unit) else 0) + (float(setup or 0) / 60 if n["qty"] > 0 else 0)
            if n["invalid"]:
                warnings.append(f"{op.semi_finished_code or item.code}: standart süre eksik")
                uncertain_wc.add(op.work_center_id)
            if not any(full.get(op.work_center_id, {}).values()) and n["qty"] > 0:
                warnings.append(f"{work_centers[op.work_center_id].code}: kapasite tanımı yok veya sıfır")
            if op.work_center_id in uncertain_wc:
                warnings.append("Ortak kapasitedeki bir işin süresi eksik; bitiş tahmini güvenilir değil")
        # Backward: each order's own chain on full capacity gives necessary latest dates.
        backward = {wid: dict(full[wid]) for wid in {n["op"].work_center_id for n in nodes}}
        for i in range(len(nodes) - 1, -1, -1):
            n = nodes[i]
            successors = [s for s in nodes[i+1:] if i in s["deps"]]
            deadline = min((s.get("latest_start", due.toordinal() + 1) - s["wait"] for s in successors), default=due.toordinal() + 1)
            begin, finish, missing = _place(n["hours"], deadline, backward[n["op"].work_center_id], True)
            n["latest_start"] = begin if begin is not None else min(past.toordinal(), deadline)
            n["latest_finish"] = _finish_day(deadline) if missing <= 1e-6 else None
            n["back_missing"] = missing
        # Forward: all competing orders share one capacity pool; filters apply afterwards.
        for n in nodes:
            predecessors = [nodes[i] for i in n["deps"]]
            blocked = n["invalid"] or any(p.get("finish") is None for p in predecessors)
            if blocked and n["qty"] > 0:
                uncertain_wc.add(n["op"].work_center_id)
            release = max(start, o.material_ready_date or start).toordinal()
            earliest = max([release, *[p["finish"] for p in predecessors if p.get("finish") is not None]]) + (n["wait"] if predecessors else 0)
            begin, finish, missing = (None, None, n["hours"]) if blocked else _place(n["hours"], earliest, forward[n["op"].work_center_id])
            n["finish"] = None if blocked or missing > 1e-6 else finish
            n["forecast"] = _finish_day(n["finish"])
            n["missing"] = missing
            if due <= monday(as_of) + timedelta(days=6):
                wk_load[n["op"].work_center_id] += n["hours"]
        forecast = max((n["forecast"] for n in nodes if n["forecast"]), default=None)
        if any(n["forecast"] is None for n in nodes):
            forecast = None
        status = "covered" if qty <= 1e-9 else "unknown" if warnings or not nodes else "late" if forecast is None or forecast > due else "tight" if (due - forecast).days <= 1 else "on_time"
        steps = []
        for n in nodes:
            op = n["op"]
            delay = max((n["forecast"] - n["latest_finish"]).days, 0) if n["forecast"] and n["latest_finish"] else None
            steps.append({"operation_id": op.id, "operation": op.operation_name, "material_code": op.semi_finished_code or item.code,
                          "work_center_id": op.work_center_id, "work_center": work_centers[op.work_center_id].code,
                          "remaining_qty": round(n["qty"], 4), "remaining_hours": round(n["hours"], 3),
                          "latest_finish": n["latest_finish"], "forecast_finish": n["forecast"], "delay_days": delay,
                          "risk": n["qty"] > 0 and (delay is None or delay > 0 or n["back_missing"] > 0),
                          "horizon_missing_hours": round(n["missing"], 3)})
        if selected and not any(s["work_center_id"] in selected for s in steps):
            continue
        rows.append({"order_id": o.id, "order_no": o.order_no, "position_no": o.position_no, "customer": o.customer,
                     "item_code": item.code if item else "", "item_name": item.name if item else "", "due_date": due,
                     "planned_finish_week": max(finish_plans[o.id]) if finish_plans[o.id] else None,
                     "forecast_finish": as_of if status == "covered" else forecast, "status": status,
                     "delay_days": max((forecast - due).days, 0) if forecast and status != "covered" else None,
                     "open_qty": demand, "reserved_qty": owned, "scenario_stock_qty": stock, "production_qty": qty,
                     "at_risk_qty": qty if status in ("late", "unknown") else 0,
                     "hours": round(sum(n["hours"] for n in nodes), 3), "steps": steps, "warnings": list(dict.fromkeys(warnings))})
    wc_rows = []
    for wid in used_wc:
        if selected and wid not in selected:
            continue
        remaining = sum(h for d, h in initial[wid].items() if d <= (monday(as_of) + timedelta(days=6)).toordinal())
        wc_rows.append({"code": work_centers[wid].code, "remaining_capacity": round(remaining, 2),
                        "due_this_week_hours": round(wk_load[wid], 2), "gap_hours": round(max(wk_load[wid] - remaining, 0), 2)})
    return {"as_of": as_of, "capacity_start": start, "horizon_end": horizon_end, "orders": rows,
            "work_centers": sorted(wc_rows, key=lambda w: -w["gap_hours"]), "allocations": allocations,
            "summary": {s: sum(r["status"] == s for r in rows) for s in ("covered", "on_time", "tight", "late", "unknown")},
            "assumptions": ["Salt okunur etki senaryosu; üretim-sipariş bağı veya rezervasyon oluşturmaz.",
                "Rapor günü tamamlanmış kabul edilir; kalan kapasite ertesi günden başlar. Stok ve rezervasyonlar mevcut durumdur.",
                "Termin sırasıyla ortak kapasite paylaşılır; iş merkezi/müşteri filtreleri diğer işlerin yükünü hesap dışı bırakmaz.",
                "Geriye hesaplanan tarihler tek siparişin tam kapasitedeki üst sınırıdır; ileri hesap diğer siparişlerin yükünü de içerir.",
                "Gün bazında ihtiyatlı tahmin: tam parti bitişi esas alınır, kısmi sevkiyat sözü verilmez. Beklemeler tam güne yukarı yuvarlanır.",
                "Örtüşen çevrim kuralları ve eksik tanımlar belirsiz olarak işaretlenir; sonuç kesin teslim taahhüdü değildir.",
                "Tanımlı malzeme hazır tarihi başlangıcı sınırlar. Tarih yoksa malzeme hazır varsayılır; satın alma ve hammadde yeterliliği hesaplanmaz.",
                "Kalan her operasyon için bir hazırlık süresi ayrılır. Üretim partileri birleştirilerek hazırlık optimizasyonu yapılmaz.",
                "Bitiş planı olmayan yarımamül havuzda kalır. Geriye hesap tam parti ve rota sırasını esas alır; paralel çalışma optimizasyonu yapmaz.",
                "Plan yükü kalan sipariş yüküne ikinci kez eklenmez. Ufuktan sonraki 26 haftaya sığmayan işler için bitiş tarihi verilmez.",
                *legacy_warnings[:10]]}
