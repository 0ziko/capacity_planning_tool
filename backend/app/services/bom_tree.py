"""Siparis BOM patlatma: yari mamul isleri + bitis rotasi + mamul rota birlestirme."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session, joinedload

from app.models import Item, Order, RoutingOperation, RoutingOperationStation


def is_wip(code: str) -> bool:
    return bool(re.match(r"^5\d{5,}$", code or ""))


def is_wip_output(code: str) -> bool:
    """Montaja giden yari mamul ciktisi (suffix'li olabilir, orn. 5600606-77)."""
    return bool(re.match(r"^5\d{5,}(-\d+)?$", code or ""))


def is_wip_step(code: str) -> bool:
    return bool(re.match(r"^5\d{5,}-", code or ""))


def is_raw_material(code: str) -> bool:
    return bool(re.match(r"^[1-4]", code or ""))


def bom_line_sort_key(bl) -> tuple:
    """Operasyon tablosu ile ayni dal ve uretim sirasi."""
    seq = getattr(bl, "recipe_seq", None)
    if seq is None:
        seq = 99999
    return (
        -(getattr(bl, "branch_listing_sira", 0) or 0),
        seq,
        getattr(bl, "component_code", "") or "",
    )


def sort_bom_lines_for_display(bom_lines: list) -> list:
    return sorted(bom_lines, key=bom_line_sort_key)


def is_wip_asm_link(component_code: str, source_wip: str, recipe_seq: int | None = None) -> bool:
    """Montaj baglantisi: code==source ve recipe_seq=0. Son operasyon adimi (seq>0) asm degildir."""
    code = (component_code or "").strip()
    src = (source_wip or "").strip()
    seq = 0 if recipe_seq is None else int(recipe_seq)
    return bool(is_wip_output(code) and code == src and seq == 0)


def is_fg(code: str) -> bool:
    return bool(re.match(r"^6\d{5,}$", code or ""))


def _is_wip(code: str) -> bool:
    return is_wip(code)


@dataclass
class FlatOperation:
    """Mamul ekraninda gosterilecek birlestirilmis operasyon satiri."""

    operation: RoutingOperation
    display_seq: int
    wip_code: str = ""


def _load_wip_items(db: Session, codes: list[str]) -> dict[str, Item]:
    if not codes:
        return {}
    items = (
        db.query(Item)
        .options(
            joinedload(Item.operations).joinedload(RoutingOperation.work_center),
            joinedload(Item.operations).joinedload(RoutingOperation.primary_machine),
            joinedload(Item.operations).joinedload(RoutingOperation.alt_stations).joinedload(RoutingOperationStation.machine),
        )
        .filter(Item.code.in_(codes))
        .all()
    )
    return {i.code.upper(): i for i in items}


def flatten_fg_operations(db: Session, fg: Item) -> list[FlatOperation]:
    """6xxxxx mamul icin yari mamul + bitis operasyonlarini uretim sirasinda birlestirir."""
    own_ops = sorted(fg.operations or [], key=lambda o: o.seq)
    if not is_fg(fg.code) or not any(
        is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))
        for bl in (fg.bom_lines or [])
    ):
        return [FlatOperation(op, op.seq, "") for op in own_ops]

    wip_codes = list(
        dict.fromkeys(
            (bl.component_code or "").strip()
            for bl in fg.bom_lines
            if is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))
        )
    )
    wip_by_code = _load_wip_items(db, wip_codes)

    wip_lines = [
        bl
        for bl in (fg.bom_lines or [])
        if is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))
    ]
    wip_lines.sort(key=lambda bl: bl.branch_listing_sira or 0, reverse=True)

    result: list[FlatOperation] = []
    seq = 10
    for bl in wip_lines:
        code = (bl.component_code or "").strip()
        wip_item = wip_by_code.get(code.upper())
        if not wip_item or not wip_item.operations:
            continue
        repeat = max(1, int(round(float(bl.quantity or 1))))
        for _ in range(repeat):
            for op in sorted(wip_item.operations, key=lambda o: o.seq):
                result.append(FlatOperation(op, seq, code))
                seq += 10

    for op in own_ops:
        result.append(FlatOperation(op, seq, ""))
        seq += 10
    return result


def fg_has_wip_structure(item: Item) -> bool:
    return is_fg(item.code) and any(
        is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))
        for bl in (item.bom_lines or [])
    )


@dataclass
class WipJob:
    item: Item
    quantity: float
    semi_finished_code: str
    label_suffix: str = ""


@dataclass
class OrderPlanJobs:
    wip_jobs: list[WipJob]
    finish_job: WipJob | None


def explode_order(db: Session, order: Order) -> OrderPlanJobs:
    """FG siparisini yari mamul islerine ve bitis rotasina ayirir."""
    fg = order.item
    if fg is None:
        fg = db.query(Item).options(joinedload(Item.bom_lines), joinedload(Item.operations)).filter(Item.id == order.item_id).first()
    if not fg:
        return OrderPlanJobs([], None)

    wip_jobs: list[WipJob] = []
    seen: set[str] = set()
    for bl in fg.bom_lines or []:
        code = (bl.component_code or "").strip()
        if not is_wip_asm_link(code, bl.source_wip or "", getattr(bl, "recipe_seq", 0)):
            continue
        if code in seen:
            continue
        seen.add(code)
        wip_item = db.query(Item).options(joinedload(Item.operations)).filter(Item.code.ilike(code)).first()
        if not wip_item or not wip_item.operations:
            continue
        qty = order.quantity * (bl.quantity or 1.0)
        wip_jobs.append(WipJob(item=wip_item, quantity=qty, semi_finished_code=code, label_suffix=code))

    finish = None
    if fg.operations:
        finish = WipJob(item=fg, quantity=order.quantity, semi_finished_code="", label_suffix="bitis")

    return OrderPlanJobs(wip_jobs=wip_jobs, finish_job=finish)


def has_wip_structure(order: Order) -> bool:
    fg = order.item
    if not fg:
        return False
    return fg_has_wip_structure(fg) and bool(fg.operations)
