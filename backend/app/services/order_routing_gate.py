"""Sipariş giriş/aktarım kapısı: bitmiş ürünün rota tanımı eksikse sipariş kabul edilmez.

Karar, plan ön kontrolünün "rotasız" kuralıyla BİREBİR aynı fonksiyondan üretilir
(plan_preflight._items_without_routing): kendi rotası olmayan ürün, kartı/rotası eksik yarımamül dalı,
ölçülmemiş dizilim aralığı, uygun aktif hat istasyonu olmayan dizilim operasyonu. Böylece plana
giremeyecek sipariş daha girişte yakalanır; kapıdan önce girilmiş (eski) siparişler etkilenmez.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import Item, RoutingOperation, WorkCenter
from app.services.bom_tree import fg_has_wip_structure, is_wip_asm_link


def routing_gaps_bulk(db: Session, items: list[Item]) -> dict[int, str | None]:
    """Toplu kapı: birçok ürün için rota eksiği açıklamaları (item_id -> açıklama | None). Tek kontrol, tek sorgu seti;
    sonuç routing_gap ile birebir aynı kuraldan (plan_preflight._items_without_routing) üretilir."""
    from app.services.plan_preflight import _items_without_routing

    ids = sorted({i.id for i in items})
    if not ids:
        return {}
    loaded = (db.query(Item)
              .options(selectinload(Item.operations).selectinload(RoutingOperation.work_center).selectinload(WorkCenter.machines), selectinload(Item.bom_lines))
              .filter(Item.id.in_(ids)).all())
    codes: set[str] = set()
    for it in loaded:
        if fg_has_wip_structure(it):
            codes |= {(bl.component_code or "").strip().upper() for bl in (it.bom_lines or [])
                      if is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))}
    wip_cache: dict[str, Item] = {}
    if codes:
        rows = db.query(Item).options(selectinload(Item.operations).selectinload(RoutingOperation.work_center).selectinload(WorkCenter.machines)).filter(func.upper(Item.code).in_(codes)).all()
        wip_cache = {w.code.upper(): w for w in rows}
    stubs = [SimpleNamespace(item=it, order_no=it.code) for it in loaded]
    gaps = {r.item_code.upper(): r for r in _items_without_routing(db, (stubs, [], wip_cache))}
    out: dict[int, str | None] = {}
    for it in loaded:
        parts = []
        own = gaps.get(it.code.upper())
        if own is not None:
            name = (own.item_name or "").strip()
            parts.append(f"{it.code}: {name.split(' — ', 1)[1] if ' — ' in name else 'rota tanımı yok'}")
        if fg_has_wip_structure(it):
            for bl in (it.bom_lines or []):
                if not is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0)):
                    continue
                code = (bl.component_code or "").strip().upper()
                r = gaps.get(code)
                if r is not None:
                    name = (r.item_name or "").strip()
                    parts.append(f"{it.code} yarımamülü {r.item_code}: {name.split(' — ', 1)[1] if ' — ' in name else 'rota tanımı yok'}")
        out[it.id] = "; ".join(parts) if parts else None
    return out


def routing_gap(db: Session, item: Item) -> str | None:
    """Rota eksikse kullanıcıya gösterilecek açıklama, tamsa None."""
    from app.services.plan_preflight import _items_without_routing

    wip_cache: dict[str, Item] = {}
    if fg_has_wip_structure(item):
        codes = {
            (bl.component_code or "").strip().upper()
            for bl in (item.bom_lines or [])
            if is_wip_asm_link(bl.component_code or "", bl.source_wip or "", getattr(bl, "recipe_seq", 0))
        }
        if codes:
            rows = db.query(Item).options(selectinload(Item.operations)).filter(func.upper(Item.code).in_(codes)).all()
            wip_cache = {w.code.upper(): w for w in rows}
    stub = SimpleNamespace(item=item, order_no="")
    rows = _items_without_routing(db, ([stub], [], wip_cache))
    if not rows:
        return None
    parts = []
    for r in rows:
        name = (r.item_name or "").strip()
        if r.item_code == item.code:
            parts.append(f"{item.code}: {name.split(' — ', 1)[1] if ' — ' in name else 'rota tanımı yok'}")
        else:
            reason = name.split(" — ", 1)[1] if " — " in name else "rota tanımı yok"
            parts.append(f"{item.code} yarımamülü {r.item_code}: {reason}")
    return "; ".join(parts)


def assert_orderable(db: Session, item: Item) -> None:
    gap = routing_gap(db, item)
    if gap:
        raise ValueError(f"Rota tanımı eksik — sipariş kabul edilmedi: {gap}. Önce Stok/BOM/Rota ekranından rotayı tanımlayın.")
