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


def widen_revision_change_values(engine: Engine) -> list[str]:
    """job_move JSON icin old/new_value kolonlarini TEXT yapar."""
    insp = inspect(engine)
    actions: list[str] = []
    if not insp.has_table("plan_revision_changes"):
        return actions
    dialect = engine.dialect.name
    if dialect != "postgresql":
        return actions
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE plan_revision_changes ALTER COLUMN new_value TYPE TEXT"))
        conn.execute(text("ALTER TABLE plan_revision_changes ALTER COLUMN old_value TYPE TEXT"))
        actions.append("plan_revision_changes old/new_value -> TEXT")
    return actions


def repair_bom_constraints(engine: Engine) -> list[str]:
    """Eski uq_bom_item_component kisitini source_wip ile degistirir."""
    insp = inspect(engine)
    actions: list[str] = []
    if not insp.has_table("bom_lines"):
        return actions
    cols = {c["name"] for c in insp.get_columns("bom_lines")}
    with engine.begin() as conn:
        if "source_wip" not in cols:
            conn.execute(text("ALTER TABLE bom_lines ADD COLUMN source_wip VARCHAR(64) DEFAULT ''"))
            actions.append("bom_lines.source_wip added")
        dialect = engine.dialect.name
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE bom_lines DROP CONSTRAINT IF EXISTS uq_bom_item_component"))
            exists = conn.execute(text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'uq_bom_item_component_wip'"
            )).scalar()
            if not exists:
                conn.execute(text(
                    "ALTER TABLE bom_lines ADD CONSTRAINT uq_bom_item_component_wip "
                    "UNIQUE (item_id, component_code, source_wip)"
                ))
                actions.append("bom_lines constraint updated (pg)")
        elif dialect == "sqlite":
            # sqlite: recreate table if old unique exists without source_wip
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_bom_item_component_wip "
                    "ON bom_lines (item_id, component_code, source_wip)"
                ))
                actions.append("bom_lines index uq_bom_item_component_wip (sqlite)")
            except Exception:  # noqa: BLE001
                pass
    return actions


# (tablo, kolon, referans tablo) — silinmis ana kayda isaret eden yetim satirlar.
# SQLite'ta yabanci anahtar denetimi acilmadan once olusmus olabilirler; ekranlari bozar (500).
_ORPHAN_CHECKS = [
    ("routing_operations", "work_center_id", "work_centers"),
    ("routing_operations", "item_id", "items"),
    ("bom_lines", "item_id", "items"),
    ("work_center_shifts", "work_center_id", "work_centers"),
    ("plan_lines", "work_center_id", "work_centers"),
    ("plan_lines", "order_id", "orders"),
    ("plan_lines", "operation_id", "routing_operations"),
    ("production_actuals", "work_center_id", "work_centers"),
    ("downtimes", "work_center_id", "work_centers"),
    ("machines", "work_center_id", "work_centers"),
    ("work_center_weeks", "work_center_id", "work_centers"),
    ("op_transition_rules", "item_id", "items"),
    ("stock_receipts", "item_id", "items"),
    ("reservations", "item_id", "items"),
    ("reservations", "order_id", "orders"),
    ("shipments", "item_id", "items"),
    ("shipments", "order_id", "orders"),
]


def repair_orphans(engine: Engine) -> dict[str, int]:
    """Ana kaydi silinmis yetim satirlari temizler; tablo -> silinen satir sayisi dondurur."""
    insp = inspect(engine)
    removed: dict[str, int] = {}
    with engine.begin() as conn:
        for table, col, ref in _ORPHAN_CHECKS:
            if not insp.has_table(table) or not insp.has_table(ref):
                continue
            res = conn.execute(text(f"DELETE FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {ref})"))
            if res.rowcount:
                removed[f"{table}.{col}"] = removed.get(f"{table}.{col}", 0) + res.rowcount
        # calisani silinmis is merkezinden ayir
        if insp.has_table("employees"):
            res = conn.execute(text("UPDATE employees SET work_center_id = NULL WHERE work_center_id IS NOT NULL AND work_center_id NOT IN (SELECT id FROM work_centers)"))
            if res.rowcount:
                removed["employees.work_center_id"] = res.rowcount
            if insp.has_table("machines"):
                # silinmis makine ya da baska is merkezine tasinmis makine atamasini kaldir
                res = conn.execute(text(
                    "UPDATE employees SET machine_id = NULL WHERE machine_id IS NOT NULL AND machine_id NOT IN "
                    "(SELECT m.id FROM machines m WHERE m.work_center_id = employees.work_center_id)"
                ))
                if res.rowcount:
                    removed["employees.machine_id"] = res.rowcount
    return removed
