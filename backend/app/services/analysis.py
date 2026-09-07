"""Durus analizi ve cevrim suresi (cycle time) onerisi."""

from collections import defaultdict
from datetime import date
from statistics import median

from sqlalchemy.orm import Session, joinedload

from app.models import Downtime, Item, ProductionActual, RoutingOperation, WorkCenter
from app.services import capacity as cap


# ---------------- Durus analizi ----------------

def downtime_analysis(db: Session, wc_ids: list[int] | None, start: date, end: date) -> dict:
    """Beklenen durus = (nominal - verimli) adam-saat. Fazla durus = gerceklesen - beklenen."""
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
    actual_by_wc_day: dict[tuple[int, date], float] = defaultdict(float)
    reason_by_wc: dict[int, dict[str, dict]] = defaultdict(dict)
    for d in dts:
        actual_by_wc_day[(d.work_center_id, d.dt_date)] += d.minutes
        r = reason_by_wc[d.work_center_id].setdefault(d.reason_code or "-", {"reason_code": d.reason_code or "-", "reason_desc": d.reason_desc, "minutes": 0.0, "count": 0})
        r["minutes"] += d.minutes
        r["count"] += 1

    summary = []
    totals_by_wc: dict[int, dict] = {}
    for w in wcs:
        emp = cap.employee_count(db, w.id)
        for day in cap.working_days(w, start, end):
            expected_min = max(cap.daily_nominal_hours(w, day, emp) - cap.daily_capacity_hours(w, day, emp), 0.0) * 60
            actual_min = actual_by_wc_day.get((w.id, day), 0.0)
            excess = actual_min - expected_min
            summary.append(
                {
                    "work_center_code": w.code,
                    "day": day.isoformat(),
                    "expected_minutes": round(expected_min, 1),
                    "actual_minutes": round(actual_min, 1),
                    "excess_minutes": round(excess, 1),
                }
            )
            t = totals_by_wc.setdefault(w.id, {"work_center_code": w.code, "expected_minutes": 0.0, "actual_minutes": 0.0, "excess_minutes": 0.0})
            t["expected_minutes"] += expected_min
            t["actual_minutes"] += actual_min
            t["excess_minutes"] += excess

    reasons = []
    for wc_id, rmap in reason_by_wc.items():
        total = sum(r["minutes"] for r in rmap.values()) or 1.0
        excess_total = max(totals_by_wc.get(wc_id, {}).get("excess_minutes", 0.0), 0.0)
        for r in sorted(rmap.values(), key=lambda x: -x["minutes"]):
            share = r["minutes"] / total
            reasons.append(
                {
                    "work_center_code": wc_by_id[wc_id].code,
                    "reason_code": r["reason_code"],
                    "reason_desc": r["reason_desc"],
                    "minutes": round(r["minutes"], 1),
                    "count": r["count"],
                    "share_pct": round(share * 100, 1),
                    # fazla durusun sebeplere oransal dagilimi
                    "excess_attributed_minutes": round(excess_total * share, 1),
                }
            )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "totals": [dict(v, **{k: round(v[k], 1) for k in ("expected_minutes", "actual_minutes", "excess_minutes")}) for v in totals_by_wc.values()],
        "daily": summary,
        "reasons": reasons,
    }


# ---------------- Cevrim suresi onerisi ----------------

def cycle_time_suggestions(db: Session, wc_ids: list[int] | None, start: date | None, end: date | None, min_samples: int = 10, threshold_pct: float = 10.0) -> list[dict]:
    """Gerceklesen uretimden etkin cevrim suresi turetir ve tanimli deger ile karsilastirir.

    Etkin CT (sn/adet) kaynaklari:
      1. Satirda 'reported_hours' varsa: reported_hours*3600/quantity
      2. Yoksa: o gun is merkezinin verimli kapasitesinden fazla durus dusulur,
         gunun uretimleri kazanilan saat oraniyla paylastirilir.
    Karar: yeterli ornek (min_samples) ve |medyan - tanimli| / tanimli > threshold_pct.
    """
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

    # gun/is merkezi bazinda toplam durus (dk)
    dt_q = db.query(Downtime.work_center_id, Downtime.dt_date, Downtime.minutes)
    if start:
        dt_q = dt_q.filter(Downtime.dt_date >= start)
    if end:
        dt_q = dt_q.filter(Downtime.dt_date <= end)
    downtime_by_wc_day: dict[tuple[int, date], float] = defaultdict(float)
    for wc_id, d, m in dt_q.all():
        downtime_by_wc_day[(wc_id, d)] += m

    by_wc_day: dict[tuple[int, date], list[ProductionActual]] = defaultdict(list)
    for r in rows:
        by_wc_day[(r.work_center_id, r.prod_date)].append(r)

    samples: dict[tuple[int, int, int | None], list[float]] = defaultdict(list)  # (item, wc, seq) -> etkin CT
    emp_cache: dict[int, int] = {}
    for (wc_id, day), lst in by_wc_day.items():
        wc = lst[0].work_center
        emp = emp_cache.setdefault(wc_id, cap.employee_count(db, wc_id))
        eff_hours = cap.daily_capacity_hours(wc, day, emp)
        nominal = cap.daily_nominal_hours(wc, day, emp)
        expected_dt_min = max(nominal - eff_hours, 0.0) * 60
        excess_dt_h = max(downtime_by_wc_day.get((wc_id, day), 0.0) - expected_dt_min, 0.0) / 60
        available_h = max(eff_hours - excess_dt_h, 0.0)
        earned_total = sum(r.earned_hours for r in lst) or 0.0
        for r in lst:
            if r.quantity <= 0:
                continue
            if r.reported_hours:
                ct = r.reported_hours * 3600 / r.quantity
            elif earned_total > 0 and available_h > 0:
                share = r.earned_hours / earned_total
                ct = available_h * share * 3600 / r.quantity
            else:
                continue
            samples[(r.item_id, wc_id, r.operation_seq)].append(ct)

    # tanimli CT'ler
    ops = db.query(RoutingOperation).options(joinedload(RoutingOperation.item), joinedload(RoutingOperation.work_center)).all()
    op_map: dict[tuple[int, int, int], RoutingOperation] = {(o.item_id, o.work_center_id, o.seq): o for o in ops}
    op_by_item_wc: dict[tuple[int, int], RoutingOperation] = {}
    for o in ops:
        op_by_item_wc.setdefault((o.item_id, o.work_center_id), o)

    out = []
    for (item_id, wc_id, seq), cts in samples.items():
        op = op_map.get((item_id, wc_id, seq)) if seq is not None else op_by_item_wc.get((item_id, wc_id))
        if not op or op.cycle_time_sec <= 0:
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
