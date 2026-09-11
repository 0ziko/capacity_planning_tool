"""SQLite ve PostgreSQL veri karsilastirmasi."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text


def counts(url: str) -> dict[str, int]:
    eng = create_engine(url)
    insp = inspect(eng)
    out: dict[str, int] = {}
    with eng.connect() as conn:
        for name in sorted(insp.get_table_names()):
            out[name] = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="sqlite:///./kapasite_dev.db")
    p.add_argument("--target", default="postgresql+psycopg://kapasite:kapasite@localhost:5432/kapasite")
    args = p.parse_args()

    src = counts(args.source)
    dst = counts(args.target)
    all_tables = sorted(set(src) | set(dst))
    ok = True
    print(f"{'Tablo':<30} {'SQLite':>10} {'PostgreSQL':>12} {'Durum'}")
    print("-" * 68)
    for t in all_tables:
        s, d = src.get(t, 0), dst.get(t, 0)
        status = "OK" if s == d else "FARK"
        if s != d:
            ok = False
        if s or d:
            print(f"{t:<30} {s:>10} {d:>12}  {status}")
    print("-" * 68)
    print(f"Toplam satir: SQLite={sum(src.values())}  PostgreSQL={sum(dst.values())}")
    if ok:
        print("Tum tablolar eslesiyor.")
    else:
        print("UYARI: Bazi tablolarda fark var.")
        sys.exit(1)


if __name__ == "__main__":
    main()
