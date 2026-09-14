"""Plan girdisi yazimlarinda ufuk bazli islem kilidi (PostgreSQL advisory / SQLite test)."""

from __future__ import annotations

import zlib
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

_LOCK_NS = 751903


def _lock_key(horizon_key: str) -> int:
    return zlib.crc32(horizon_key.encode("utf-8")) & 0x7FFFFFFF


@contextmanager
def plan_input_write_lock(db: Session, horizon_key: str):
    """Onay / otomatik plan yazimi oncesi ayni ufuk icin seri erisim."""
    if not horizon_key:
        yield
        return
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    if dialect == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": _LOCK_NS, "k": _lock_key(horizon_key)},
        )
    # SQLite test ortami tek thread; advisory lock yerine islem siralamasi yeterli.
    yield
