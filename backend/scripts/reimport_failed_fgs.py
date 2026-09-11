"""UniqueViolation ile basarisiz kalan mamulleri yeniden import eder."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models import Item
from app.services.production_bom import _parse_fg_records, import_parsed_fg, load_bom_grouped
from app.services.stations import ensure_extra_work_centers, wc_lookup_by_name
from app.models import Machine

BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")
FAILED = ("6007517", "6007518", "6007619", "6012095", "6012099", "6012294")


def main() -> None:
    by_fg, header = load_bom_grouped(path=BOM_PATH)
    db = SessionLocal()
    wc_idx = wc_lookup_by_name(db)
    ensure_extra_work_centers(db, wc_idx)
    machines = {m.code.upper(): m for m in db.query(Machine).filter(Machine.is_active.is_(True)).all()}
    cache = {i.code.upper(): i for i in db.query(Item).all()}
    counters = {"created": 0, "updated": 0, "routes": 0, "bom": 0}
    warnings: list[str] = []
    ok = 0
    for fg in FAILED:
        if fg not in by_fg:
            print(f"{fg}: Excel'de yok")
            continue
        try:
            parsed = _parse_fg_records(fg, by_fg[fg], header)
            if not parsed.branches:
                print(f"{fg}: dal yok")
                continue
            import_parsed_fg(db, parsed, cache=cache, wc_idx=wc_idx, machines=machines, counters=counters, warnings=warnings)
            db.commit()
            item = db.query(Item).filter(Item.code == fg).first()
            n = len(item.bom_lines) if item else 0
            ops = len(item.operations) if item else 0
            print(f"{fg}: OK — {n} BOM satir, {ops} op")
            ok += 1
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"{fg}: HATA — {e}")
    print(f"\nTamamlanan: {ok}/{len(FAILED)}")
    db.close()


if __name__ == "__main__":
    main()
