"""Is tasima: kalan operasyon zincirini hedef haftaya oncelikli yerlestir, yalnizca cakisan IM'leri kaydir."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, PlanLine, RoutingOperation
from app.schemas import AutoPlanRequest, JobMoveOpOut, JobMovePreviewOut
from app.services import capacity as cap
from app.services import planning
from app.services import scenarios as scen
from app.services.bom_tree import explode_order, flatten_fg_operations, has_wip_structure
from app.services.orders import effective_due
from app.services.plan_draft import DraftLine
from app.services.wip import _produced_qty_map

DONE_PCT = 99.5
QTY_MODES = ("remaining", "split")


@dataclass
class JobMove:
    order_id: int
    item_code: str
    start_date: date
    qty_mode: str
    quantity: float | None = None


@dataclass
class RemainingOp:
    operation: RoutingOperation
    display_seq: int
    semi_finished_code: str
    produced_qty: float
    remaining_qty: float
    locked: bool


@dataclass
class RemainingChain:
    order: Order
    ops: list[RemainingOp]
    movable_qty: float
    completed_op_ids: set[int] = field(default_factory=set)
    current_start: date | None = None


def parse_job_move(ch) -> JobMove | None:
    if getattr(ch, "field", "") != "job_move":
        return None
    try:
        data = json.loads(ch.new_value or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    start_raw = data.get("start_date") or ""
    if not start_raw:
        return None
    mode = data.get("qty_mode") or "remaining"
    if mode not in QTY_MODES:
        mode = "remaining"
    qty = data.get("quantity")
    try:
        qty_f = float(qty) if qty not in (None, "") else None
    except (TypeError, ValueError):
        qty_f = None
    return JobMove(
        order_id=int(ch.entity_id),
        item_code=(data.get("item_code") or ch.extra_key or "").strip(),
        start_date=date.fromisoformat(str(start_raw)),
        qty_mode=mode,
        quantity=qty_f,
    )


def job_moves_from_revision(rev) -> list[JobMove]:
    out: list[JobMove] = []
    for ch in rev.changes or []:
        mv = parse_job_move(ch)
        if mv:
            out.append(mv)
    return out


def _load_order(db: Session, order_id: int) -> Order:
    order = (
        db.query(Order)
        .options(
            joinedload(Order.item).joinedload(Item.operations).joinedload(RoutingOperation.work_center),
            joinedload(Order.item).joinedload(Item.bom_lines),
        )
        .filter(Order.id == order_id)
        .first()
    )
    if not order or order.status != "open":
        raise ValueError("Acik siparis bulunamadi")
    return order


def remaining_chain(db: Session, order: Order, wc_ids: list[int] | None = None) -> RemainingChain:
    flats = flatten_fg_operations(db, order.item)
    if not flats and order.item:
        flats = []
        from app.services.bom_tree import FlatOperation

        for op in sorted(order.item.operations or [], key=lambda x: x.seq):
            flats.append(FlatOperation(op, op.seq, op.semi_finished_code or ""))
    if wc_ids:
        allow = set(wc_ids)
        flats = [f for f in flats if f.operation.work_center_id in allow]

    produced = _produced_qty_map(db)
    first_plan: dict[int, date] = {}
    for pl in db.query(PlanLine).filter(PlanLine.order_id == order.id).all():
        cur = first_plan.get(pl.operation_id)
        if cur is None or pl.week_start < cur:
            first_plan[pl.operation_id] = pl.week_start

    ops: list[RemainingOp] = []
    completed: set[int] = set()
    for f in flats:
        op = f.operation
        pq = float(produced.get((order.id, op.id), 0.0))
        rem = max((order.quantity or 0.0) - pq, 0.0)
        locked = rem <= 1e-6 or (order.quantity and pq / order.quantity * 100 >= DONE_PCT)
        if locked:
            completed.add(op.id)
        ops.append(
            RemainingOp(
                operation=op,
                display_seq=f.display_seq,
                semi_finished_code=f.wip_code or op.semi_finished_code or "",
                produced_qty=round(pq, 2),
                remaining_qty=round(rem, 2),
                locked=locked,
            )
        )

    first = next((r for r in ops if not r.locked), None)
    movable = first.remaining_qty if first else 0.0
    current_start = None
    if first:
        current_start = first_plan.get(first.operation.id)
        if current_start is None:
            later = [first_plan[r.operation.id] for r in ops if not r.locked and r.operation.id in first_plan]
            current_start = min(later) if later else None
    return RemainingChain(order=order, ops=ops, movable_qty=movable, completed_op_ids=completed, current_start=current_start)


def preview_move(db: Session, order_id: int, wc_ids: list[int] | None = None) -> JobMovePreviewOut:
    order = _load_order(db, order_id)
    chain = remaining_chain(db, order, wc_ids)
    first_open = True
    op_rows: list[JobMoveOpOut] = []
    consumed: list[str] = []
    seen_wc: set[str] = set()
    for r in chain.ops:
        if r.locked:
            status = "completed"
        elif first_open:
            status = "current"
            first_open = False
        else:
            status = "remaining"
        wc_code = r.operation.work_center.code if r.operation.work_center else ""
        if not r.locked and wc_code and wc_code not in seen_wc:
            seen_wc.add(wc_code)
            consumed.append(wc_code)
        op_rows.append(
            JobMoveOpOut(
                operation_id=r.operation.id,
                operation_seq=r.display_seq,
                operation_name=r.operation.operation_name,
                work_center_id=r.operation.work_center_id,
                work_center_code=wc_code,
                semi_finished_code=r.semi_finished_code,
                produced_qty=r.produced_qty,
                remaining_qty=r.remaining_qty,
                locked=r.locked,
                status=status,
            )
        )
    status = "completed" if not any(not r.locked for r in chain.ops) else (
        "in_progress" if any(r.locked for r in chain.ops) else "not_started"
    )
    return JobMovePreviewOut(
        order_id=order.id,
        order_no=order.order_no,
        item_code=order.item.code if order.item else "",
        quantity=order.quantity,
        movable_qty=chain.movable_qty,
        current_start=chain.current_start,
        status=status,
        ops=op_rows,
        consumed_work_centers=consumed,
    )


def validate_and_payload(db: Session, order_id: int, raw: str, extra_key: str) -> tuple[str, str]:
    """job_move new_value JSON dogrular; (old_value, extra_key) dondurur."""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("Is tasima JSON gecersiz") from exc
    if not isinstance(data, dict):
        raise ValueError("Is tasima JSON gecersiz")
    order = _load_order(db, order_id)
    item_code = (data.get("item_code") or extra_key or order.item.code or "").strip()
    if item_code.upper() != (order.item.code or "").upper():
        raise ValueError("Secilen stok kodu bu siparise ait degil")
    start_raw = data.get("start_date") or ""
    if not start_raw:
        raise ValueError("Yeni baslangic tarihi gerekli")
    start = cap.week_start(date.fromisoformat(str(start_raw)))
    mode = data.get("qty_mode") or "remaining"
    if mode not in QTY_MODES:
        raise ValueError("Miktar kipi remaining veya split olmali")
    chain = remaining_chain(db, order)
    if chain.movable_qty <= 1e-6:
        raise ValueError("Tasinacak kalan operasyon yok (is tamamlanmis)")
    qty = data.get("quantity")
    qty_f: float | None
    try:
        qty_f = float(qty) if qty not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError("Miktar gecersiz") from exc
    if mode == "split":
        if qty_f is None or qty_f <= 1e-6:
            raise ValueError("Kismi tasimada miktar girin")
        if qty_f > chain.movable_qty + 1e-6:
            raise ValueError(f"Kismi miktar kalan {chain.movable_qty} adedi asamaz")
    payload = {
        "item_code": order.item.code,
        "start_date": start.isoformat(),
        "qty_mode": mode,
        "quantity": qty_f,
    }
    old = chain.current_start.isoformat() if chain.current_start else ""
    return json.dumps(payload, ensure_ascii=False), old


def _start_idx(weeks: list[date], start_date: date) -> int:
    wk = cap.week_start(start_date)
    if wk < weeks[0]:
        return 0
    if wk > weeks[-1]:
        raise ValueError("Yeni baslangic tarihi revizyon ufkunun disinda")
    for i, w in enumerate(weeks):
        if w == wk:
            return i
    return 0


def _qty_for_move(chain: RemainingChain, move: JobMove) -> float:
    cap_qty = chain.movable_qty
    if move.qty_mode == "split" and move.quantity is not None:
        return min(float(move.quantity), cap_qty)
    return cap_qty


def _place_remaining(
    db: Session,
    order: Order,
    chain: RemainingChain,
    move_qty: float,
    min_start_idx: int,
    wc_by_id: dict,
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    rules: scen.RuleLookup | None,
) -> tuple[list[DraftLine], list[dict]]:
    qty_by_op = {r.operation.id: min(move_qty, r.remaining_qty) for r in chain.ops if not r.locked}
    if has_wip_structure(order):
        jobs = explode_order(db, order)
        all_lines: list[DraftLine] = []
        all_unplanned: list[dict] = []
        wip_end = min_start_idx
        scale = (move_qty / order.quantity) if order.quantity else 1.0
        for job in jobs.wip_jobs:
            rem_ops = [
                op
                for op in sorted(job.item.operations or [], key=lambda x: x.seq)
                if op.id not in chain.completed_op_ids and op.work_center_id in wc_by_id
            ]
            if not rem_ops:
                continue
            job_qty = job.quantity * scale
            job_qty_by_op = {op.id: qty_by_op.get(op.id, job_qty) for op in rem_ops}
            lbl = f"{order.order_no}/{job.label_suffix}" if job.label_suffix else order.order_no
            ls, un, end_idx = planning._place_quantity(
                order,
                job_qty,
                lbl,
                None,
                wc_by_id,
                weeks,
                remaining,
                rules,
                item=job.item,
                semi_finished_code=job.semi_finished_code,
                min_start_idx=min_start_idx,
                ops=rem_ops,
                qty_by_op=job_qty_by_op,
            )
            all_lines.extend(ls)
            all_unplanned.extend(un)
            wip_end = max(wip_end, end_idx)
        if jobs.finish_job:
            rem_ops = [
                op
                for op in sorted(jobs.finish_job.item.operations or [], key=lambda x: x.seq)
                if op.id not in chain.completed_op_ids and op.work_center_id in wc_by_id
            ]
            if rem_ops:
                fin_qty = jobs.finish_job.quantity * scale
                ls, un, _ = planning._place_quantity(
                    order,
                    fin_qty,
                    order.order_no,
                    None,
                    wc_by_id,
                    weeks,
                    remaining,
                    rules,
                    item=jobs.finish_job.item,
                    semi_finished_code="",
                    min_start_idx=max(wip_end, min_start_idx),
                    ops=rem_ops,
                    qty_by_op={op.id: qty_by_op.get(op.id, fin_qty) for op in rem_ops},
                )
                all_lines.extend(ls)
                all_unplanned.extend(un)
        return all_lines, all_unplanned

    rem_ops = [r.operation for r in chain.ops if not r.locked and r.operation.work_center_id in wc_by_id]
    ls, un, _ = planning._place_quantity(
        order,
        move_qty,
        order.order_no,
        None,
        wc_by_id,
        weeks,
        remaining,
        rules,
        min_start_idx=min_start_idx,
        ops=rem_ops,
        qty_by_op=qty_by_op,
    )
    return ls, un


def _to_draft(pl: PlanLine, order: Order | None) -> DraftLine:
    return DraftLine(
        order=order or pl.order,
        order_id=pl.order_id,
        operation_id=pl.operation_id,
        work_center_id=pl.work_center_id,
        week_start=pl.week_start,
        planned_hours=pl.planned_hours,
        planned_qty=pl.planned_qty,
        mode=pl.mode,
        production_batch_id=pl.production_batch_id,
        label=(order.order_no if order else "") or "",
        semi_finished_code=pl.semi_finished_code or "",
    )


def _scale_line(line: DraftLine, ratio: float) -> DraftLine:
    return DraftLine(
        order=line.order,
        order_id=line.order_id,
        operation_id=line.operation_id,
        work_center_id=line.work_center_id,
        week_start=line.week_start,
        planned_hours=round(line.planned_hours * ratio, 3),
        planned_qty=round(line.planned_qty * ratio, 2),
        mode=line.mode,
        production_batch_id=line.production_batch_id,
        label=line.label,
        semi_finished_code=line.semi_finished_code,
    )


def _place_hours_on_wc(
    line: DraftLine,
    hours: float,
    qty: float,
    start_idx: int,
    prefer_idx: int,
    weeks: list[date],
    remaining: dict[tuple[int, date], float],
    wc_code: str,
) -> tuple[list[DraftLine], float]:
    """Ayni IM'de saat yerlestir: once tercih edilen hafta, sonra ileri."""
    out: list[DraftLine] = []
    hours_left = hours
    total = hours
    idx = max(prefer_idx, start_idx)
    while hours_left > 1e-6 and idx < len(weeks):
        wk = weeks[idx]
        avail = remaining[(line.work_center_id, wk)]
        if avail > 1e-6:
            take = min(avail, hours_left)
            take_qty = qty * (take / total) if total > 0 else 0
            out.append(
                DraftLine(
                    order=line.order,
                    order_id=line.order_id,
                    operation_id=line.operation_id,
                    work_center_id=line.work_center_id,
                    week_start=wk,
                    planned_hours=round(take, 3),
                    planned_qty=round(take_qty, 2),
                    mode=line.mode,
                    production_batch_id=line.production_batch_id,
                    label=line.label,
                    semi_finished_code=line.semi_finished_code,
                )
            )
            remaining[(line.work_center_id, wk)] = avail - take
            hours_left -= take
        idx += 1
    return out, hours_left


