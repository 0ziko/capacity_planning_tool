"""Yedek restore sonrasi kaynak/hedef karsilastirma."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.data_integrity import critical_table_counts


def compare_critical_counts(source: Session, target: Session) -> dict:
    a = critical_table_counts(source)
    b = critical_table_counts(target)
    keys = sorted(set(a) | set(b))
    rows = []
    ok = True
    for k in keys:
        sa, tb = a.get(k, 0), b.get(k, 0)
        match = sa == tb
        if not match:
            ok = False
        rows.append({"table": k, "source": sa, "target": tb, "match": match})
    return {"ok": ok, "tables": rows}
