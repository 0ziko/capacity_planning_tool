"""Fazla mesai havuzu (Aşama 2): planlayıcının termini kurtarmak için kullanabileceği ek kapasite.

Havuz, iş gücü modundaki her iş merkezi × hafta için hafta içi (18:00-21:00, kişi başı ≤ 2,5 sa) ve
hafta sonu (Cmt/Paz 08:00-18:00, kişi başı ≤ 8,5 sa) fazla mesai potansiyelini verimli saat olarak tutar.
Haftalık iş gücünde zaten onaylı fazla mesai varsa potansiyel onun üstüne hesaplanır; önceki plandan kalan
"onay bekliyor" önerileri yeniden hesapta sıfırdan değerlendirilir.

Kullanılan saatler `proposals()` ile kişi/gün önerisine çevrilir ve plan yazılırken haftalık iş gücüne
`overtime_proposed=True` olarak işlenir (onay: haftalık iş gücü ekranı).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import WorkCenter, WorkCenterWeek
from app.services import capacity as cap
from app.services.orders import DELIVERY_BUFFER_DAYS

EPS = 1e-6


def target_week_index(weeks: list[date], due: date) -> int:
    """Hedef bitiş haftası: (etkin termin − teslim tamponu) hangi haftaya düşüyor. Ufuk öncesi ⇒ -1."""
    t = due - timedelta(days=DELIVERY_BUFFER_DAYS)
    if not weeks or t < weeks[0]:
        return -1
    for i, wk in enumerate(weeks):
        if wk <= t < wk + timedelta(days=7):
            return i
    return len(weeks) - 1


@dataclass
class OvertimePool:
    potential: dict[tuple[int, date], float] = field(default_factory=dict)  # kalan FM kapasitesi (verimli saat)
    used: dict[tuple[int, date], float] = field(default_factory=dict)
    meta: dict[tuple[int, date], dict] = field(default_factory=dict)
    proposed_base: dict[tuple[int, date], float] = field(default_factory=dict)  # eski plandan kalan öneri (kapasiteden düşülür)

    def has_capacity(self) -> bool:
        return any(v > EPS for v in self.potential.values())

    def merged(self, remaining: dict) -> dict:
        out = dict(remaining)
        for k, v in self.potential.items():
            if v > EPS:
                out[k] = out.get(k, 0.0) + v
        return out

    def split_usage(self, remaining: dict, merged_before: dict, merged_after: dict) -> tuple[dict, set]:
        """Birleşik kapasite üzerinde yapılan yerleşimi normal / fazla mesai olarak ayırır.
        Yeni normal kalan sözlüğü ve fazla mesai kullanılan hücreleri döner."""
        new_rem = dict(remaining)
        ot_cells: set = set()
        for k, before in merged_before.items():
            used = before - merged_after.get(k, 0.0)
            if used <= EPS:
                continue
            if k in self.potential:
                normal_before = remaining.get(k, 0.0)
                ot = max(0.0, used - normal_before)
                new_rem[k] = max(0.0, normal_before - used)
                if ot > EPS:
                    self.potential[k] = max(0.0, self.potential[k] - ot)
                    self.used[k] = self.used.get(k, 0.0) + ot
                    ot_cells.add(k)
            else:
                new_rem[k] = merged_after.get(k, 0.0)
        return new_rem, ot_cells


def build_pool(db: Session, wcs: list[WorkCenter], weeks: list[date]) -> OvertimePool:
    pool = OvertimePool()
    for w in wcs:
        if (w.planning_mode or "labor") == "line":
            continue
        reserve = 1.0 - max(0.0, min(float(getattr(w, "planning_reserve_pct", 0.0) or 0.0), 99.0)) / 100.0
        for wk in weeks:
            ov = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == w.id, WorkCenterWeek.week_start == wk).first()
            p = cap.week_profile(db, w, wk)
            hc = int(p.get("headcount") or 0) + int(p.get("overtime_extra_headcount") or 0)  # mesaiye alınabilecek kişi (ek kişi dahil)
            ratio = float(p.get("overtime_efficiency_ratio") or 0.0)
            wdays = int(p.get("working_days") or 0)
            free_we = len(cap.weekend_candidate_days(w, wk, ov))
            existing = float(p.get("overtime_capacity_hours") or 0.0)
            proposed = bool(ov.overtime_proposed) if ov else False
            key = (w.id, wk)
            pool.meta[key] = {"wc_code": w.code, "headcount": hc, "ratio": ratio, "working_days": wdays, "free_weekend_days": free_we,
                              "reserve": reserve, "approved_existing": 0.0 if proposed else existing, "person_cap": None}
            if hc <= 0 or ratio <= 0:
                continue
            person_week = cap.OVERTIME_MAX_HOURS * wdays + cap.WEEKEND_OVERTIME_MAX_HOURS * free_we  # kişi başı nominal sa/hafta
            from app.core.config import get_settings
            st_ = get_settings()
            # Aylık kişi başı sınır haftaya indirgenir (100 sa/ay ≈ 23 sa/hafta); yıllık sınır bursta engel olmaz,
            # onay/elle giriş denetiminde (overtime_cap_violation) ve raporda (yıl içi birikim) izlenir.
            month_cap = st_.overtime_monthly_cap_hours / 4.33 if st_.overtime_monthly_cap_hours > 0 else 0
            scale = min(1.0, month_cap / person_week) if month_cap > 0 and person_week > 0 else 1.0
            full = hc * person_week * ratio * reserve * scale
            pool.meta[key]["person_cap"] = person_week * scale  # kişi başı nominal sa/hafta tavanı (aylık sınırdan)
            if proposed:
                pool.proposed_base[key] = existing * reserve  # eski öneri: taban kapasiteden düşülür, havuz sıfırdan
                pool.potential[key] = max(0.0, full)
            else:
                pool.potential[key] = max(0.0, full - existing * reserve)
    return pool


def proposals(pool: OvertimePool) -> list[dict]:
    """Kullanılan fazla mesai saatini kişi/gün önerisine çevirir (önce hafta içi, sonra hafta sonu)."""
    out: list[dict] = []
    for key, hours in sorted(pool.used.items(), key=lambda kv: (kv[0][1], pool.meta[kv[0]]["wc_code"])):
        if hours <= EPS:
            continue
        m = pool.meta[key]
        hc, ratio, wdays, free_we, reserve = m["headcount"], m["ratio"], m["working_days"], m["free_weekend_days"], m["reserve"]
        wd_unit = cap.OVERTIME_MAX_HOURS * ratio * reserve  # kişi-gün başına verimli saat
        we_unit = cap.WEEKEND_OVERTIME_MAX_HOURS * ratio * reserve
        wd_cap = hc * wdays * wd_unit
        if hours <= wd_cap + EPS and wdays > 0:
            persons = max(1, min(hc, math.ceil(hours / (wdays * wd_unit) - EPS)))
            days = max(1, min(wdays, math.ceil(hours / (persons * wd_unit) - EPS)))
            we_persons, we_days = 0, 0
            added = persons * days * wd_unit
        else:
            # Hafta içi tam + hafta sonu; kişi başı nominal saat tavanını (aylık sınır) aşmayan en küçük gün sayısı
            person_cap = m.get("person_cap") or (cap.OVERTIME_MAX_HOURS * wdays + cap.WEEKEND_OVERTIME_MAX_HOURS * free_we)
            persons, days = (hc, wdays) if wdays > 0 else (0, 0)
            while days > 0 and days * cap.OVERTIME_MAX_HOURS > person_cap + EPS:
                days -= 1
            rest = max(0.0, hours - persons * days * wd_unit)
            we_days = 0
            while we_days < free_we and rest > EPS and (days * cap.OVERTIME_MAX_HOURS + (we_days + 1) * cap.WEEKEND_OVERTIME_MAX_HOURS) <= person_cap + EPS:
                we_days += 1
                rest = max(0.0, hours - persons * days * wd_unit - hc * we_days * we_unit)
            we_persons = max(1, min(hc, math.ceil((hours - persons * days * wd_unit) / (we_days * we_unit) - EPS))) if we_days > 0 else 0
            added = persons * days * wd_unit + we_persons * we_days * we_unit
        out.append({
            "work_center_id": key[0], "work_center_code": m["wc_code"], "week_start": key[1].isoformat(),
            "hours": round(hours, 1), "added_capacity_hours": round(added, 1),
            "headcount": hc, "weekday_persons": persons, "weekday_days": days, "weekday_hours_per_person": cap.OVERTIME_MAX_HOURS,
            "weekend_persons": we_persons, "weekend_days": we_days, "weekend_hours_per_person": cap.WEEKEND_OVERTIME_MAX_HOURS,
            "person_hours": round(days * cap.OVERTIME_MAX_HOURS + we_days * cap.WEEKEND_OVERTIME_MAX_HOURS, 1),
            "efficiency_ratio": round(ratio, 3), "approved_existing_hours": round(m["approved_existing"], 1),
            "explanation": (f"{persons} kişi × {cap.OVERTIME_MAX_HOURS} sa × {days} gün" if persons else "")
                           + (f" + hafta sonu {we_persons} kişi × {cap.WEEKEND_OVERTIME_MAX_HOURS} sa × {we_days} gün" if we_days else "")
                           + f" × verim {round(ratio, 2)} = {round(added, 1)} sa",
        })
    return out


def apply_proposals(db: Session, props: list[dict], wc_ids: list[int], weeks: list[date], *, clear_existing: bool = True) -> int:
    """Önerileri haftalık iş gücüne 'onay bekliyor' olarak yazar. Onaylı fazla mesaisi olan hücre atlanır
    (öneri onaylının üstüne eklenir; kayıt tek satır olduğundan ayrı gösterilemez, plan sonucunda görünür)."""
    if not weeks:
        return 0
    if clear_existing:
        rows = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id.in_(wc_ids), WorkCenterWeek.week_start >= weeks[0],
                                               WorkCenterWeek.week_start <= weeks[-1], WorkCenterWeek.overtime_proposed.is_(True)).all()
        for r in rows:
            r.overtime_headcount = r.overtime_days = r.overtime_hours_per_person = None
            r.weekend_overtime_headcount = r.weekend_overtime_days = r.weekend_overtime_hours_per_person = None
            r.overtime_proposed = False
        db.flush()
    n = 0
    for p in props:
        wk = date.fromisoformat(p["week_start"])
        row = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == p["work_center_id"], WorkCenterWeek.week_start == wk).first()
        if row is None:
            row = WorkCenterWeek(work_center_id=p["work_center_id"], week_start=wk)
            db.add(row)
        elif not row.overtime_proposed and (row.overtime_headcount or row.weekend_overtime_headcount):
            continue  # onaylı fazla mesai var; üstüne yazma
        row.overtime_headcount = p["weekday_persons"] or None
        row.overtime_days = p["weekday_days"] if p["weekday_persons"] else None
        row.overtime_hours_per_person = cap.OVERTIME_MAX_HOURS if p["weekday_persons"] else None
        row.weekend_overtime_headcount = p["weekend_persons"] or None
        row.weekend_overtime_days = p["weekend_days"] if p["weekend_persons"] else None
        row.weekend_overtime_hours_per_person = cap.WEEKEND_OVERTIME_MAX_HOURS if p["weekend_persons"] else None
        row.overtime_proposed = True
        n += 1
    db.flush()
    return n