def simulate_priority_insert(
    db: Session,
    req: AutoPlanRequest,
    moves: list[JobMove],
    *,
    replace_manual: bool = False,
) -> planning.Simulation:
    """Canli plani koruyarak oncelikli is yerlestir; yalnizca cakisan IM satirlari kayar."""
    start = cap.week_start(req.start_week)
    weeks = [start + timedelta(weeks=i) for i in range(req.weeks)]
    wcs = planning._selected_work_centers(db, req.work_center_ids)
    all_orders = planning._open_orders_with_ops(db)
    if not wcs:
        return planning.Simulation(req.mode, start, weeks, [], [], [], [], 0.0, all_orders, [], [])

    wc_ids = [w.id for w in wcs]
    wc_by_id = {w.id: w for w in wcs}
    wc_code = {w.id: w.code for w in wcs}
    forecast = planning.planned_hours_by_week(db, wc_ids, start, weeks[-1], mode="forecast")
    remaining: dict[tuple[int, date], float] = {}
    for w in wcs:
        for wk in weeks:
            remaining[(w.id, wk)] = max(cap.planning_capacity_hours(db, w, wk) - forecast.get((w.id, wk), 0.0), 0.0)
    live = (
        db.query(PlanLine)
        .options(joinedload(PlanLine.order), joinedload(PlanLine.operation))
        .filter(
            PlanLine.week_start >= start,
            PlanLine.week_start <= weeks[-1],
            PlanLine.work_center_id.in_(wc_ids),
            PlanLine.mode.in_(["auto", "manual"]),
        )
        .all()
    )
    orders_by_id = {o.id: o for o in all_orders}
    for pl in live:
        if pl.order_id not in orders_by_id and pl.order:
            orders_by_id[pl.order_id] = pl.order
    return _simulate_insert(
        db,
        req,
        moves,
        start,
        weeks,
        wcs,
        wc_by_id,
        wc_code,
        remaining,
        sum(remaining.values()),
        live,
        orders_by_id,
        all_orders,
        replace_manual=replace_manual,
    )


