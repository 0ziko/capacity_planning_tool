"""Uretim partisi birlestirme oncesi/sonrasi termin ve yuk etki analizi."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models import Order
from app.schemas import AutoPlanRequest, MergeDelayRow, MergeImpactOut, MergeLoadDelta, MergePreviewGroup
from app.services import capacity as cap
from app.services import planning as plan
from app.services import production_batches as pbatches
from app.services.orders import effective_due, order_schedule


@dataclass
class VirtualBatchLink:
    order_id: int
    quantity: float
    order: Order


@dataclass
class VirtualBatch:
    id: int
    batch_no: str
    item_id: int
    item: object
    due_date: date
    quantity: float
    orders: list[VirtualBatchLink] = field(default_factory=list)


def _virtual_batches(db: Session, groups: list[list[int]]) -> list[VirtualBatch]:
    in_batch = pbatches.batched_order_ids(db)
    out: list[VirtualBatch] = []
    seq = 0
    for order_ids in groups:
        ids = list(dict.fromkeys(order_ids))
        if len(ids) < 2:
            continue
        orders = (
            db.query(Order)
            .options(joinedload(Order.item))
            .filter(Order.id.in_(ids), Order.status == "open")
            .all()
        )
        if len(orders) < 2:
            continue
        if len({o.item_id for o in orders}) != 1:
            continue
        if any(o.id in in_batch for o in orders):
            continue
        orders.sort(key=lambda o: (effective_due(o), o.order_no, o.position_no))
        item = orders[0].item
        due = min(effective_due(o) for o in orders)
        seq += 1
        links = [VirtualBatchLink(order_id=o.id, quantity=o.quantity, order=o) for o in orders]
        out.append(
            VirtualBatch(
                id=-seq,
                batch_no=f"ONIZLEME-{item.code}-{seq}",
                item_id=item.id,
                item=item,
                due_date=due,
                quantity=round(sum(o.quantity for o in orders), 3),
                orders=links,
            )
        )
    return out


def _load_from_sim(db: Session, wc_ids: list[int], start: date, end: date, sim_lines) -> dict[tuple[int, date], float]:
    load: dict[tuple[int, date], float] = defaultdict(float)
    for mode in ("manual", "forecast"):
        for k, v in plan.planned_hours_by_week(db, wc_ids, start, end, mode=mode).items():
            load[k] += v
    for ln in sim_lines:
        if start <= ln.week_start <= end:
            load[(ln.work_center_id, ln.week_start)] += ln.planned_hours
    return dict(load)


def _sim_batch_map(virtual: list[VirtualBatch]) -> dict[int, tuple[float, list[int]]]:
    return {b.id: (b.quantity, [link.order_id for link in b.orders]) for b in virtual}


def preview_merge_impact(db: Session, groups: list[MergePreviewGroup], req: AutoPlanRequest) -> MergeImpactOut:
    """Birlestirme + otomatik plan yenileme sonrasi termin kaymasi ve haftalik yuk farki."""
    virtual = _virtual_batches(db, [g.order_ids for g in groups])
    if not virtual:
        raise ValueError("Gecerli birlestirme grubu yok (en az iki acik siparis, ayni stok, partide olmama).")

    start = cap.week_start(req.start_week)
    weeks = [start + timedelta(weeks=i) for i in range(req.weeks)]
    wcs = plan._selected_work_centers(db, req.work_center_ids)
    wc_ids = [w.id for w in wcs]
    wc_code = {w.id: w.code for w in wcs}
    end = weeks[-1]

    sim_req = AutoPlanRequest(
        start_week=req.start_week,
        weeks=req.weeks,
        work_center_ids=req.work_center_ids,
        replace_existing=True,
        mode=req.mode,
    )
    sim_before = plan.simulate(db, sim_req)
    sim_after = plan.simulate(db, sim_req, extra_batches=virtual)
    batch_map = _sim_batch_map(virtual)

    before_load = _load_from_sim(db, wc_ids, start, end, sim_before.lines)
    after_load = _load_from_sim(db, wc_ids, start, end, sim_after.lines)
    before_sched = {s.order_id: s for s in order_schedule(db, req.work_center_ids, lines=sim_before.lines, orders=sim_before.orders)}
    after_sched = {
        s.order_id: s
        for s in order_schedule(db, req.work_center_ids, lines=sim_after.lines, orders=sim_after.orders, sim_batches=batch_map)
    }

    merged_ids = {link.order_id for b in virtual for link in b.orders}
    delays: list[MergeDelayRow] = []
    for oid in set(before_sched) | set(after_sched):
        after = after_sched.get(oid)
        before = before_sched.get(oid)
        if not after or not before:
            continue
        be, ae = before.planned_end, after.planned_end
        delay = (ae - be).days if be and ae else 0
        bl = before.lateness_days or 0
        al = after.lateness_days or 0
        lateness_increase = max(al - bl, 0)
        if delay > 0 or lateness_increase > 0:
            delays.append(
                MergeDelayRow(
                    order_id=oid,
                    order_no=after.order_no,
                    position_no=after.position_no,
                    customer=after.customer,
                    item_code=after.item_code,
                    due_date=after.due_date,
                    before_end=be,
                    after_end=ae,
                    delay_days=max(delay, lateness_increase),
                    before_lateness=bl if bl else None,
                    after_lateness=al if al else None,
                )
            )
    delays.sort(key=lambda r: (-r.delay_days, r.due_date, r.order_no))

    load_deltas: list[MergeLoadDelta] = []
    for key in set(before_load) | set(after_load):
        b = before_load.get(key, 0.0)
        a = after_load.get(key, 0.0)
        d = round(a - b, 2)
        if abs(d) < 0.05:
            continue
        wc_id, wk = key
        load_deltas.append(
            MergeLoadDelta(
                work_center_id=wc_id,
                work_center_code=wc_code.get(wc_id, "?"),
                week_start=wk,
                before_hours=round(b, 2),
                after_hours=round(a, 2),
                delta_hours=d,
            )
        )
    load_deltas.sort(key=lambda x: (-abs(x.delta_hours), x.work_center_code, x.week_start))

    batch_summaries = [
        {
            "batch_no": b.batch_no,
            "item_code": b.item.code if b.item else "",
            "quantity": b.quantity,
            "order_count": len(b.orders),
            "order_nos": [link.order.order_no for link in b.orders if link.order],
            "due_date": b.due_date.isoformat(),
        }
        for b in virtual
    ]

    return MergeImpactOut(
        merge_count=len(virtual),
        order_count=sum(len(b.orders) for b in virtual),
        delayed_count=len(delays),
        delayed_orders=delays,
        load_deltas=load_deltas,
        batches=batch_summaries,
        note="Otomatik plan yenileme oncesi/sonrasi karsilastirma (simule); manuel ve tahmin satirlari korunur.",
    )
