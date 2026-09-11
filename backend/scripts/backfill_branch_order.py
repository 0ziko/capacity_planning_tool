"""Mevcut FG BOM satirlarina branch_listing_sira degerini BOM.xlsx'ten yazar."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.migrate import ensure_columns
from app.db.session import SessionLocal, engine
from app.models import Item
from app.services.production_bom import _parse_fg_records, load_bom_grouped

BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")


def main() -> None:
    ensure_columns(engine)
    by_fg, header = load_bom_grouped(path=BOM_PATH)
    db = SessionLocal()
    updated = 0
    try:
        for fg, recs in by_fg.items():
            parsed = _parse_fg_records(fg, recs, header)
            wip_sira: dict[str, int] = {}
            for branch in parsed.branches:
                if branch.wip == parsed.finish_wip:
                    continue
                max_sira = max((o.listing_sira for o in branch.ops), default=0)
                wip_sira[branch.wip] = max(wip_sira.get(branch.wip, 0), max_sira)
            item = db.query(Item).filter(Item.code == fg).first()
            if not item:
                continue
            for bl in item.bom_lines:
                sira = wip_sira.get(bl.component_code)
                if sira is not None and bl.branch_listing_sira != sira:
                    bl.branch_listing_sira = sira
                    updated += 1
        db.commit()
        print(f"Guncellenen BOM satiri: {updated}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
