"""Durus analizi ve cevrim suresi (cycle time) onerisi."""

from collections import defaultdict
from datetime import date
from statistics import median

from sqlalchemy.orm import Session, joinedload

from app.models import Downtime, Item, ProductionActual, RoutingOperation, WorkCenter
from app.services import capacity as cap
from app.services.kpi_units import downtime_labor_minutes, downtime_machine_minutes, effective_good_qty


# ---------------- Durus analizi ----------------

def downtime_analysis(db: Session, wc_ids: list[int] | None, start: date, end: date) -> dict:
    """Beklenen durus = (nominal - verimli) adam-dakika. Olculmus adam-dakika yalnizca time_basis != legacy."""
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if wc_ids:
        q = q.filter(WorkCenter.id.in_(wc_ids))
    wcs = q.order_by(WorkCenter.code).all()
    wc_by_id = {w.id: w for w in wcs}

    dts = (
        db.query(Downtime)
        .filter(Downtime.work_center_id.in_(list(wc_by_id)), Downtime.dt_date >= start, Downtime.dt_date <= end)
        .all()
    )
    measured_labor_by_wc_day: dict[tuple[int, date], float] = defaultdict(float)
    machine_by_wc_day: dict[tuple[int, date], float] = defaultdict(float)
    reason_by_wc: dict[int, dict[str, dict]] = defaultdict(dict)
    for d in dts:
        lab, _ = downtime_labor_minutes(d)
        if lab is not None:
            measured_labor_by_wc_day[(d.work_center_id, d.dt_date)] += lab
        mach = downtime_machine_minutes(d)
        if mach is not None:
            machine_by_wc_day[(d.work_center_id, d.dt_date)] += mach
        r = reason_by_wc[d.work_center_id].setdefault(
            d.reason_code or "-",
            {"reason_code": d.reason_code or "-", "reason_desc": d.reason_desc, "minutes": 0.0, "measured_labor_minutes": 0.0, "count": 0},
        )
        r["minutes"] += d.minutes
        if lab is not None:
            r["measured_labor_minutes"] += lab
        r["count"] += 1

    summary = []
    totals_by_wc: dict[int, dict] = {}
    for w in wcs:
        emp = cap.employee_count(db, w)
        ovl = cap.Overrides(db, w.id)
        ovl.preload(start, end)
        for day in cap.working_days(w, start, end, ovl):
            ov = ovl.get(day)
            expected_min = max(cap.daily_nominal_hours(w, day, emp, ov) - cap.daily_capacity_hours(w, day, emp, ov), 0.0) * 60
            actual_min = measured_labor_by_wc_day.get((w.id, day), 0.0)
            mach_min = machine_by_wc_day.get((w.id, day), 0.0)
            excess = actual_min - expected_min
            summary.append(
                {
                    "work_center_code": w.code,
                    "day": day.isoformat(),
                    "expected_minutes": round(expected_min, 1),
                    "actual_minutes": round(actual_min, 1),
                    "machine_minutes": round(mach_min, 1),
                    "excess_minutes": round(excess, 1),
                }
            )
            t = totals_by_wc.setdefault(
                w.id,
                {
                    "work_center_code": w.code,
                    "expected_minutes": 0.0,
                    "actual_minutes": 0.0,
                    "machine_minutes": 0.0,
                    "excess_minutes": 0.0,
                },
            )
            t["expected_minutes"] += expected_min
            t["actual_minutes"] += actual_min
            t["machine_minutes"] += mach_min
            t["excess_minutes"] += excess

    reasons = []
    for wc_id, rmap in reason_by_wc.items():
        total_meas = sum(r.get("measured_labor_minutes", 0.0) for r in rmap.values()) or 1.0
        excess_total = max(totals_by_wc.get(wc_id, {}).get("excess_minutes", 0.0), 0.0)
        for r in sorted(rmap.values(), key=lambda x: -x["minutes"]):
            share = r.get("measured_labor_minutes", 0.0) / total_meas if total_meas else 0.0
            reasons.append(
                {
                    "work_center_code": wc_by_id[wc_id].code,
                    "reason_code": r["reason_code"],
                    "reason_desc": r["reason_desc"],
                    "minutes": round(r["minutes"], 1),
                    "measured_labor_minutes": round(r.get("measured_labor_minutes", 0.0), 1),
                    "count": r["count"],
                    "share_pct": round(share * 100, 1),
                    "proportional_allocation_minutes": round(excess_total * share, 1),
                    "excess_attributed_minutes": round(excess_total * share, 1),
                }
            )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "totals": [
            dict(
                v,
                **{k: round(v[k], 1) for k in ("expected_minutes", "actual_minutes", "machine_minutes", "excess_minutes")},
            )
            for v in totals_by_wc.values()
        ],
        "daily": summary,
        "reasons": reasons,
    }


