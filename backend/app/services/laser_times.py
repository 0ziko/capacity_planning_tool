"""Lazer sure standardi: yari mamul lazer operasyonlarina olculmus/tahmini CT ve setup yazar.

ERP BOM aktarimi SURE'yi bom_cycle_factor (1.6) ile carpar ve setup'i 0 yazar. Lazer icin referans
olcum tablosu esas alindigindan bu operasyonlarda standart degerler carpansiz kullanilir. Degerler
operasyon kodu (semi_finished_code, orn. 5909828-12) ile eslesir; ayni yari mamulu kullanan tum
bitmis urunlerde ayni sure gecerlidir. Setup, planlamada her is (siparis x yari mamul) icin bir kez
yuke eklenir (routing_resource.legacy_hours_for).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import LaserTimeStandard, RoutingOperation

LASER_WC_KEY = "LAZER"


def standards_by_code(db: Session) -> dict[str, LaserTimeStandard]:
    return {s.semi_finished_code.upper(): s for s in db.query(LaserTimeStandard).all()}


def apply_to_operation(op: RoutingOperation, std: LaserTimeStandard) -> None:
    op.cycle_time_sec = float(std.cycle_time_sec)
    op.setup_time_min = float(std.setup_time_min or 0.0)


def apply_standards(db: Session, ops: list[RoutingOperation], standards: dict[str, LaserTimeStandard] | None = None) -> int:
    """Eslesen operasyonlara standardi yazar; yazilan operasyon sayisini dondurur."""
    standards = standards_by_code(db) if standards is None else standards
    n = 0
    for op in ops:
        std = standards.get((op.semi_finished_code or "").upper())
        if std is not None:
            apply_to_operation(op, std)
            n += 1
    return n


def _f(v) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def import_laser_times(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    """Standart tabloyu gunceller ve programdaki eslesen lazer operasyonlarina uygular."""
    ins = upd = 0
    errs: list[str] = []
    existing = standards_by_code(db)
    touched: dict[str, LaserTimeStandard] = {}
    for r in rows:
        code = str(r.get("semi_finished_code") or "").strip()
        ct = _f(r.get("cycle_time_sec"))
        setup = _f(r.get("setup_time_min"))
        if not code:
            errs.append(f"Satir {r['_row']}: yari mamul operasyon kodu bos")
            continue
        if ct is None or ct <= 0:
            errs.append(f"Satir {r['_row']}: {code} cevrim suresi gecersiz ({r.get('cycle_time_sec')})")
            continue
        if setup is not None and setup < 0:
            errs.append(f"Satir {r['_row']}: {code} setup negatif olamaz")
            continue
        std = existing.get(code.upper())
        if std is None:
            std = LaserTimeStandard(semi_finished_code=code)
            db.add(std)
            existing[code.upper()] = std
            ins += 1
        else:
            upd += 1
        std.cycle_time_sec = ct
        std.setup_time_min = setup or 0.0
        std.note = str(r.get("note") or "")[:256]
        touched[code.upper()] = std
    db.flush()

    ops = (
        db.query(RoutingOperation)
        .filter(RoutingOperation.semi_finished_code.in_([s.semi_finished_code for s in touched.values()]))
        .all()
        if touched
        else []
    )
    apply_standards(db, ops, touched)
    found = {(op.semi_finished_code or "").upper() for op in ops}
    non_laser = sorted({op.semi_finished_code for op in ops if LASER_WC_KEY not in ((op.work_center.name if op.work_center else "") or "").upper()})
    missing = sorted(s.semi_finished_code for k, s in touched.items() if k not in found)
    if non_laser:
        errs.append(f"Uyari: {len(non_laser)} kod lazer disi bir is merkezindeki operasyona yazildi: {', '.join(non_laser[:20])}")
    if missing:
        errs.append(
            f"Bilgi: {len(missing)} kod programdaki rotalarda henuz yok; standart kaydedildi, ERP receteleri yuklendiginde otomatik uygulanir: "
            + ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")
        )
    return ins, upd, errs
