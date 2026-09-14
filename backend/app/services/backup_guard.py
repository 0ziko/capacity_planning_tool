"""PostgreSQL geri yukleme hedefi guvenlik kontrolleri (canli DB koruma)."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

# Canli / uygulama varsayilan veritabani adlari — restore hedefi olamaz.
BLOCKED_RESTORE_TARGETS = frozenset({"kapasite", "postgres", "template0", "template1"})

# Gecici audit/test DB: ad 'audit' veya 'test' icermeli (kapasite_audit_test vb.)
_RESTORE_TARGET_RE = re.compile(r"^[a-z0-9_]*(audit|test)[a-z0-9_]*$", re.I)


def database_name_from_url(database_url: str) -> str:
    u = database_url.strip()
    if u.startswith("postgresql+"):
        u = "postgresql://" + u.split("://", 1)[1]
    parsed = urlparse(u)
    name = (parsed.path or "").lstrip("/").split("?")[0]
    return unquote(name)


def validate_restore_target(target_db: str, *, source_db: str | None = None) -> None:
    """Gecici audit/test DB disinda hedefe izin verme; canli adla ayniysa reddet."""
    name = (target_db or "").strip().lower()
    if not name:
        raise ValueError("Hedef veritabani adi bos")
    if name in BLOCKED_RESTORE_TARGETS:
        raise ValueError(f"Canli veya sistem veritabani hedeflenemez: {target_db}")
    if not _RESTORE_TARGET_RE.search(name):
        raise ValueError(
            f"Hedef veritabani adi audit/test gecici DB olmali (orn. kapasite_audit_test): {target_db}"
        )
    if source_db and name == source_db.strip().lower():
        raise ValueError("Kaynak ve hedef veritabani adi ayni olamaz")
