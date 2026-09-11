"""SQLite gelistirme veritabanini PostgreSQL'e tasir.

Ornek:
  python scripts/migrate_sqlite_to_postgres.py
  python scripts/migrate_sqlite_to_postgres.py --source sqlite:///./kapasite_dev.db --target postgresql+psycopg://kapasite:kapasite@localhost:5432/kapasite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text

from app.db.migrate import ensure_columns, repair_orphans
from app.db.session import Base
from app.models import (  # noqa: F401 — modelleri yukle
    BomLine,
    Downtime,
    Employee,
    ImportLog,
    Item,
    Machine,
    OpTransitionRule,
    Order,
    PlanLine,
    ProductionActual,
    ProductionBatch,
    ProductionBatchOrder,
    Reservation,
    RoutingOperation,
    Shipment,
    StockReceipt,
    User,
    WorkCenter,
    WorkCenterShift,
    WorkCenterWeek,
)


def _reset_sequences(conn, table_names: list[str]) -> None:
    for name in table_names:
        seq = conn.execute(text(f"SELECT pg_get_serial_sequence('{name}', 'id')")).scalar()
        if not seq:
            continue
        conn.execute(
            text(f"SELECT setval('{seq}', GREATEST(COALESCE((SELECT MAX(id) FROM {name}), 1), 1), true)")
        )


def migrate(source_url: str, target_url: str, *, replace: bool = True) -> dict[str, int]:
    if not source_url.startswith("sqlite"):
        raise ValueError("Kaynak yalnizca SQLite olabilir")
    if not target_url.startswith("postgresql"):
        raise ValueError("Hedef yalnizca PostgreSQL olabilir")

    src_eng = create_engine(source_url)
    dst_eng = create_engine(target_url)

    if not inspect(src_eng).get_table_names():
        raise SystemExit("Kaynak SQLite veritabani bos veya bulunamadi.")

    Base.metadata.create_all(dst_eng)
    ensure_columns(dst_eng)

    counts: dict[str, int] = {}
    dst_insp = inspect(dst_eng)

    with dst_eng.begin() as dst:
        if replace:
            for table in reversed(Base.metadata.sorted_tables):
                if dst_insp.has_table(table.name):
                    dst.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))

    with src_eng.connect() as src, dst_eng.begin() as dst:
        for table in Base.metadata.sorted_tables:
            if not dst_insp.has_table(table.name):
                continue
            rows = src.execute(table.select()).mappings().all()
            if not rows:
                continue
            dst.execute(table.insert(), [dict(r) for r in rows])
            counts[table.name] = len(rows)

    if counts:
        with dst_eng.begin() as dst:
            _reset_sequences(dst, list(counts.keys()))

    repair_orphans(dst_eng)
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description="SQLite -> PostgreSQL veri tasima")
    p.add_argument("--source", default="sqlite:///./kapasite_dev.db", help="Kaynak SQLite URL")
    p.add_argument(
        "--target",
        default="postgresql+psycopg://kapasite:kapasite@localhost:5432/kapasite",
        help="Hedef PostgreSQL URL",
    )
    p.add_argument("--keep-existing", action="store_true", help="Hedefteki mevcut veriyi silme (varsayilan: sil ve kopyala)")
    args = p.parse_args()

    print(f"Kaynak: {args.source}")
    print(f"Hedef:  {args.target}")
    counts = migrate(args.source, args.target, replace=not args.keep_existing)
    total = sum(counts.values())
    for name, n in sorted(counts.items()):
        print(f"  {name}: {n} satir")
    print(f"Tamam — {total} satir tasindi.")


if __name__ == "__main__":
    main()
