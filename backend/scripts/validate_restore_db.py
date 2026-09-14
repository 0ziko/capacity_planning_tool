"""Restore sonrasi kritik tablo sayilari ve butunluk denetimi."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.migrate import ensure_columns, repair_bom_constraints, repair_orphans
from app.services.data_integrity import audit_data, critical_table_counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="PostgreSQL baglanti URL (sifre ortamdan)")
    p.add_argument("--expected-counts", help="JSON dosyasi: kaynak kritik tablo sayilari")
    args = p.parse_args()

    engine = create_engine(args.url)
    ensure_columns(engine)
    repair_bom_constraints(engine)
    repair_orphans(engine)
    with Session(engine) as db:
        counts = critical_table_counts(db)
        audit = audit_data(db)
    out = {"counts": counts, "audit": audit}
    if args.expected_counts:
        exp = json.loads(Path(args.expected_counts).read_text(encoding="utf-8"))
        mism = {k: {"expected": exp.get(k), "actual": counts.get(k)} for k in counts if exp.get(k) != counts.get(k)}
        out["count_match"] = len(mism) == 0
        out["mismatches"] = mism
        if mism:
            print(json.dumps(out, indent=2, default=str))
            return 1
    print(json.dumps(out, indent=2, default=str))
    return 0 if audit["error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
