"""Weekly MES progress against the plan preserved at first MES import."""
from collections import defaultdict
from datetime import timedelta
from app.models import WorkCenter
from app.models.mes import MesDetail, MesPlanBaseline
from app.services.capacity import LaborCapacityCalendar
from app.services.mes import monday, live_plan_range, detail_dict, balances


def ratio(n, d):
    return round(n / d * 100, 2) if d > 1e-9 else None


def progress(db, day, wc_ids=None, horizon=4):
    week = monday(day)
    end = week + timedelta(days=6)
    baselines = {b.week_start: b for b in db.query(MesPlanBaseline).filter(
        MesPlanBaseline.week_start >= week, MesPlanBaseline.week_start <= week + timedelta(weeks=horizon)).all()}
    selected = set(wc_ids or [])
    targets = {}
    live_weeks = live_plan_range(db, {week + timedelta(weeks=i) for i in range(horizon + 1)} - baselines.keys())
    for offset in range(horizon + 1):
        wk = week + timedelta(weeks=offset)
        lines = baselines[wk].lines if wk in baselines else live_weeks[wk]
        for p in lines:
            if selected and p["work_center_id"] not in selected:
                continue
            k = (wk, p["key"])
            if k not in targets:
                targets[k] = {**p, "week": wk.isoformat(), "quantity": 0., "hours": 0.,
                              "actual_qty": 0., "earned_hours": 0., "products": set(), "daily": [0.] * 7}
            t = targets[k]
            t["quantity"] += p["quantity"]
            t["hours"] += p["hours"]
            t["products"].add(p["item_code"])
            t["unit_hours_total"] = t.get("unit_hours_total", 0) + p["quantity"] * p["unit_hours"]
    records = [detail_dict(d) for d in db.query(MesDetail).filter(MesDetail.prod_date <= day).order_by(
        MesDetail.prod_date, MesDetail.detail_id).all()]
    # Previous weeks' early output reserves its future target exactly once.
    historical = {b.week_start: b.lines for b in db.query(MesPlanBaseline).filter(MesPlanBaseline.week_start < week).all()}
    history_room = defaultdict(float)
    for wk, lines in historical.items():
        for p in lines:
            history_room[(wk, p["key"])] += p["quantity"]
    early = defaultdict(float)
    for r in records:
        m = r["mapping"]
        if r["prod_date"] >= week or m["status"] != "mapped":
            continue
        left = r["quantity"]
        start = monday(r["prod_date"])
        for offset in range(horizon + 1):
            wk = start + timedelta(weeks=offset)
            key = (wk, m["key"])
            if wk < week:
                take = min(left, history_room[key])
                history_room[key] -= take
            else:
                target = targets.get(key)
                take = min(left, max(target["quantity"] - early[key], 0)) if target else 0
                early[key] += take
            left -= take
            if left <= 1e-9:
                break
    daily = [{"day": (week + timedelta(days=i)).isoformat(), "hours": 0., "matched_hours": 0.,
              "off_plan_hours": 0., "cumulative_hours": 0., "reported": week + timedelta(days=i) <= day} for i in range(7)]
    wc_actual = defaultdict(float)
    off = []
    unresolved = []
    current = [r for r in records if week <= r["prod_date"] <= day]
    for r in current:
        m = r["mapping"]
        if selected and m.get("work_center_id") not in selected:
            continue
        if m["status"] == "free_stock":
            continue
        if m["status"] != "mapped":
            unresolved.append(r)
            continue
        index = (r["prod_date"] - week).days
        hours = r["quantity"] * m["standard_unit_hours"]
        daily[index]["hours"] += hours
        wc_actual[m["work_center_id"]] += hours
        left = r["quantity"]
        for offset in range(horizon + 1):
            wk = week + timedelta(weeks=offset)
            key = (wk, m["key"])
            target = targets.get(key)
            if not target:
                continue
            take = min(left, max(target["quantity"] - early[key] - target["actual_qty"], 0))
            target["actual_qty"] += take
            target["earned_hours"] += take * m["standard_unit_hours"]
            if offset == 0:
                target["daily"][index] += take
                daily[index]["matched_hours"] += take * m["standard_unit_hours"]
            elif take > 0:
                off.append({**r, "quantity": take, "match_week": wk.isoformat(), "match_products": sorted(target["products"]),
                            "standard_hours": take * m["standard_unit_hours"], "classification": "early"})
            left -= take
            if left <= 1e-9:
                break
        if left > 1e-9:
            off.append({**r, "quantity": left, "match_week": None, "match_products": [],
                        "standard_hours": left * m["standard_unit_hours"], "classification": "unplanned"})
    rows = []
    for (wk, key), t in targets.items():
        if wk != week:
            continue
        t["products"] = sorted(t["products"])
        t["unit_hours"] = t["unit_hours_total"] / t["quantity"] if t["quantity"] > 0 else 0
        t["early_qty"] = early[(wk, key)]
        t["remaining_qty"] = max(t["quantity"] - t["actual_qty"] - t["early_qty"], 0)
        t["remaining_hours"] = t["remaining_qty"] * t["unit_hours"]
        t["completion_pct"] = ratio(t["actual_qty"] + t["early_qty"], t["quantity"])
        rows.append(t)
    rows.sort(key=lambda r: (-r["remaining_hours"], r["material_code"]))
    wc_rows = []
    for wc in db.query(WorkCenter).order_by(WorkCenter.code).all():
        if selected and wc.id not in selected:
            continue
        plans = [r for r in rows if r["work_center_id"] == wc.id]
        if not plans and not wc_actual[wc.id]:
            continue
        cal = LaborCapacityCalendar(db, wc, week, end)
        cap_week = cal.capacity(week, end).capacity_hours
        cap_elapsed = cal.capacity(week, day).capacity_hours
        planned = sum(r["hours"] for r in plans)
        actual = wc_actual[wc.id]
        wc_rows.append({"id": wc.id, "code": wc.code, "planned_hours": planned,
                        "actual_hours": actual, "matched_hours": sum(r["earned_hours"] for r in plans),
                        "remaining_hours": sum(r["remaining_hours"] for r in plans),
                        "capacity_hours": cap_week, "elapsed_capacity_hours": cap_elapsed,
                        "output_vs_plan_pct": ratio(actual, planned), "capacity_usage_pct": ratio(actual, cap_elapsed)})
    cum = 0.
    for d in daily:
        cum += d["hours"]
        d["cumulative_hours"] = cum
        d["off_plan_hours"] = d["hours"] - d["matched_hours"]
    from app.services.mes_inventory import inventory
    stock = inventory(records, day)
    pool = {r["material_code"]: r["balance"] for r in stock["rows"]}
    total_plan = sum(r["hours"] for r in rows)
    total_actual = sum(d["hours"] for d in daily)
    allocations = []
    if records:
        from app.services.remaining_work import produced_qty_map
        produced_qty_map(db, as_of=day, mes_allocations=allocations, include_mes=True)
        if selected:
            allocations = [a for a in allocations if a["work_center_id"] in selected]
    from app.services.mes import free_stock_rows
    return {"pending_consumption_count": len(stock["pending"]), "free_stock": free_stock_rows(db, day), "week": week, "week_end": end, "as_of": day, "horizon": horizon,
            "baseline": "frozen" if week in baselines else "live",
            "baseline_created_at": baselines[week].created_at if week in baselines else None,
            "summary": {"planned_hours": total_plan, "actual_hours": total_actual,
                        "matched_hours": sum(r["earned_hours"] for r in rows),
                        "remaining_hours": sum(r["remaining_hours"] for r in rows),
                        "off_plan_hours": sum(r["standard_hours"] for r in off),
                        "early_hours": sum(r["standard_hours"] for r in off if r["classification"] == "early"),
                        "output_vs_plan_pct": ratio(total_actual, total_plan),
                        "capacity_usage_pct": ratio(total_actual, sum(r["elapsed_capacity_hours"] for r in wc_rows)),
                        "record_count": len(current), "unresolved_count": len(unresolved)},
            "daily": daily, "operations": rows, "work_centers": wc_rows, "off_plan": off,
            "allocations": allocations,
            "unresolved": unresolved, "pool": [{"material_code": c, "quantity": round(q, 4)} for c, q in sorted(pool.items())],
            "notes": ["Saatler net miktar × standart rota işçiliğidir; MES süreleri kullanılmaz.",
                      "Günlük plan üretilmez; haftalık hedef ile tarih dahil günlük üretim karşılaştırılır.",
                      "Ortak yarımamül tek havuzdur; aday bitmiş ürünlerin her birine ayrı gerçekleşme yazılmaz.",
                      "Havuz önce bu haftanın bitiş planına, sonra en yakın bitiş haftasından uzağa dağıtılır. Bitiş planı olmayan miktar havuzda kalır.",
                      "Havuz bakiyesi MES başlangıcından itibaren hareketlerdir; negatif bakiye eksik açılış/üretim verisini gösterir."]}
