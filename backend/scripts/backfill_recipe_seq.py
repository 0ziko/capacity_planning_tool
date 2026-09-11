"""Mevcut BOM satirlarina recipe_seq ve branch_listing_sira yazar (operasyon sirasi ile uyum)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.migrate import ensure_columns
from app.db.session import SessionLocal, engine
from app.models import BomLine, Item
from app.services.production_bom import _branch_listing_sira, _parse_fg_records, load_bom_grouped

BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")


def _merge(
    out: dict[tuple[str, str], tuple[int, int]],
    key: tuple[str, str],
    branch_sira: int,
    recipe_seq: int,
) -> None:
    prev = out.get(key)
    if prev:
        out[key] = (branch_sira, min(prev[1], recipe_seq))
    else:
        out[key] = (branch_sira, recipe_seq)


def maps_from_parsed(parsed) -> tuple[dict[tuple[str, str], tuple[int, int]], dict[str, dict[tuple[str, str], tuple[int, int]]]]:
    """FG BOM haritasi + WIP kodu -> (comp, src) -> siralama."""
    fg_map: dict[tuple[str, str], tuple[int, int]] = {}
    wip_maps: dict[str, dict[tuple[str, str], tuple[int, int]]] = {}

    for branch in parsed.branches:
        src = branch.wip
        branch_sira = _branch_listing_sira(branch)
        wip_map = wip_maps.setdefault(src.upper(), {})

        for mfg_idx, op in enumerate(branch.ops):
            step_seq = (mfg_idx + 1) * 10
            mat_seq = step_seq + 1
            if op.wip_op_code:
                k = (op.wip_op_code.upper(), src)
                _merge(fg_map, k, branch_sira, step_seq)
            for m in op.materials:
                if not m.code:
                    continue
                k = (m.code.upper(), src)
                _merge(fg_map, k, branch_sira, mat_seq)
                _merge(wip_map, k, branch_sira, mat_seq)

        if branch.wip != parsed.finish_wip:
            _merge(fg_map, (src.upper(), src), branch_sira, 0)

    return fg_map, wip_maps


def main() -> None:
    if not BOM_PATH.is_file():
        raise SystemExit(f"BOM yok: {BOM_PATH}")

    ensure_columns(engine)
    by_fg, header = load_bom_grouped(path=BOM_PATH)
    db = SessionLocal()
    t0 = time.time()
    fg_updated = wip_updated = 0

    try:
        fg_items = {i.code: i for i in db.query(Item).filter(Item.code.in_(list(by_fg.keys()))).all()}
        wip_items = {i.code.upper(): i for i in db.query(Item).filter(Item.code.like("5%")).all()}

        for fg_code in sorted(by_fg.keys()):
            parsed = _parse_fg_records(fg_code, by_fg[fg_code], header)
            if not parsed.branches:
                continue
            fg_map, wip_maps = maps_from_parsed(parsed)
            item = fg_items.get(fg_code)
            if item:
                for bl in item.bom_lines:
                    key = ((bl.component_code or "").upper(), bl.source_wip or "")
                    hit = fg_map.get(key)
                    if hit:
                        bs, rs = hit
                        if bl.branch_listing_sira != bs or bl.recipe_seq != rs:
                            bl.branch_listing_sira = bs
                            bl.recipe_seq = rs
                            fg_updated += 1

            for wip_code, wmap in wip_maps.items():
                wit = wip_items.get(wip_code)
                if not wit:
                    continue
                for bl in wit.bom_lines:
                    key = ((bl.component_code or "").upper(), bl.source_wip or "")
                    hit = wmap.get(key)
                    if hit:
                        bs, rs = hit
                        if bl.branch_listing_sira != bs or bl.recipe_seq != rs:
                            bl.branch_listing_sira = bs
                            bl.recipe_seq = rs
                            wip_updated += 1

        db.commit()
        print(f"FG BOM satir guncellendi: {fg_updated}")
        print(f"WIP BOM satir guncellendi: {wip_updated}")
        print(f"Sure: {time.time() - t0:.0f} sn")
    finally:
        db.close()


if __name__ == "__main__":
    main()
