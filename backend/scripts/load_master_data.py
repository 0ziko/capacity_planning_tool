"""Masaustu Excel dosyalarindan istasyon + production BOM toplu yukleme."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.migrate import ensure_columns, repair_bom_constraints
from app.db.session import Base, SessionLocal, engine
from app.models import ImportLog, Item, Machine, WorkCenter
from app.services.production_bom import _parse_fg_records, import_parsed_fg, load_bom_grouped
from app.services.stations import ensure_extra_work_centers, import_stations, load_stations_xlsx, wc_lookup_by_name

STATIONS_PATH = Path(r"C:\Users\ozan.deniz\Desktop\İstasyonlar.xlsx")
BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")
BATCH = 50


def _refresh_cache(db) -> dict[str, Item]:
    return {i.code.upper(): i for i in db.query(Item).all()}


def main() -> None:
    if not STATIONS_PATH.is_file():
        raise SystemExit(f"Istasyon dosyasi yok: {STATIONS_PATH}")
    if not BOM_PATH.is_file():
        raise SystemExit(f"BOM dosyasi yok: {BOM_PATH}")

    Base.metadata.create_all(bind=engine)
    ensure_columns(engine)
    repair_bom_constraints(engine)

    db = SessionLocal()
    t0 = time.time()
    try:
        print("=== Istasyonlar import ===")
        rows = load_stations_xlsx(STATIONS_PATH)
        ins, upd, deact, errs = import_stations(db, rows, replace_missing=True)
        db.add(
            ImportLog(
                kind="istasyonlar",
                filename=STATIONS_PATH.name,
                username="system",
                inserted=ins,
                updated=upd + deact,
                errors="\n".join(errs[:500]),
            )
        )
        db.commit()
        print(f"  {ins} yeni, {upd} guncelleme, {deact} pasif, {len(errs)} hata")
        print(f"  Toplam IM: {db.query(WorkCenter).count()}, aktif istasyon: {db.query(Machine).filter(Machine.is_active.is_(True)).count()}")

        print("\n=== BOM okuma (gruplama) ===")
        by_fg, header = load_bom_grouped(path=BOM_PATH)
        fg_list = sorted(by_fg.keys())
        print(f"  {len(fg_list)} mamul, {sum(len(v) for v in by_fg.values())} satir")

        print("\n=== Production BOM import ===")
        wc_idx = wc_lookup_by_name(db)
        ensure_extra_work_centers(db, wc_idx)
        machines = {m.code.upper(): m for m in db.query(Machine).filter(Machine.is_active.is_(True)).all()}
        cache = _refresh_cache(db)
        counters = {"created": 0, "updated": 0, "routes": 0, "bom": 0}
        warnings: list[str] = []
        errors: list[str] = []
        total_fg = 0

        for i in range(0, len(fg_list), BATCH):
            batch_fg = 0
            for fg in fg_list[i : i + BATCH]:
                try:
                    with db.begin_nested():
                        parsed = _parse_fg_records(fg, by_fg[fg], header)
                        if not parsed.branches:
                            warnings.append(f"{fg}: dal/operasyon yok")
                            continue
                        import_parsed_fg(
                            db, parsed, cache=cache, wc_idx=wc_idx, machines=machines,
                            counters=counters, warnings=warnings,
                        )
                    batch_fg += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{fg}: {e}")
            try:
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                errors.append(f"Parti {i // BATCH + 1} commit: {e}")
                cache = _refresh_cache(db)
                print(f"  Parti {i // BATCH + 1} COMMIT HATA — rollback")
                continue
            cache = _refresh_cache(db)
            total_fg += batch_fg
            db.add(
                ImportLog(
                    kind="production_bom",
                    filename=f"{BOM_PATH.name} [{i + 1}-{min(i + BATCH, len(fg_list))}]",
                    username="system",
                    inserted=batch_fg,
                    updated=sum(counters.values()),
                    errors="\n".join((errors[-3:] + warnings[-3:]))[:5000],
                )
            )
            db.commit()
            print(f"  Parti {i // BATCH + 1}: +{batch_fg} ({total_fg}/{len(fg_list)}) — {time.time() - t0:.0f}s")

        print("\n=== Ozet ===")
        print(f"  Mamul: {total_fg}/{len(fg_list)}")
        print(f"  Stok karti: {db.query(Item).count()}")
        print(f"  Yeni kart: {counters['created']}, rota op: {counters['routes']}, BOM satir: {counters['bom']}")
        print(f"  Hata: {len(errors)}, uyari: {len(warnings)}")
        if errors:
            for e in errors[:8]:
                print(f"    {e}")
        print(f"  Sure: {time.time() - t0:.0f} sn")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
