"""Termine yakın (JIT) yerleştirme ve ara stok (WIP) sınırı — haftalık plan son geçişi.

Otomatik plan önce mevcut (ASAP, termine göre öncelikli) yerleştirmeyi yapar; bu modül sonuç
taslak satırlarını **yalnızca daha geç haftalara** kaydırır:

* JIT modu: her sipariş/partinin son operasyonu "etkin termin − tampon gün" (varsayılan 2 gün)
  haftasına, öncüller ardılın ilk haftasına doğru geriye kaydırılır. Kapasite yoksa satır olduğu yerde
  kalır (erken üretim); sipariş takvimindeki "termine kalan gün" bunu gösterir.
* Ara stok sınırı: yarımamül kartında azami adet ve/veya azami gün tanımlıysa öncül operasyon,
  ardılın kümülatif tüketiminden bu sınırdan fazla önde üretilemez (adet) / ardıldan bu kadar günden
  daha önce başlayamaz (gün). Yalnızca gereken kadar kaydırılır; kapasite izin vermiyorsa uyarı üretilir.

Güvenlik kuralları (mevcut motorun kabullerine göre koruyucu):
* Satırlar hiçbir zaman daha erkene alınmaz, ufuk dışına çıkmaz, termin hedef haftasını geçmez.
* Her hafta öncül kümülatifi ≥ ardıl kümülatifi korunur (ardıl haftada öncülden fazla olamaz).
* Dizilim/makine kapasiteli satırlar (machine_id), birlikte sevk satırları ve tahmin/manuel satırlar taşınmaz.
* Kapasite haritası (`remaining`) taşımayla birlikte güncellenir; toplam planlanan saat değişmez.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Item, RoutingOperation
from app.services.plan_draft import DraftLine

EPS = 1e-6


@dataclass
class PlacementNote:
    kind: str  # jit_moved / wip_cap_moved / wip_cap_violation / jit_skipped
    label: str
    detail: str
    hours: float = 0.0
    qty: float = 0.0

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "detail": self.detail, "hours": round(self.hours, 2), "qty": round(self.qty, 2)}


def _effective_due(order) -> date:
    return order.revised_due_date or order.due_date


def _entity_key(line: DraftLine) -> tuple:
    return ("b", line.production_batch_id) if line.production_batch_id else ("o", line.order_id)


def _qty_per_hour(line: DraftLine) -> float:
    return (line.planned_qty / line.planned_hours) if line.planned_hours > EPS else 0.0


def _cum_qty(lines: list[DraftLine], week_idx: dict[date, int]) -> list[float]:
    per = [0.0] * len(week_idx)
    for ln in lines:
        per[week_idx[ln.week_start]] += ln.planned_qty
    out, run = [], 0.0
    for v in per:
        run += v
        out.append(run)
    return out


def _movable(ln: DraftLine) -> bool:
    return ln.machine_id is None and ln.mode == "auto"


def _move_from_line(
    ln: DraftLine, qty_max: float, dest_range: list[int], weeks: list[date], remaining: dict,
    cum: list[float], succ_cum: list[float] | None, week_idx: dict[date, int],
) -> tuple[list[DraftLine], float, float]:
    """ln satırından en fazla qty_max miktarı dest_range haftalarına (sırayla) taşır.
    Dönüş: (yeni parçalar dahil satır listesi, taşınan saat, taşınan miktar). Kümülatif koruma uygulanır."""
    a = week_idx[ln.week_start]
    qph = _qty_per_hour(ln)
    if qph <= EPS:
        return [ln], 0.0, 0.0
    hours_left, qty_left = ln.planned_hours, ln.planned_qty
    budget_qty = min(qty_max, qty_left)
    pieces: list[DraftLine] = []
    moved_h = moved_q = 0.0
    for b in dest_range:
        if budget_qty <= EPS or b <= a:
            continue
        free = remaining.get((ln.work_center_id, weeks[b]), 0.0)
        if free <= EPS:
            continue
        slack = float("inf")
        if succ_cum is not None:
            for w in range(a, b):
                slack = min(slack, cum[w] - succ_cum[w])
        mv_qty = min(budget_qty, free * qph, slack if slack != float("inf") else budget_qty)
        if mv_qty <= EPS:
            if slack != float("inf") and slack <= EPS:
                break
            continue
        mv_h = mv_qty / qph
        pieces.append(replace(ln, week_start=weeks[b], planned_hours=round(mv_h, 6), planned_qty=round(mv_qty, 6)))
        remaining[(ln.work_center_id, weeks[b])] = free - mv_h
        remaining[(ln.work_center_id, weeks[a])] = remaining.get((ln.work_center_id, weeks[a]), 0.0) + mv_h
        for w in range(a, b):
            cum[w] -= mv_qty
        hours_left -= mv_h
        qty_left -= mv_qty
        budget_qty -= mv_qty
        moved_h += mv_h
        moved_q += mv_qty
    out = [replace(ln, planned_hours=round(hours_left, 6), planned_qty=round(qty_left, 6))] if hours_left > EPS else []
    return out + pieces, moved_h, moved_q


def _push_latest(op_lines: list[DraftLine], succ_cum: list[float] | None, latest_idx: int, weeks: list[date], remaining: dict) -> tuple[list[DraftLine], float]:
    """JIT: satırları en geç `latest_idx` haftasına doğru (geç haftadan başlayarak) mümkün olduğunca kaydırır."""
    week_idx = {w: i for i, w in enumerate(weeks)}
    cum = _cum_qty(op_lines, week_idx)
    moved = 0.0
    out: list[DraftLine] = []
    for ln in sorted(op_lines, key=lambda l: week_idx[l.week_start], reverse=True):
        a = week_idx[ln.week_start]
        if not _movable(ln) or a >= latest_idx:
            out.append(ln)
            continue
        parts, mh, _ = _move_from_line(ln, ln.planned_qty, list(range(latest_idx, a, -1)), weeks, remaining, cum, succ_cum, week_idx)
        out.extend(parts)
        moved += mh
    return out, moved


def _enforce_qty_cap(op_lines: list[DraftLine], succ_cum: list[float], cap_qty: float, latest_idx: int, weeks: list[date], remaining: dict) -> tuple[list[DraftLine], float, float]:
    """Ara stok adet sınırı: her hafta öncül kümülatifi − ardıl kümülatifi ≤ cap. Yalnızca gereken kadar kaydırır."""
    week_idx = {w: i for i, w in enumerate(weeks)}
    lines = list(op_lines)
    moved = 0.0
    unmet = 0.0
    for _ in range(200):
        cum = _cum_qty(lines, week_idx)
        viol = next((w for w in range(len(weeks)) if cum[w] - succ_cum[w] > cap_qty + EPS), None)
        if viol is None:
            break
        excess = cum[viol] - succ_cum[viol] - cap_qty
        # Kaynak: viol haftasına kadar üreten en geç taşınabilir satır
        srcs = [l for l in lines if _movable(l) and week_idx[l.week_start] <= viol]
        if not srcs:
            unmet = max(unmet, excess)
            break
        src = max(srcs, key=lambda l: week_idx[l.week_start])
        lines.remove(src)
        parts, mh, mq = _move_from_line(src, excess, list(range(viol + 1, latest_idx + 1)), weeks, remaining, cum, succ_cum, week_idx)
        lines.extend(parts)
        moved += mh
        if mq <= EPS:
            unmet = max(unmet, excess)
            break
    return lines, moved, unmet


def _enforce_min_week(op_lines: list[DraftLine], succ_cum: list[float], min_idx: int, latest_idx: int, weeks: list[date], remaining: dict) -> tuple[list[DraftLine], float, float]:
    """Ara stok gün sınırı: öncül satırları min_idx haftasından önce olamaz; gereken kadar kaydırır."""
    week_idx = {w: i for i, w in enumerate(weeks)}
    cum = _cum_qty(op_lines, week_idx)
    out: list[DraftLine] = []
    moved = unmet = 0.0
    for ln in sorted(op_lines, key=lambda l: week_idx[l.week_start], reverse=True):
        a = week_idx[ln.week_start]
        if not _movable(ln) or a >= min_idx:
            out.append(ln)
            continue
        parts, mh, mq = _move_from_line(ln, ln.planned_qty, list(range(min_idx, latest_idx + 1)), weeks, remaining, cum, succ_cum, week_idx)
        out.extend(parts)
        moved += mh
        unmet += max(ln.planned_qty - mq, 0.0)
    return out, moved, unmet


def apply_placement_pass(
    db: Session,
    lines: list[DraftLine],
    weeks: list[date],
    remaining: dict,
    *,
    mode: str = "asap",
    buffer_days: int = 2,
    skip_order_ids: set[int] | None = None,
    jit: bool | None = None,
) -> tuple[list[DraftLine], list[dict]]:
    """Yerleştirme son geçişi. mode: asap (yalnız ara stok sınırı) · jit (termine yakın) · flow (akış:
    bitiş operasyonu yerinde kalır, öncüller ardılın haftasına yaslanır). Yeni satır listesi ve notlar döner."""
    if jit is not None:  # geriye uyumluluk
        mode = "jit" if jit else mode
    jit = mode == "jit"
    flow = mode == "flow"
    if not lines:
        return lines, []
    week_idx = {w: i for i, w in enumerate(weeks)}
    op_ids = {ln.operation_id for ln in lines}
    ops: dict[int, RoutingOperation] = {o.id: o for o in db.query(RoutingOperation).filter(RoutingOperation.id.in_(op_ids)).all()}
    wip_codes = {(o.semi_finished_code or "").strip() for o in ops.values() if o.semi_finished_code}
    wip_items: dict[str, Item] = {}
    if wip_codes:
        for it in db.query(Item).filter(Item.code.in_(wip_codes)).all():
            if (it.max_wip_qty or 0) > 0 or (it.max_wip_days or 0) > 0:
                wip_items[it.code] = it
    if not jit and not flow and not wip_items:
        return lines, []

    from app.services import scenarios as scen

    rules = scen.RuleLookup(db)
    skip_order_ids = skip_order_ids or set()
    groups: dict[tuple, list[DraftLine]] = defaultdict(list)
    untouched: list[DraftLine] = []
    for ln in lines:
        if ln.mode != "auto" or ln.order_id in skip_order_ids or ln.week_start not in week_idx or ln.operation_id not in ops:
            untouched.append(ln)
        else:
            groups[_entity_key(ln)].append(ln)

    notes: list[PlacementNote] = []
    result: list[DraftLine] = list(untouched)
    for key, glines in groups.items():
        anchor = glines[0].order
        label = glines[0].label or anchor.order_no
        if key[0] == "b":
            dues = [_effective_due(l.order) for l in glines if l.order is not None]
            due = min(dues) if dues else _effective_due(anchor)
        else:
            due = _effective_due(anchor)
        target_day = due - timedelta(days=max(int(buffer_days), 0))
        target_idx = -1 if target_day < weeks[0] else len(weeks) - 1
        for i, w in enumerate(weeks):
            if w <= target_day < w + timedelta(days=7):
                target_idx = i
        by_op: dict[int, list[DraftLine]] = defaultdict(list)
        for ln in glines:
            by_op[ln.operation_id].append(ln)
        jobs: dict[int, list[int]] = defaultdict(list)
        for oid in by_op:
            jobs[ops[oid].item_id].append(oid)
        for item_id in jobs:
            jobs[item_id].sort(key=lambda oid: ops[oid].seq)
        finish_ops = jobs.get(anchor.item_id, [])
        wip_jobs = [jobs[i] for i in jobs if i != anchor.item_id]
        entity_lines: dict[int, list[DraftLine]] = {oid: list(v) for oid, v in by_op.items()}
        moved_jit = moved_cap = 0.0

        def process_job(op_seq: list[int], succ_first: int | None, succ_last: int | None, succ_cum: list[float] | None):
            nonlocal moved_jit, moved_cap
            succ_op = None  # ayni is icinde bir sonraki operasyon (gecis kurali bekleme suresi icin)
            for oid in reversed(op_seq):
                op = ops[oid]
                wip = wip_items.get((op.semi_finished_code or "").strip())
                if (jit and target_idx >= 0) or (flow and succ_first is not None):
                    # jit: son operasyon termin hedef haftasina, onculler ardilin ilk haftasina;
                    # flow: son operasyon yerinde (en erken cikti), yalnizca onculler ardila yaslanir.
                    # flow: parça işinin son operasyonu (succ_op None, ardıl = montaj) montajın son haftasına kadar
                    # yaslanabilir; kümülatif koruma (öncül ≥ ardıl) her hafta için _move_from_line içinde uygulanır.
                    if succ_first is None:
                        latest = target_idx
                    elif flow and succ_op is None and succ_last is not None:
                        latest = succ_last
                    else:
                        latest = succ_first
                    if succ_op is not None and succ_first is not None:
                        # Gecis kurali bekleme suresi (ornegin 14 gun): oncul, ardildan en az bu kadar once bitmeli.
                        rule = rules.get(ops[oid].item if hasattr(ops[oid], "item") else anchor.item, op, succ_op)
                        if rule is not None and getattr(rule, "wait_minutes", 0) > 0:
                            latest = min(latest, succ_first - int(math.ceil(rule.wait_minutes / (7 * 24 * 60))))
                    new, mh = _push_latest(entity_lines[oid], succ_cum, latest, weeks, remaining)
                    entity_lines[oid], moved_jit = new, moved_jit + mh
                if wip is not None and succ_cum is not None and succ_last is not None:
                    if (wip.max_wip_qty or 0) > 0:
                        new, mh, unmet = _enforce_qty_cap(entity_lines[oid], succ_cum, float(wip.max_wip_qty), succ_last, weeks, remaining)
                        entity_lines[oid], moved_cap = new, moved_cap + mh
                        if unmet > EPS:
                            notes.append(PlacementNote("wip_cap_violation", label, f"{op.semi_finished_code}: ara stok azami {wip.max_wip_qty:g} adeti {round(unmet, 1)} adet aşıyor (öncül daha geç çalışamıyor: kapasite yok)", qty=unmet))
                    if (wip.max_wip_days or 0) > 0 and succ_first is not None:
                        min_idx = succ_first - int(math.ceil(float(wip.max_wip_days) / 7.0))
                        if min_idx > 0:
                            new, mh, unmet = _enforce_min_week(entity_lines[oid], succ_cum, min_idx, succ_last, weeks, remaining)
                            entity_lines[oid], moved_cap = new, moved_cap + mh
                            if unmet > EPS:
                                notes.append(PlacementNote("wip_cap_violation", label, f"{op.semi_finished_code}: öncül ardıldan {wip.max_wip_days} günden daha önce başlıyor; {round(unmet, 1)} adet kaydırılamadı (kapasite yok)", qty=unmet))
                cur = entity_lines[oid]
                succ_op = op
                idxs = [week_idx[l.week_start] for l in cur]
                succ_first = min(idxs) if idxs else succ_first
                succ_last = max(idxs) if idxs else succ_last
                succ_cum = _cum_qty(cur, week_idx) if cur else succ_cum
            return succ_first, succ_last, succ_cum

        if finish_ops:
            f_first, f_last, f_cum = process_job(finish_ops, None, None, None)
        else:
            f_first, f_last, f_cum = None, None, None
        for job in wip_jobs:
            process_job(job, f_first, f_last, f_cum)
        for oid in by_op:
            result.extend(entity_lines[oid])
        if moved_jit > EPS and flow:
            notes.append(PlacementNote("flow_aligned", label, f"{round(moved_jit, 1)} saat öncül üretimi ardılın haftasına yaslandı (ara stok azaltıldı, bitiş değişmedi)", hours=moved_jit))
        elif moved_jit > EPS:
            notes.append(PlacementNote("jit_moved", label, f"{round(moved_jit, 1)} saat termine yakın haftalara kaydırıldı (hedef hafta {weeks[target_idx].isoformat() if 0 <= target_idx < len(weeks) else '-'})", hours=moved_jit))
        if moved_cap > EPS:
            notes.append(PlacementNote("wip_cap_moved", label, f"ara stok sınırı için {round(moved_cap, 1)} saat öncül üretimi ardıla yaklaştırıldı", hours=moved_cap))
        if jit and target_idx < 0:
            notes.append(PlacementNote("jit_skipped", label, "termin ufkun başından önce; JIT kaydırma yok"))
    return result, [n.as_dict() for n in notes]
