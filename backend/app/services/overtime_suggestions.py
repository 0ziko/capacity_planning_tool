"""Darboğazdan fazla mesai önerisi (plan revizyonu içinde, onaya tabi).

Hesaplanmış bir revizyonun önerilen planında kapasite yetersizliğinden plansız kalan saatler
iş merkezi bazında toplanır; dolu haftalara (doluluk ≥ %95, yoksa ufuktaki haftalar sırayla)
18:00-21:00 penceresinde kişi başı ≤ 2,5 saat fazla mesai önerilir. Kısıtlar:
  * fazla mesai kişi sayısı haftanın kişi sayısını aşamaz (aynı ekip geç kalır),
  * gün sayısı haftanın çalışma gününü aşamaz,
  * verimli katkı = kişi × 2,5 × (vardiya verim oranı) × gün.
Öneri yalnızca hesaptır; planlamacı taslağa ekler, yeniden hesaplar ve onaylarsa haftalık iş gücüne yazılır.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import PlanRevision, WorkCenter
from app.schemas import OvertimeSuggestionOut, OvertimeSuggestionRow
from app.services import capacity as cap

FULL_WEEK_UTILIZATION = 0.95
SHORTFALL_REASONS = {"kapasite_yetersiz", "capacity"}


def suggest_for_revision(db: Session, rev: PlanRevision) -> OvertimeSuggestionOut:
    from app.services.plan_revisions import _req

    by_kind = {s.kind: s for s in rev.snapshots}
    if "apply" not in by_kind or "proposed" not in by_kind:
        raise ValueError("Öneri için revizyon önce hesaplanmalı")
    apply = json.loads(by_kind["apply"].payload_json or "{}")
    prop = json.loads(by_kind["proposed"].payload_json or "{}")
    req = _req(rev)
    weeks = [req.start_week + timedelta(weeks=i) for i in range(req.weeks)]
    wcs = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if req.work_center_ids:
        wcs = wcs.filter(WorkCenter.id.in_(req.work_center_ids))
    else:
        wcs = wcs.filter(WorkCenter.is_planned.is_(True))
    wcs = wcs.order_by(WorkCenter.code).all()
    by_code = {w.code: w for w in wcs}

    load: dict[tuple[int, date], float] = {}
    for ln in apply.get("plan_lines") or []:
        if ln.get("mode") == "forecast":
            continue
        key = (int(ln["work_center_id"]), date.fromisoformat(str(ln["week_start"])[:10]))
        load[key] = load.get(key, 0.0) + float(ln.get("planned_hours") or 0)

    shortfall: dict[str, float] = {}
    orders_by_wc: dict[str, list[str]] = {}
    for u in prop.get("unplanned") or []:
        reason = str(u.get("reason") or "")
        if reason and reason not in SHORTFALL_REASONS:
            continue
        code = str(u.get("work_center_code") or "")
        if code not in by_code:
            continue
        shortfall[code] = shortfall.get(code, 0.0) + float(u.get("hours") or 0)
        no = str(u.get("order_no") or "")
        if no and no not in orders_by_wc.setdefault(code, []):
            orders_by_wc[code].append(no)

    rows: list[OvertimeSuggestionRow] = []
    notes: list[str] = []
    covered_total = 0.0
    for code, need in sorted(shortfall.items()):
        wc = by_code[code]
        if wc.planning_mode == "line":
            notes.append(f"{code}: dizilim (hat) modunda fazla mesai istasyon saatleriyle tanımlanır; öneri üretilmedi ({round(need, 1)} sa açık).")
            continue
        remaining = need
        profiles = {wk: cap.week_profile(db, wc, wk) for wk in weeks}
        full = [wk for wk in weeks if profiles[wk]["capacity_hours"] > 0
                and load.get((wc.id, wk), 0.0) / cap.apply_planning_reserve(wc, profiles[wk]["capacity_hours"]) >= FULL_WEEK_UTILIZATION]
        candidates = full or [wk for wk in weeks if profiles[wk]["capacity_hours"] > 0]
        for wk in candidates:
            if remaining <= 1e-6:
                break
            p = profiles[wk]
            headcount = int(p["headcount"] or 0)
            current_ot = int(p["overtime_headcount"] or 0)
            available = headcount - current_ot
            n_days = int(p["working_days"] or 0)
            ratio = float(p["overtime_efficiency_ratio"] or 0)
            per_person_week = cap.OVERTIME_MAX_HOURS * ratio * n_days
            if available <= 0 or n_days <= 0 or per_person_week <= 0:
                continue
            persons = min(available, int(math.ceil(remaining / per_person_week)))
            added = round(persons * per_person_week, 2)
            rows.append(OvertimeSuggestionRow(
                work_center_id=wc.id, work_center_code=wc.code, week_start=wk,
                headcount=headcount, current_overtime_headcount=current_ot,
                suggested_overtime_headcount=current_ot + persons, overtime_days=n_days,
                overtime_hours_per_person=cap.OVERTIME_MAX_HOURS, efficiency_ratio=round(ratio, 3),
                added_capacity_hours=added, utilization=round(load.get((wc.id, wk), 0.0) / cap.apply_planning_reserve(wc, p["capacity_hours"]), 3) if p["capacity_hours"] > 0 else 0.0,
                shortfall_hours_before=round(remaining, 2), shortfall_hours_after=round(max(remaining - added, 0.0), 2),
                order_nos=orders_by_wc.get(code, [])[:20],
                explanation=f"{persons} kişi × {cap.OVERTIME_MAX_HOURS} sa × verim {round(ratio, 2)} × {n_days} gün = {added} sa",
            ))
            remaining -= added
        covered_total += need - max(remaining, 0.0)
        if remaining > 1e-6:
            notes.append(f"{code}: fazla mesai kısıtları içinde {round(remaining, 1)} sa açık kapatılamıyor (kişi/gün sınırı). Ek personel, ek vardiya veya termin görüşmesi gerekir.")
    total = round(sum(shortfall.values()), 2)
    return OvertimeSuggestionOut(
        revision_id=rev.id, total_shortfall_hours=total, covered_hours=round(covered_total, 2),
        uncovered_hours=round(max(total - covered_total, 0.0), 2), suggestions=rows, notes=notes,
        window="18:00-21:00", max_hours_per_person=cap.OVERTIME_MAX_HOURS,
    )