def _ct_comparable(reported_time_basis: str) -> bool:
    """Tanimli CT sn/adet; olcum is gucu veya gecen sure bazli olmali."""
    b = reported_time_basis or "legacy_unspecified"
    return b in ("labor_hours", "elapsed_time", "legacy_unspecified")


# ---------------- Cevrim suresi onerisi ----------------

def cycle_time_suggestions(db: Session, wc_ids: list[int] | None, start: date | None, end: date | None, min_samples: int = 10, threshold_pct: float = 10.0) -> list[dict]:
    """Yalniz fiili reported_hours ile olculmus etkin CT; earned saat payi ile otomatik oneri yok."""
    q = db.query(ProductionActual).options(joinedload(ProductionActual.item), joinedload(ProductionActual.work_center))
    if wc_ids:
        q = q.filter(ProductionActual.work_center_id.in_(wc_ids))
    if start:
        q = q.filter(ProductionActual.prod_date >= start)
    if end:
        q = q.filter(ProductionActual.prod_date <= end)
    rows = q.all()
    if not rows:
        return []

    samples: dict[tuple[int, int, int | None], list[float]] = defaultdict(list)
    for r in rows:
        qty = effective_good_qty(r)
        if qty <= 0:
            continue
        if not r.reported_hours or r.reported_hours <= 0:
            continue
        basis = getattr(r, "reported_time_basis", None) or "legacy_unspecified"
        if not _ct_comparable(basis):
            continue
        ct = r.reported_hours * 3600 / qty
        samples[(r.item_id, r.work_center_id, r.operation_seq)].append(ct)

    ops = db.query(RoutingOperation).options(joinedload(RoutingOperation.item), joinedload(RoutingOperation.work_center)).all()
    op_map: dict[tuple[int, int, int], RoutingOperation] = {(o.item_id, o.work_center_id, o.seq): o for o in ops}
    op_by_item_wc: dict[tuple[int, int], RoutingOperation] = {}
    for o in ops:
        op_by_item_wc.setdefault((o.item_id, o.work_center_id), o)

    out = []
    for (item_id, wc_id, seq), cts in samples.items():
        op = op_map.get((item_id, wc_id, seq)) if seq is not None else op_by_item_wc.get((item_id, wc_id))
        if not op or op.cycle_time_sec <= 0 or op.work_center is None:
            continue
        med = median(cts)
        dev_pct = (med - op.cycle_time_sec) / op.cycle_time_sec * 100
        out.append(
            {
                "item_code": op.item.code,
                "product_group": op.item.product_group,
                "work_center_code": op.work_center.code,
                "operation_seq": op.seq,
                "defined_ct_sec": round(op.cycle_time_sec, 1),
                "observed_median_ct_sec": round(med, 1),
                "observed_min_ct_sec": round(min(cts), 1),
                "observed_max_ct_sec": round(max(cts), 1),
                "samples": len(cts),
                "deviation_pct": round(dev_pct, 1),
                "measurement_source": "measured",
                "suggested_ct_sec": round(med, 1) if len(cts) >= min_samples and abs(dev_pct) > threshold_pct else None,
                "status": "yeterli veri yok" if len(cts) < min_samples else ("oneri var" if abs(dev_pct) > threshold_pct else "uyumlu"),
            }
        )
    out.sort(key=lambda x: (x["work_center_code"], x["item_code"], x["operation_seq"]))
    return out


def group_suggestions(rows: list[dict]) -> list[dict]:
    """Urun grubu + is merkezi bazinda ozet (ortalama sapma)."""
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        k = (r["product_group"] or "-", r["work_center_code"])
        a = agg.setdefault(k, {"product_group": k[0], "work_center_code": k[1], "items": 0, "samples": 0, "dev_sum": 0.0})
        a["items"] += 1
        a["samples"] += r["samples"]
        a["dev_sum"] += r["deviation_pct"]
    return [
        {"product_group": a["product_group"], "work_center_code": a["work_center_code"], "items": a["items"], "samples": a["samples"], "avg_deviation_pct": round(a["dev_sum"] / a["items"], 1)}
        for a in sorted(agg.values(), key=lambda x: (x["work_center_code"], x["product_group"]))
    ]
