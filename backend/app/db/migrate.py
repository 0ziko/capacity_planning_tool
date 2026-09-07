"""Alembic oncesi hafif sema guncellemesi: mevcut tablolara eksik kolonlari ekler.

create_all yalnizca eksik tablolari olusturur; var olan tabloya yeni kolon eklemez.
Bu yardimci, model tanimindaki kolonlar veritabaninda yoksa ALTER TABLE ile ekler
(yalnizca nullable veya sabit varsayilanli kolonlar icin guvenlidir).
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.session import Base


def _default_sql(col) -> str:
    d = col.default
    if d is None or not getattr(d, "is_scalar", False):
        return ""
    v = d.arg
    if isinstance(v, bool):
        return f" DEFAULT {1 if v else 0}"
    if isinstance(v, (int, float)):
        return f" DEFAULT {v}"
    if isinstance(v, str):
        return " DEFAULT '" + v.replace("'", "''") + "'"
    return ""


def ensure_columns(engine: Engine) -> list[str]:
    insp = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                col_type = col.type.compile(engine.dialect)
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{_default_sql(col)}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added