def _simulate_insert(
    db: Session,
    req: AutoPlanRequest,
    moves: list[JobMove],
    start: date,
    weeks: list[date],
    wcs,
    wc_by_id,
    wc_code: dict[int, str],
    remaining: dict[tuple[int, date], float],
    capacity_total: float,
    live: list[PlanLine],
    orders_by_id: dict[int, Order],
    all_orders: list[Order],
    *,
    replace_manual: bool,
) -> planning.Simulation:
    if not moves:
        return planning.simulate(db, req)

    rules = scen.RuleLookup(db)
    week_idx = {wk: i for i, wk in enumerate(weeks)}
    frozen: list[DraftLine] = []
    displaced: list[DraftLine] = []
    unplanned: list[dict] = []
    skipped: list[dict] = []
    notes: list[str] = []

    # Birden fazla tasima: tuketilen IM birlesimi + en erken baslangic
    move_specs: list[tuple[JobMove, RemainingChain, float, int, set[int], set[int]]] = []
    consumed_wcs: set[int] = set()
    earliest_idx = len(weeks) - 1
    target_ids: set[int] = set()
    remaining_op_ids: set[int] = set()

    for move in moves:
        order = orders_by_id.get(move.order_id) or _load_order(db, move.order_id)
        if move.item_code and order.item and move.item_code.upper() != order.item.code.upper():
            raise ValueError("Secilen stok kodu bu siparise ait degil")
        chain = remaining_chain(db, order, list(wc_by_id.keys()))
        move_qty = _qty_for_move(chain, move)
        if move_qty <= 1e-6:
            raise ValueError(f"{order.order_no}: tasinacak kalan yok")
        idx = _start_idx(weeks, move.start_date)
        rem_ids = {r.operation.id for r in chain.ops if not r.locked}
        wcs_used = {r.operation.work_center_id for r in chain.ops if not r.locked and r.operation.work_center_id in wc_by_id}
        move_specs.append((move, chain, move_qty, idx, rem_ids, wcs_used))
        consumed_wcs |= wcs_used
        earliest_idx = min(earliest_idx, idx)
        target_ids.add(order.id)
        remaining_op_ids |= rem_ids

    stay_ratio: dict[int, float] = {}
    for move, chain, move_qty, _idx, rem_ids, _wcs in move_specs:
        if chain.movable_qty > 1e-6 and move_qty < chain.movable_qty - 1e-6:
            stay_ratio[chain.order.id] = (chain.movable_qty - move_qty) / chain.movable_qty
        else:
            stay_ratio[chain.order.id] = 0.0

    for pl in live:
        order = orders_by_id.get(pl.order_id)
        draft = _to_draft(pl, order)
        is_manual = pl.mode == "manual"
        freeze_manual = is_manual and not replace_manual
        wk_i = week_idx.get(pl.week_start)
        if wk_i is None:
            frozen.append(draft)
            continue
        target_remaining = pl.order_id in target_ids and pl.operation_id in remaining_op_ids
        if target_remaining:
            ratio = stay_ratio.get(pl.order_id, 0.0)
            if ratio > 1e-6:
                frozen.append(_scale_line(draft, ratio))
            continue
        if freeze_manual or wk_i < earliest_idx or pl.work_center_id not in consumed_wcs:
            frozen.append(draft)
            continue
        displaced.append(draft)

    for line in frozen:
        key = (line.work_center_id, line.week_start)
        if key in remaining:
            remaining[key] = max(remaining[key] - line.planned_hours, 0.0)

    priority_lines: list[DraftLine] = []
    for move, chain, move_qty, idx, _rem_ids, _wcs in move_specs:
        ls, un = _place_remaining(db, chain.order, chain, move_qty, idx, wc_by_id, weeks, remaining, rules)
        priority_lines.extend(ls)
        unplanned.extend(un)
        notes.append(f"{chain.order.order_no} {move_qty:g} adet {weeks[idx].isoformat()} haftasina oncelikli")

    displaced.sort(
        key=lambda l: (
            effective_due(l.order) if l.order else date.max,
            l.order_id,
            l.week_start,
            l.operation_id,
        )
    )
    bumped_nos: set[str] = set()
    bumped_lines: list[DraftLine] = []
    for line in displaced:
        prefer = week_idx.get(line.week_start, earliest_idx)
        placed, leftover = _place_hours_on_wc(
            line,
            line.planned_hours,
            line.planned_qty,
            earliest_idx,
            prefer,
            weeks,
            remaining,
            wc_code.get(line.work_center_id, ""),
        )
        if any(p.week_start != line.week_start for p in placed) or leftover > 1e-6:
            if line.order:
                bumped_nos.add(line.order.order_no)
        bumped_lines.extend(placed)
        if leftover > 1e-6:
            unplanned.append(
                {
                    "order_no": line.label or (line.order.order_no if line.order else ""),
                    "item_code": line.order.item.code if line.order and line.order.item else "",
                    "semi_finished_code": line.semi_finished_code,
                    "operation_seq": line.operation_id,
                    "work_center_code": wc_code.get(line.work_center_id, ""),
                    "hours": round(leftover, 2),
                }
            )

    lines = frozen + priority_lines + bumped_lines
    sim = planning.Simulation(
        req.mode, start, weeks, wcs, lines, unplanned, skipped, capacity_total, all_orders, [], []
    )
    sim.insert_notes = notes  # type: ignore[attr-defined]
    sim.bumped_orders = sorted(bumped_nos)  # type: ignore[attr-defined]
    return sim


def extra_from_sim(sim: planning.Simulation) -> dict[str, Any]:
    return {
        "unplanned": sim.unplanned,
        "skipped": sim.skipped,
        "insert_notes": getattr(sim, "insert_notes", []) or [],
        "bumped_orders": getattr(sim, "bumped_orders", []) or [],
    }
