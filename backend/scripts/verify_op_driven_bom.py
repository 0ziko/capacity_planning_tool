"""6005510 / 6000006: operasyon sirasina gore malzeme parent dogrulama."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models import Item
from app.services.bom_tree import flatten_fg_operations, is_wip_asm_link, is_wip_step


def unique_ops_by_semi(ops):
    out = []
    seen = set()
    for op in sorted(ops, key=lambda o: o.seq):
        sf = (op.semi_finished_code or "").strip()
        if not sf or sf in seen:
            continue
        seen.add(sf)
        out.append(op)
    return out


def source_key(op, all_ops):
    wip = (op.wip_code or "").strip()
    if wip:
        return wip
    finish = [o for o in all_ops if not (o.wip_code or "").strip()]
    uniq = unique_ops_by_semi(finish or all_ops)
    return (uniq[-1].semi_finished_code if uniq else op.semi_finished_code) or ""


def parent_of_material(fg_code: str, mat_code: str):
    db = SessionLocal()
    try:
        item = db.query(Item).filter(Item.code == fg_code).first()
        flat = flatten_fg_operations(db, item)
        ops = []
        for f in flat:
            ops.append(
                type(
                    "Op",
                    (),
                    {
                        "seq": f.display_seq,
                        "semi_finished_code": f.operation.semi_finished_code or "",
                        "wip_code": f.wip_code or "",
                        "name": f.operation.operation_name,
                    },
                )()
            )
        lines = [
            bl
            for bl in item.bom_lines
            if not is_wip_asm_link(bl.component_code, bl.source_wip or "", bl.recipe_seq)
            and not is_wip_step(bl.component_code)
        ]
        by_src = defaultdict(list)
        for bl in lines:
            by_src[bl.source_wip or ""].append(bl)

        found = []
        attached = set()
        for source, bls in by_src.items():
            group = [o for o in ops if source_key(o, ops) == source]
            uniq = unique_ops_by_semi(group)
            for i, op in enumerate(uniq):
                step_seq = (i + 1) * 10
                next_seq = (i + 2) * 10 if i + 1 < len(uniq) else None
                key = f"{source}::{op.semi_finished_code}"
                if key in attached:
                    continue
                attached.add(key)
                for bl in bls:
                    rs = bl.recipe_seq if bl.recipe_seq is not None else 99999
                    if rs <= step_seq:
                        continue
                    if next_seq is not None and rs >= next_seq:
                        continue
                    if bl.component_code == mat_code:
                        found.append((op.semi_finished_code, op.name, bl.quantity, source, rs))
        return found
    finally:
        db.close()


def main():
    print("6000006 3000261 ->", parent_of_material("6000006", "3000261"))
    print("6000006 2000067 ->", parent_of_material("6000006", "2000067"))
    print("6005510 3000205 ->", parent_of_material("6005510", "3000205"))
    print("6005510 1001822 ->", parent_of_material("6005510", "1001822"))

    koli = parent_of_material("6005510", "3000205")
    pack = parent_of_material("6000006", "3000261")
    ok = (
        koli
        and koli[0][0] == "5005510-50"
        and pack
        and pack[0][0] == "5000006-50"
    )
    print("SONUC:", "OK" if ok else "HATA")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
