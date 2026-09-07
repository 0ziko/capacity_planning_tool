"""Yarimamul kodu (operasyon cikisi) cozumleme ve indeks."""

from sqlalchemy.orm import Session, joinedload

from app.models import RoutingOperation, norm_wip


def wip_index(db: Session) -> dict[str, list[RoutingOperation]]:
    idx: dict[str, list[RoutingOperation]] = {}
    rows = (
        db.query(RoutingOperation)
        .options(joinedload(RoutingOperation.item), joinedload(RoutingOperation.work_center))
        .filter(RoutingOperation.semi_finished_code != "")
        .all()
    )
    for op in rows:
        k = norm_wip(op.semi_finished_code)
        if k:
            idx.setdefault(k, []).append(op)
    return idx


def resolve_wip(db: Session, wip_code: str, item_code: str | None = None, index: dict[str, list[RoutingOperation]] | None = None) -> RoutingOperation:
    k = norm_wip(wip_code)
    if not k:
        raise ValueError("Yarimamul kodu bos")
    idx = index if index is not None else wip_index(db)
    ops = idx.get(k, [])
    if not ops:
        raise ValueError(f"Yarimamul kodu bulunamadi: {wip_code}")
    if item_code:
        hint = item_code.strip().upper()
        op = next((o for o in ops if o.item.code.upper() == hint), None)
        if not op:
            raise ValueError(f"Yarimamul kodu {wip_code} bu stok kodu ile eslesmiyor: {item_code}")
        return op
    if len(ops) > 1:
        codes = ", ".join(sorted({o.item.code for o in ops}))
        raise ValueError(f"Yarimamul kodu birden fazla rotada: {wip_code} ({codes}) — stok kodu belirtin")
    return ops[0]
