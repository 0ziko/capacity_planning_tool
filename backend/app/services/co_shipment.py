"""Optional co-shipment mode: selected order positions finish in the same week by target ready date."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, WorkCenter
from app.schemas import CoShipmentOptions
from app.services import capacity as cap
from app.services import production_batches as pbatches
from app.services import scenarios as scen
from app.services.orders import effective_due
from app.services.plan_draft import DraftLine


@dataclass
class CoShipmentGroup:
    order_no: str
    orders: list[Order]
    target_ready_date: date
    position_nos: list[str]


@dataclass
class CoShipmentOutcome:
    lines: list[DraftLine] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    handled_order_ids: set[int] = field(default_factory=set)


def _week_index(weeks: list[date], d: date) -> int:
    """Latest week index whose Monday is on or before d."""
    ws = cap.week_start(d)
    idx = -1
    for i, w in enumerate(weeks):
        if w <= ws:
            idx = i
        else:
            break
    return idx


def _completion_week_start(lines: list[DraftLine], order_id: int) -> date | None:
    wks = [ln.week_start for ln in lines if ln.order_id == order_id]
    return max(wks) if wks else None


def _ready_date_from_week(week_start: date) -> date:
    """Plan haftasinin son gunu (Pazar) — sevke hazirlik gosterimi icin."""
    return week_start + timedelta(days=6)


def _place_quantity_max_week(
    anchor: Order,
    quantity: float,
    label: str,
    production_batch_id: int | None,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
    max_last_week_idx: int,
) -> tuple[list[DraftLine], list[dict]]:
    lines: list[DraftLine] = []
    unplanned: list[dict] = []
    prev_first_idx = 0
    prev_last_idx = 0
    prev_op = None
    for op in anchor.item.operations:
        if op.work_center_id not in wc_by_id:
            continue
        total_hours = op.hours_for(quantity)
        hours_left = total_hours
        if prev_op is None:
            idx = 0
        else:
            rule = rules.get(anchor.item, prev_op, op) if rules else scen.Rule()
            idx = prev_first_idx if rule.rule == "cycles" else prev_last_idx
        first_idx = None
        last_idx = None
        while hours_left > 1e-6 and idx <= max_last_week_idx:
            wk = weeks[idx]
            avail = remaining[(op.work_center_id, wk)]
            if avail > 1e-6:
                take = min(avail, hours_left)
                qty = quantity * (take / total_hours) if total_hours > 0 else 0
                lines.append(
                    DraftLine(
                        order=anchor,
                        order_id=anchor.id,
                        operation_id=op.id,
                        work_center_id=op.work_center_id,
                        week_start=wk,
                        planned_hours=round(take, 3),
                        planned_qty=round(qty, 2),
                        production_batch_id=production_batch_id,
                        label=label or anchor.order_no,
                    )
                )
                remaining[(op.work_center_id, wk)] = avail - take
                hours_left -= take
                if first_idx is None:
                    first_idx = idx
                last_idx = idx
            if hours_left > 1e-6:
                idx += 1
        if first_idx is not None:
            prev_first_idx = first_idx
            prev_last_idx = last_idx if last_idx is not None else first_idx
            prev_op = op
        if hours_left > 1e-6:
            unplanned.append(
                {
                    "order_no": label or anchor.order_no,
                    "item_code": anchor.item.code,
                    "operation_seq": op.seq,
                    "work_center_code": wc_by_id[op.work_center_id].code,
                    "hours": round(hours_left, 2),
                }
            )
    return lines, unplanned


def _try_group_at_week(
    group: CoShipmentGroup,
    week_idx: int,
    wc_by_id: dict[int, WorkCenter],
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
) -> tuple[list[DraftLine], dict[tuple[int, date], float]] | None:
    trial = dict(remaining)
    all_lines: list[DraftLine] = []
    for order in group.orders:
        if not order.item or not order.item.operations:
            return None
        ls, un = _place_quantity_max_week(
            order, order.quantity, order.order_no, None, wc_by_id, weeks, trial, rules, week_idx
        )
        if un:
            return None
        all_lines.extend(ls)
    completion_weeks = {_completion_week_start(all_lines, o.id) for o in group.orders}
    if len(completion_weeks) != 1 or None in completion_weeks:
        return None
    if list(completion_weeks)[0] != weeks[week_idx]:
        return None
    return all_lines, trial


def resolve_groups(db: Session, options: CoShipmentOptions) -> list[CoShipmentGroup]:
    if not options.enabled or not options.selections:
        return []
    in_batch = pbatches.batched_order_ids(db)
    groups: list[CoShipmentGroup] = []
    buffer = options.ready_before_delivery_days
    for sel in options.selections:
        q = (
            db.query(Order)
            .options(joinedload(Order.item).joinedload(Item.operations))
            .filter(Order.status == "open", Order.order_no == sel.order_no)
        )
        if sel.position_nos:
            q = q.filter(Order.position_no.in_(sel.position_nos))
        rows = [o for o in q.all() if o.id not in in_batch and o.item and o.item.operations]
        if not rows:
            continue
        due = effective_due(rows[0])
        target = due - timedelta(days=buffer)
        pos = sorted({o.position_no or "" for o in rows}, key=lambda x: (len(x), x))
        groups.append(CoShipmentGroup(order_no=sel.order_no, orders=rows, target_ready_date=target, position_nos=pos))
    return groups


def apply_co_shipment(
    db: Session,
    options: CoShipmentOptions,
    weeks: list[date],
    start: date,
    wc_by_id: dict[int, WorkCenter],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
) -> CoShipmentOutcome:
    out = CoShipmentOutcome()
    if not options.enabled:
        return out

    groups = resolve_groups(db, options)
    for group in sorted(groups, key=lambda g: g.target_ready_date):
        pos_nos = group.position_nos
        due = effective_due(group.orders[0])
        target = group.target_ready_date
        target_idx = _week_index(weeks, target)

        if target < start:
            out.exceptions.append(
                {
                    "code": "TARGET_PAST",
                    "order_no": group.order_no,
                    "position_nos": pos_nos,
                    "target_ready_date": target.isoformat(),
                    "planned_ready_date": None,
                    "deviation_days": (start - target).days,
                    "reason": f"Hedef sevke hazır tarihi ({target.strftime('%d.%m.%Y')}) plan ufku başlangıcından önce.",
                    "suggestion": "Başlangıç haftasını geriye alın veya termin / bekleme gününü gözden geçirin.",
                }
            )
            continue

        placed = None
        placed_idx = None
        if target_idx >= 0:
            for candidate in range(target_idx, -1, -1):
                trial = _try_group_at_week(group, candidate, wc_by_id, weeks, remaining, rules)
                if trial:
                    placed, placed_idx = trial, candidate
                    break

        if placed:
            lines, new_rem = placed
            remaining.clear()
            remaining.update(new_rem)
            out.lines.extend(lines)
            for o in group.orders:
                out.handled_order_ids.add(o.id)
            comp_week = weeks[placed_idx]
            planned_ready = _ready_date_from_week(comp_week)
            on_target = placed_idx <= target_idx
            out.results.append(
                {
                    "order_no": group.order_no,
                    "position_nos": pos_nos,
                    "due_date": due.isoformat(),
                    "target_ready_date": target.isoformat(),
                    "planned_ready_date": planned_ready.isoformat(),
                    "completion_week": comp_week.isoformat(),
                    "same_week_ok": True,
                    "on_target": on_target,
                }
            )
            continue

        alt = None
        alt_idx = None
        for candidate in range(max(target_idx + 1, 0), len(weeks)):
            trial = _try_group_at_week(group, candidate, wc_by_id, weeks, remaining, rules)
            if trial:
                alt, alt_idx = trial, candidate
                break

        if alt:
            lines, new_rem = alt
            remaining.clear()
            remaining.update(new_rem)
            out.lines.extend(lines)
            for o in group.orders:
                out.handled_order_ids.add(o.id)
            comp_week = weeks[alt_idx]
            planned_ready = _ready_date_from_week(comp_week)
            dev = (planned_ready - target).days
            out.exceptions.append(
                {
                    "code": "TARGET_MISSED",
                    "order_no": group.order_no,
                    "position_nos": pos_nos,
                    "target_ready_date": target.isoformat(),
                    "planned_ready_date": planned_ready.isoformat(),
                    "deviation_days": dev,
                    "reason": "Seçili pozların aynı haftada tamamlanması hedef tarihe yetişmiyor; kapasite yetersiz.",
                    "suggestion": "Kapasite artırın, ufku genişletin veya bekleme gününü azaltın.",
                }
            )
            out.results.append(
                {
                    "order_no": group.order_no,
                    "position_nos": pos_nos,
                    "due_date": due.isoformat(),
                    "target_ready_date": target.isoformat(),
                    "planned_ready_date": planned_ready.isoformat(),
                    "completion_week": comp_week.isoformat(),
                    "same_week_ok": True,
                    "on_target": False,
                }
            )
        else:
            out.exceptions.append(
                {
                    "code": "CAPACITY_SAME_WEEK",
                    "order_no": group.order_no,
                    "position_nos": pos_nos,
                    "target_ready_date": target.isoformat(),
                    "planned_ready_date": None,
                    "deviation_days": None,
                    "reason": "Seçili pozların tamamını aynı haftada planlamak mevcut kapasite ile mümkün değil.",
                    "suggestion": "Hafta sayısını artırın, iş merkezi kapasitesini yükseltin veya poz seçimini daraltın.",
                }
            )

    return out
